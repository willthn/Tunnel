"""Boucle agent Claude (B : Agent).

Le LLM ne calcule jamais un plan : il pilote des outils (météo, réévaluation du plan en cours,
planificateur, vérification), décide s'il faut durcir les contraintes, puis explique en français.
Tous les chiffres viennent du code (`planner`), le LLM ne les met qu'en mots.

Si l'API échoue, est trop lente, refuse, ou si le LLM ne produit aucun plan, on bascule sur un
repli déterministe qui joue la même séquence sans LLM (`fallback: true` dans la réponse).
`AGENT_LLM=off` force ce repli (répétitions hors ligne, tests).
"""
import copy
import json
import os
import time

import anthropic

from app import data, planner
from app.contract import Constraints, Plan

MODEL = "claude-sonnet-5-5"
MAX_TOOL_ROUNDS = 8          # tours d'outils maximum avant de passer au repli
DEADLINE_S = 30.0            # budget de temps pour toute la boucle LLM
LLM_TIMEOUT_S = 15.0         # délai maximum d'un appel API
MARGIN_BOUNDS = (15.0, 30.0)
EVENT_DROP_C = 3.0           # baisse de la prévision (°C) au-delà de laquelle on parle de vague de froid
REFRESH_TTL_S = float(os.getenv("REFRESH_TTL_S", "600"))  # météo réutilisée 10 min par nuit
HARDEN_STEPS = [             # repli déterministe : du plus léger au plus prudent
    {"advance_charging": True},
    {"advance_charging": True, "margin_pct": 20.0},
    {"advance_charging": True, "margin_pct": 25.0},
]

SYSTEM = """Tu es l'agent de planification nocturne d'un dépôt de bus électriques.
Chaque nuit tu décides quel bus part sur quel service et quand le recharger. Tu ne calcules rien
toi-même : tu utilises les outils, qui renvoient des faits chiffrés.

Appelle ensemble, dans le même tour, les outils indépendants (get_forecast, assess_current_plan et un
premier run_planner par défaut peuvent partir en parallèle) : chaque tour coûte plusieurs secondes.

La météo a déjà été rafraîchie auprès des fournisseurs (Open-Meteo, Météo-France, ECMWF, GFS) et
l'événement détecté par le code : le contexte du premier message donne la prévision consolidée, la
température de l'incident s'il y en a un, et le plan déjà enregistré.

Méthode :
1. (déjà fait) Lis le contexte. get_forecast reste disponible pour relire la prévision consolidée.
2. assess_current_plan : mesure combien de services du plan actuel ne tiennent plus à cette température.
3. run_planner : produis un plan. Garde la marge par défaut (15 %) pour un premier essai.
4. check_plan : vérifie-le. S'il n'est pas ok, relance run_planner avec des contraintes plus prudentes
   (advance_charging=true, margin_pct entre 15 et 30, use_reserve=true), au plus 2 relances.
5. finalize_plan : retiens le meilleur plan (le moins de services non couverts, puis le moins cher).
6. Réponds enfin par l'explication, rien d'autre.

Explication : en français, un seul paragraphe de 5 à 8 phrases, sans liste ni titre, pour un responsable
de dépôt. Dis ce qui a changé (froid détecté, services concernés, bus qui changent de service, bus de
réserve activés, recharge avancée), puis le résultat (services sauvés ou restant non couverts, surcoût de
recharge). N'utilise que les chiffres renvoyés par les outils, n'en invente aucun. Si des services restent
non couverts, dis-le franchement. Cite au plus 2 ou 3 exemples de changements de bus au lieu de tous les
énumérer, et ne mentionne jamais les identifiants internes des plans (p1, p2...)."""

TOOLS = [
    {
        "name": "get_forecast",
        "description": "Température minimale prévue pour la nuit et température de l'incident de froid éventuel.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "assess_current_plan",
        "description": "Réévalue le plan actuel (fait pour la prévision normale) à la température de la nuit : "
                       "renvoie le nombre de services qui ne tiennent plus.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "run_planner",
        "description": "Calcule un plan complet (affectation et recharge) et renvoie son résumé et un identifiant "
                       "(p1, p2...). Sans argument : réglages par défaut.",
        "input_schema": {
            "type": "object",
            "properties": {
                "margin_pct": {"type": "number", "description": "Marge de sécurité en %, entre 15 et 30."},
                "advance_charging": {"type": "boolean", "description": "Commencer la recharge plus tôt (19h au lieu de 21h)."},
                "use_reserve": {"type": "boolean", "description": "Autoriser les bus de réserve B31 à B40."},
                "exclude_buses": {"type": "array", "items": {"type": "string"}, "description": "Bus à ne pas utiliser."},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "check_plan",
        "description": "Vérifie un plan : couverture, marges, puissance du dépôt, conflits de chargeurs.",
        "input_schema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string"}},
            "required": ["plan_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "finalize_plan",
        "description": "Retient un plan comme plan final de la nuit et renvoie ce qui change par rapport au plan "
                       "actuel (bus qui changent de service, réserves activées, recharges avancées, surcoût).",
        "input_schema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string"}},
            "required": ["plan_id"],
            "additionalProperties": False,
        },
    },
]


