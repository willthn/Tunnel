"""Métriques naïf vs agent (Data 2). Tout est calculé, rien n'est écrit en dur.

Les plans sont jugés sur la consommation RÉELLE du monde simulé (app.truth), pas sur ce que le
planificateur croyait : un plan naïf qui « voit » tout couvert à 15°C peut échouer par -5°C.
"""
import csv
import json
from pathlib import Path

from app import model, truth

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def mape_history() -> list[dict]:
    """Courbe d'erreur de prévision par nuit : [{night_date, mape (en %)}], ordre chronologique."""
    path = DATA_DIR / "model_runs.csv"
    if not path.exists():
        return []
    with open(path, encoding="utf-8") as f:
        rows = [{"night_date": r["night_date"], "mape": float(r["mape"])} for r in csv.DictReader(f)]
    return sorted(rows, key=lambda r: r["night_date"])


def _evaluate(plan: dict) -> dict:
    """Juge un plan (format section 9) sur la consommation réelle à la température prévue."""
    temp = plan["forecast_min_temp_c"]
    buses, services = model._buses(), model._services()
    charged = {}
    for c in plan.get("charging", []):
        charged[c["bus_id"]] = charged.get(c["bus_id"], 0.0) + c["kwh"]

    unassigned = list(plan.get("uncovered_services", []))
    failing = []
    for a in plan.get("assignments", []):
        b, s = buses[a["bus_id"]], services[a["service_id"]]
        usable = b["battery_kwh"] * b["soh"]
        soc = min(1.0, b["soc_evening"] + charged.get(a["bus_id"], 0.0) / usable)
        need = truth.true_kwh_per_km(b["base_kwh_per_km"], s["line"], temp) * s["distance_km"]
        have = truth.true_available_kwh(b["battery_kwh"], b["soh"], soc, temp)
        if need > have:
            failing.append({"service_id": a["service_id"], "bus_id": a["bus_id"], "missing_kwh": round(need - have, 1)})

    cost = plan.get("kpis", {}).get("charging_cost_eur")
    if cost is None:
        cost = sum(c.get("cost_eur", 0.0) for c in plan.get("charging", []))
    return {
        "strategy": plan.get("strategy"),
        "services_uncovered_planned": len(unassigned),  # ce que le plan annonce lui-même
        "services_uncovered_real": len(unassigned) + len(failing),  # panne sèche en ligne incluse
        "services_failing_en_route": failing,
        "charging_cost_eur": round(float(cost), 2),
        "replan_seconds": plan.get("kpis", {}).get("replan_seconds"),
    }


def compare_plans(naive: dict, agent: dict) -> dict:
    """KPIs de la section 11 : {naive, agent, delta}, à afficher dans l'écran de résultats."""
    n, a = _evaluate(naive), _evaluate(agent)
    return {
        "temp_c": agent["forecast_min_temp_c"],
        "naive": n,
        "agent": a,
        "delta": {
            "services_uncovered_real": n["services_uncovered_real"] - a["services_uncovered_real"],
            "charging_cost_eur": round(a["charging_cost_eur"] - n["charging_cost_eur"], 2),
        },
        "mape_history": mape_history(),
    }
