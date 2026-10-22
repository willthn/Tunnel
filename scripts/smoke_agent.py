"""Test de fumée de la démo, de bout en bout : à lancer avant de monter sur scène.

    uv run python scripts/smoke_agent.py                       # en local (aucun serveur à lancer)
    uv run python scripts/smoke_agent.py --url https://...     # contre le backend déployé
    uv run python scripts/smoke_agent.py --require-llm         # échoue si l'agent est tombé en repli
    uv run python scripts/smoke_agent.py --no-llm              # répétition hors ligne

Rejoue le scénario du pitch : reset, nuit normale, vague de froid à -5°C, comparaison naïf contre
agent, courbe de MAPE. Code de sortie 1 si une vérification échoue. Un vrai appel à Claude coûte
quelques centimes et prend 15 à 20 s.
"""
import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

failures: list[str] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    print(f"  {'OK ' if ok else 'ÉCHEC'} {label}" + (f"  ({detail})" if detail else ""))
    if not ok:
        failures.append(label)


def make_client(url: str | None):
    if url:
        import httpx

        return httpx.Client(base_url=url.rstrip("/"), timeout=90.0)
    from fastapi.testclient import TestClient

    from app.main import app

    return TestClient(app)


def timed(fn, *a, **kw):
    t = time.perf_counter()
    r = fn(*a, **kw)
    return r, time.perf_counter() - t


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", help="URL du backend déployé (sinon test en local)")
    ap.add_argument("--temp", type=float, default=-5.0, help="température de la vague de froid (-5 par défaut)")
    ap.add_argument("--require-llm", action="store_true", help="échoue si l'agent a utilisé le repli sans LLM")
    ap.add_argument("--no-llm", action="store_true", help="force le repli sans LLM (local uniquement)")
    args = ap.parse_args()
    if args.no_llm:
        os.environ["AGENT_LLM"] = "off"

    c = make_client(args.url)
    print(f"Cible : {args.url or 'en local (TestClient)'}")

    print("\n1. /health")
    r, _ = timed(c.get, "/health")
    h = r.json()
    check(r.status_code == 200 and h.get("ok"), "serveur en ligne", f"données : {h.get('data_mode')}")
    check(bool(h.get("llm")) or not args.require_llm, "agent branché sur Claude", f"modèle : {h.get('model')}, llm={h.get('llm')}")

    print("\n2. Nuit normale (POST /reset)")
    r, dt = timed(c.post, "/reset")
    normal = r.json()
    check(r.status_code == 200, "reset", f"{dt:.1f}s")
    check(normal["forecast_min_temp_c"] > 0, "nuit douce", f"{normal['forecast_min_temp_c']:g}°C")
    check(normal["uncovered_services"] == [], "tous les services couverts", f"coût {normal['kpis']['charging_cost_eur']:g} €")

    print("\n3. Plans enregistrés (GET /plan)")
    r, dt = timed(c.get, "/plan", params={"strategy": "agent"})
    saved = r.json()
    tools = [s["tool"] for s in saved.get("agent_trace", [])]
    check(tools == ["refresh_forecast", "detect_event"], "plan agent renvoyé sans replanifier", f"{dt:.1f}s")
    naive = c.get("/plan", params={"strategy": "naive"}).json()
    check(naive["strategy"] == "naive" and "agent_trace" not in naive, "plan naïf sans trace")

    print(f"\n4. Vague de froid {args.temp:g}°C (POST /incident/cold-snap)")
    r, dt = timed(c.post, "/incident/cold-snap", json={"temp_c": args.temp})
    check(r.status_code == 200, "réponse", f"{dt:.1f}s")
    body = r.json()
    agent, cmp_ = body["agent"], body["comparison"]
    tools = [s["tool"] for s in agent["agent_trace"]]
    check("Événement détecté" in agent["agent_trace"][1]["summary"], "événement détecté", agent["agent_trace"][1]["summary"])
    check("assess_current_plan" in tools and "run_planner" in tools and tools[-1] == "finalize_plan", "boucle complète", " > ".join(tools))
    check(bool(agent["explanation"].strip()), "explication rédigée", f"{len(agent['explanation'])} caractères")
    check(not (args.require_llm and agent["fallback"]), "pas de repli sans LLM", f"fallback={agent['fallback']}")
    nu, au = cmp_["naive"]["services_uncovered_real"], cmp_["agent"]["services_uncovered_real"]
    check(au < nu, "l'agent bat le plan naïf (services en échec réel)", f"naïf {nu}, agent {au}")
    check(au == 0, "aucun service en échec réel avec l'agent", f"{au}")
    check(agent["kpis"]["replan_seconds"] < 60, "replanification sous 60 s", f"{agent['kpis']['replan_seconds']:g}s")

    print("\n5. Courbe d'apprentissage (GET /metrics/mape)")
    mape = c.get("/metrics/mape").json()
    check(len(mape) >= 3, "3 nuits d'historique", f"{len(mape)} points")
    if len(mape) >= 2:
        check(mape[-1]["mape"] < mape[0]["mape"], "l'erreur de prévision baisse", " → ".join(f"{p['mape']:.1f} %" for p in mape))

    print("\nRésumé")
    print(f"  naïf : {nu} service(s) en échec  |  agent : {au}  |  coût agent : {agent['kpis']['charging_cost_eur']:g} €")
    print(f"  agent : {'Claude' if not agent['fallback'] else 'repli sans LLM'}, {dt:.1f}s")
    print("\n" + ("TOUT EST OK" if not failures else f"{len(failures)} VÉRIFICATION(S) EN ÉCHEC : " + " ; ".join(failures)))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