# Dernière météo rafraîchie par nuit : {night_date: (instant, résultat)}. Évite d'interroger les
# fournisseurs à chaque GET /plan (plusieurs secondes en mode aiven).
_refresh_cache: dict[str, tuple[float, dict]] = {}
_now = time.monotonic


class _ToolError(Exception):
    """Erreur d'outil renvoyée au LLM (is_error), qui peut se corriger."""


class _AgentError(Exception):
    """La boucle LLM n'a pas abouti : on bascule sur le repli."""


class _Session:
    """État d'un passage de l'agent : plans produits, trace, évaluation du plan en cours."""

    def __init__(self, constraints: Constraints):
        self.night = constraints["night_date"]
        self.override = constraints.get("temp_override_c")
        self.t0 = time.perf_counter()
        self.trace: list[dict] = []
        self.plans: dict[str, tuple[Plan, float]] = {}  # handle -> (plan, marge utilisée)
        self.final_id: str | None = None
        self.assessment: dict | None = None
        self._baseline: Plan | None = None
        self._forecast_temp: float | None = None
        self.refreshed: dict | None = None
        self.detection: dict | None = None
        self.saved: Plan | None = None

    # --- données ---
    def forecast_temp(self) -> float:
        if self._forecast_temp is None:
            from datetime import date
            self._forecast_temp = min(p["temp_c"] for p in data.get_forecast(date.fromisoformat(self.night)))
        return self._forecast_temp

    def baseline(self) -> Plan:
        """Le plan en cours de la nuit : celui déjà enregistré, sinon un plan frais sur la prévision."""
        if self._baseline is None:
            self._baseline = self.saved or planner.run_planner({"night_date": self.night, "strategy": "agent"})
        return self._baseline

    def elapsed(self) -> float:
        return time.perf_counter() - self.t0

    # --- appel d'outil (trace incluse) ---
    def call(self, name: str, args: dict) -> dict:
        t = time.perf_counter()
        try:
            result, summary = getattr(self, f"_tool_{name}")(args or {})
        except _ToolError as exc:
            self._log(name, f"Erreur : {exc}", t)
            raise
        self._log(name, summary, t)
        return result

    def _log(self, tool: str, summary: str, t: float) -> None:
        self.trace.append({"tool": tool, "summary": summary, "seconds": round(time.perf_counter() - t, 3)})

    # --- outils ---
    def _tool_refresh_forecast(self, args):
        """Premier outil de la boucle : interroge les fournisseurs météo (data.refresh_forecast).

        Résultat réutilisé pendant REFRESH_TTL_S pour la même nuit, sauf si `fresh` est vrai.
        """
        from datetime import date
        cached = _refresh_cache.get(self.night)
        age = _now() - cached[0] if cached else None
        if cached and not args.get("fresh") and age < REFRESH_TTL_S:
            res = copy.deepcopy(cached[1])
            summary = f"Prévision du cache (il y a {age:.0f} s) : {res['min_temp_c']:g}°C"
        else:
            night = date.fromisoformat(self.night)
            fetch = getattr(data, "refresh_forecast", None)
            if fetch is None:  # data.py d'avant refresh_forecast : on relit la prévision en base
                res = {"min_temp_c": min(p["temp_c"] for p in data.get_forecast(night)), "sources": [], "fetched_at": ""}
            else:
                res = fetch(night)
            _refresh_cache[self.night] = (_now(), copy.deepcopy(res))
            summary = f"Prévision rafraîchie : {res['min_temp_c']:g}°C"
        self._forecast_temp = None  # get_forecast renvoie désormais la valeur consolidée
        self.refreshed = res
        return res, f"{summary} ({len(res.get('sources', []))} source(s) météo)"

    def _tool_detect_event(self, _args):
        """Compare la prévision au plan déjà enregistré : baisse de plus de 3°C = vague de froid."""
        try:
            saved = data.get_plan(self.night, "agent")
        except NotImplementedError:
            saved = None
        self.saved = copy.deepcopy(saved) if saved else None
        new_temp = self.override if self.override is not None else self.refreshed["min_temp_c"]
        if self.override is not None:
            reason = f"incident simulé à {new_temp:g}°C"
        elif self.saved is None:
            reason = "aucun plan enregistré pour cette nuit"
        else:
            drop = self.saved["forecast_min_temp_c"] - new_temp
            reason = (f"prévision en baisse de {drop:.1f}°C ({self.saved['forecast_min_temp_c']:g}°C → {new_temp:g}°C)"
                      if drop > EVENT_DROP_C else
                      f"écart de {drop:+.1f}°C avec le plan enregistré, sous le seuil de {EVENT_DROP_C:g}°C")
        needs_plan = (self.override is not None or self.saved is None
                      or self.saved["forecast_min_temp_c"] - new_temp > EVENT_DROP_C)
        self.detection = {"event": needs_plan, "reason": reason, "new_temp_c": new_temp,
                          "saved_plan_temp_c": self.saved["forecast_min_temp_c"] if self.saved else None}
        return self.detection, ("Événement détecté : " if needs_plan else "Pas d'événement : ") + reason

    def _tool_get_forecast(self, _args):
        f = self.forecast_temp()
        result = {"night_date": self.night, "forecast_min_temp_c": f, "incident_temp_c": self.override}
        summary = f"Prévision de la nuit : {f:g}°C"
        if self.override is not None:
            summary += f", incident de froid annoncé à {self.override:g}°C"
        return result, summary

    def _tool_assess_current_plan(self, _args):
        base = self.baseline()
        new_temp = self.override if self.override is not None else self.forecast_temp()
        res = planner.reassess_plan(base, new_temp)
        self.assessment = {"baseline_temp_c": base["forecast_min_temp_c"], "new_temp_c": new_temp,
                           "infeasible": res["infeasible"]}
        n = len(res["infeasible"])
        result = {"baseline_min_temp_c": base["forecast_min_temp_c"], "new_temp_c": new_temp,
                  "services_infeasible": n, "examples": res["infeasible"][:5]}
        return result, f"À {new_temp:g}°C, {n} service(s) du plan actuel ne tiennent plus"

    def _tool_run_planner(self, args):
        settings = self._clean(args)
        constraints = {"night_date": self.night, "strategy": "agent", **settings}
        if self.override is not None:
            constraints["temp_override_c"] = self.override
        plan = planner.run_planner(constraints)
        handle = f"p{len(self.plans) + 1}"
        margin = settings.get("margin_pct", planner.DEFAULT_MARGIN_PCT)
        self.plans[handle] = (plan, margin)
        k = plan["kpis"]
        result = {"plan_id": handle, "settings": settings,
                  "services_uncovered": k["services_uncovered"], "uncovered_services": plan["uncovered_services"],
                  "charging_cost_eur": k["charging_cost_eur"], "replan_seconds": k["replan_seconds"]}
        mode = "avancée" if settings.get("advance_charging") else "normale"
        return result, (f"Plan {handle} (marge {margin:g} %, recharge {mode}) : "
                        f"{k['services_uncovered']} service(s) non couvert(s), {k['charging_cost_eur']:g} € de recharge")

    def _tool_check_plan(self, args):
        plan, margin = self._plan(args)
        chk = planner.check_plan(plan, margin_pct=margin)
        result = {"plan_id": args["plan_id"], "ok": chk["ok"], "issues": chk["issues"][:6],
                  "low_margin_buses": chk["low_margin_buses"][:6],
                  "peak_power_kw": chk["peak_power_kw"], "max_power_kw": chk["max_power_kw"]}
        verdict = "ok" if chk["ok"] else f"{len(chk['issues'])} problème(s)"
        return result, f"Vérification de {args['plan_id']} : {verdict}, pic {chk['peak_power_kw']:g} kW sur {chk['max_power_kw']:g}"

    def _tool_finalize_plan(self, args):
        plan, _ = self._plan(args)
        self.final_id = args["plan_id"]
        diff = planner.diff_plans(self.baseline(), plan)
        saved = self.services_saved(plan)
        short = {k: (v[:8] if isinstance(v, list) else v) for k, v in diff.items()}
        result = {"plan_id": self.final_id, "kpis": plan["kpis"], "services_saved": saved, "diff": short}
        return result, (f"Plan {self.final_id} retenu : {saved} service(s) sauvé(s), "
                        f"{len(plan['uncovered_services'])} non couvert(s), {diff['cost_delta_eur']:+g} € de recharge")

    # --- utilitaires ---
    def _plan(self, args) -> tuple[Plan, float]:
        handle = args.get("plan_id")
        if handle not in self.plans:
            raise _ToolError(f"plan_id inconnu : {handle!r} (disponibles : {', '.join(self.plans) or 'aucun'})")
        return self.plans[handle]

    def _clean(self, args: dict) -> dict:
        """Borne les arguments du LLM : le code reste maître des contraintes."""
        out: dict = {}
        if "margin_pct" in args:
            try:
                out["margin_pct"] = min(max(float(args["margin_pct"]), MARGIN_BOUNDS[0]), MARGIN_BOUNDS[1])
            except (TypeError, ValueError):
                raise _ToolError("margin_pct doit être un nombre") from None
        for key in ("advance_charging", "use_reserve"):
            if key in args:
                out[key] = bool(args[key])
        if "exclude_buses" in args:
            known = {b["bus_id"] for b in data.get_buses()}
            out["exclude_buses"] = [b for b in args["exclude_buses"] if b in known]
        return out

    def services_saved(self, plan: Plan) -> int:
        if not self.assessment:
            return 0
        covered = {a["service_id"] for a in plan["assignments"]}
        return sum(1 for i in self.assessment["infeasible"] if i["service_id"] in covered)

    def best_handle(self) -> str:
        def key(h):
            plan, margin = self.plans[h]
            chk = planner.check_plan(plan, margin_pct=margin)
            return (len(plan["uncovered_services"]), not chk["ok"], plan["kpis"]["charging_cost_eur"], h)
        return min(self.plans, key=key)


