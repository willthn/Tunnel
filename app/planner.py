"""Planificateur (B : Agent). VERSION FACTICE, à remplacer par B.

Associe bêtement le bus i au service i et recharge tout le monde à 23h.
Le format de sortie est exactement le Plan du contrat : le front peut s'y brancher.
"""
import time
from datetime import date, datetime, timedelta

from app import data, model
from app.contract import Constraints, Plan, PlanCheck


def run_planner(constraints: Constraints) -> Plan:
    t0 = time.perf_counter()
    night = date.fromisoformat(constraints["night_date"])
    strategy = constraints["strategy"]
    forecast = data.get_forecast(night)
    min_temp = constraints.get("temp_override_c", min(p["temp_c"] for p in forecast))
    temp = 15.0 if strategy == "naive" else min_temp
    margin = constraints.get("margin_pct", 15.0)

    assignments, uncovered = [], []
    for bus, service in zip(data.get_buses(), data.get_services()):
        need = model.predict_consumption(bus["bus_id"], service["service_id"], temp)
        avail = model.available_kwh(bus["bus_id"], temp)
        margin_pct = round((avail - need) / avail * 100, 1)
        if margin_pct < margin:
            uncovered.append(service["service_id"])
            continue
        assignments.append({
            "bus_id": bus["bus_id"],
            "service_id": service["service_id"],
            "predicted_kwh": need,
            "available_kwh": avail,
            "margin_pct": margin_pct,
        })

    chargers = data.get_chargers()
    start = datetime.combine(night - timedelta(days=1), datetime.min.time()) + timedelta(hours=23)
    charging = [
        {
            "bus_id": a["bus_id"],
            "charger_id": chargers[i % len(chargers)]["charger_id"],
            "start": start.isoformat(),
            "end": (start + timedelta(hours=4)).isoformat(),
            "kwh": 200.0,
            "cost_eur": 26.0,
        }
        for i, a in enumerate(assignments)
    ]

    return {
        "plan_id": f"{night.isoformat()}-{strategy}",
        "night_date": night.isoformat(),
        "strategy": strategy,
        "forecast_min_temp_c": min_temp,
        "assignments": assignments,
        "charging": charging,
        "uncovered_services": uncovered,
        "kpis": {
            "services_uncovered": len(uncovered),
            "charging_cost_eur": round(sum(c["cost_eur"] for c in charging), 1),
            "replan_seconds": round(time.perf_counter() - t0, 2),
        },
        "explanation": "",
    }


def check_plan(plan: Plan) -> PlanCheck:
    return {
        "ok": not plan["uncovered_services"],
        "uncovered_services": plan["uncovered_services"],
        "low_margin_buses": [a["bus_id"] for a in plan["assignments"] if a["margin_pct"] < 15],
        "peak_power_kw": 0.0,
        "max_power_kw": data.get_depot()["max_power_kw"],
        "issues": [f"Service {s} non couvert" for s in plan["uncovered_services"]],
    }