# --- boucle LLM -----------------------------------------------------------------

def llm_enabled() -> bool:
    """Vrai si l'agent appellera Claude (clé présente et AGENT_LLM différent de « off »)."""
    return os.getenv("AGENT_LLM", "on").lower() != "off" and bool(
        os.getenv("ANTHROPIC_API_KEY") or os.getenv("ANTHROPIC_AUTH_TOKEN")
    )


def _make_client():
    if not llm_enabled():
        return None  # pas de clé dans .env ou AGENT_LLM=off : repli direct, sans erreur dans la trace
    headers = {}
    if os.getenv("ANTHROPIC_WORKSPACE_ID"):  # clé non rattachée à un workspace : l'API exige l'en-tête
        headers["anthropic-workspace-id"] = os.environ["ANTHROPIC_WORKSPACE_ID"]
    return anthropic.Anthropic(timeout=LLM_TIMEOUT_S, max_retries=1, default_headers=headers or None)


def _run_llm(session: _Session, client) -> str:
    """Boucle d'appel d'outils. Renvoie le texte d'explication du LLM ('' s'il n'en a pas donné)."""
    task = {"night_date": session.night, "incident_temp_c": session.override,
            "forecast_refreshed": session.refreshed, "event_detection": session.detection}
    messages = [{"role": "user", "content": "Planifie la nuit. Contexte : " + json.dumps(task)}]
    rounds = 0
    while True:
        if session.elapsed() > DEADLINE_S:
            raise TimeoutError(f"budget de {DEADLINE_S:g}s dépassé")
        resp = client.messages.create(
            model=MODEL, max_tokens=2000, system=SYSTEM, tools=TOOLS, messages=messages,
            output_config={"effort": "low"},
        )
        if resp.stop_reason in ("refusal", "max_tokens"):
            raise _AgentError(f"arrêt du modèle : {resp.stop_reason}")
        calls = [b for b in resp.content if b.type == "tool_use"]
        if resp.stop_reason == "tool_use" and calls:
            rounds += 1
            if rounds > MAX_TOOL_ROUNDS:
                raise _AgentError(f"plus de {MAX_TOOL_ROUNDS} tours d'outils")
            results = []
            for c in calls:  # tous les résultats dans un seul message utilisateur
                try:
                    out = session.call(c.name, dict(c.input or {}))
                    results.append({"type": "tool_result", "tool_use_id": c.id, "content": json.dumps(out)})
                except _ToolError as exc:
                    results.append({"type": "tool_result", "tool_use_id": c.id, "content": str(exc), "is_error": True})
                except AttributeError:
                    results.append({"type": "tool_result", "tool_use_id": c.id,
                                    "content": f"outil inconnu : {c.name}", "is_error": True})
            messages.append({"role": "assistant", "content": resp.content})
            messages.append({"role": "user", "content": results})
            continue
        if not session.plans:
            raise _AgentError("le modèle n'a produit aucun plan")
        if session.final_id is None:
            session.call("finalize_plan", {"plan_id": session.best_handle()})
        return " ".join(b.text for b in resp.content if b.type == "text").strip()


# --- repli déterministe ---------------------------------------------------------

def _run_fallback(session: _Session) -> None:
    """Même séquence que le LLM, pilotée par le code : prévoir, mesurer, planifier, durcir, retenir."""
    session.call("assess_current_plan", {})
    for step in [{}] + HARDEN_STEPS:
        handle = session.call("run_planner", step)["plan_id"]
        if session.call("check_plan", {"plan_id": handle})["ok"]:
            break
    session.call("finalize_plan", {"plan_id": session.best_handle()})


def _template_explanation(session: _Session, plan: Plan) -> str:
    """Explication sans LLM, construite uniquement à partir des faits calculés."""
    diff = planner.diff_plans(session.baseline(), plan)
    parts = []
    a = session.assessment
    if a and session.override is not None:
        parts.append(
            f"La température minimale passe de {a['baseline_temp_c']:g}°C à {a['new_temp_c']:g}°C : "
            f"{len(a['infeasible'])} service(s) du plan actuel ne tiennent plus."
        )
    if diff["reassigned"]:
        r = diff["reassigned"][0]
        parts.append(f"{len(diff['reassigned'])} service(s) changent de bus (par exemple {r['service_id']} : "
                     f"{r['from_bus']} remplacé par {r['to_bus']}).")
    if diff["reserve_activated"]:
        parts.append("Bus de réserve activés : " + ", ".join(diff["reserve_activated"]) + ".")
    if diff["charging_advanced"]:
        parts.append(f"La recharge est avancée pour {len(diff['charging_advanced'])} bus.")
    saved = session.services_saved(plan)
    if saved:
        parts.append(f"{saved} service(s) menacés par le froid sont sauvés.")
    unc = plan["uncovered_services"]
    parts.append("Tous les services sont couverts." if not unc else
                 f"{len(unc)} service(s) restent non couverts ({', '.join(unc)}) : la recharge ou la flotte ne suffit pas.")
    parts.append(f"Coût de recharge : {plan['kpis']['charging_cost_eur']:g} € ({diff['cost_delta_eur']:+g} € par rapport au plan normal).")
    return " ".join(parts)


# --- point d'entrée -------------------------------------------------------------

def _start(constraints: Constraints, fresh: bool = False) -> _Session:
    """Étapes communes : rafraîchir la météo (premier outil), puis détecter l'événement."""
    session = _Session(constraints)
    session.call("refresh_forecast", {"fresh": fresh})
    session.call("detect_event", {})
    return session


def run_agent(constraints: Constraints, client=None, *, use_llm: bool = True, force: bool = False) -> Plan:
    """Produit, vérifie, explique et enregistre le plan de la nuit.

    Sans événement (pas d'incident, prévision stable) et avec un plan déjà enregistré, renvoie ce plan
    tel quel. `force` ignore ce plan et le cache météo, `use_llm=False` impose le repli
    (réinitialisation rapide).
    """
    if constraints["strategy"] != "agent":
        return planner.run_planner(constraints)

    session = _start(constraints, fresh=force)
    if session.saved is not None and not session.detection["event"] and not force:
        plan = session.saved
        plan["agent_trace"] = session.trace
        plan.setdefault("fallback", False)
        return plan

    client = (client if client is not None else _make_client()) if use_llm else None
    explanation: str | None = None
    if client is not None:
        try:
            explanation = _run_llm(session, client)
        except Exception as exc:  # panne API, délai, refus, boucle ratée : la démo continue
            reason = f"{type(exc).__name__}: {exc}"[:160]
            session = _start(constraints)
            session.trace.append({"tool": "fallback", "summary": f"Repli sans LLM ({reason})", "seconds": 0.0})
    used_fallback = explanation is None
    if used_fallback:
        _run_fallback(session)

    plan = copy.deepcopy(session.plans[session.final_id][0])
    plan["explanation"] = explanation or _template_explanation(session, plan)
    plan["agent_trace"] = session.trace
    plan["fallback"] = used_fallback
    plan["kpis"]["replan_seconds"] = round(session.elapsed(), 2)
    data.save_plan(plan, plan["explanation"])
    return plan
