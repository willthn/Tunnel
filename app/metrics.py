"""Métriques naïf vs agent (Data 2). Tout est calculé, rien n'est écrit en dur.

Les plans sont jugés sur la consommation RÉELLE du monde simulé (app.truth), pas sur ce que le
planificateur croyait : un plan naïf qui « voit » tout couvert à 15°C peut échouer par -5°C.
"""
from app import model, truth


def mape_history() -> list[dict]:
    """Courbe d'erreur de prévision par nuit : [{night_date, mape (en %)}], ordre chronologique.
    Lue dans la base (data.get_model_runs), un point par nuit."""
    model.ensure_trained()
    return [{"night_date": r["night_date"], "mape": float(r["mape"])} for r in model._latest_runs()]


def _evaluate(plan: dict, temp: float) -> dict:
    """Juge un plan (format section 9) sur la consommation réelle à la température `temp`."""
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


def _impact(naive: dict, agent: dict, n: dict, a: dict) -> dict:
    """Résultats mesurables pour le one-pager et la vidéo, calculés sur les plans (rien en dur).

    Aucun coût de service perdu n'est supposé : on donne le surcoût de recharge par service sauvé,
    donc « rentable dès qu'un service perdu coûte plus que extra_eur_per_saved_service »."""
    from app.planner import check_plan  # import local : planner dépend de model, pas de metrics

    def lost(ev: dict, plan: dict) -> set[str]:
        return set(plan.get("uncovered_services", [])) | {f["service_id"] for f in ev["services_failing_en_route"]}

    services = model._services()
    saved = lost(n, naive) - lost(a, agent)  # perdus avec le plan naïf, tenus avec le plan agent
    saved_n = len(saved)
    extra = round(a["charging_cost_eur"] - n["charging_cost_eur"], 1)
    check = check_plan(agent)
    return {
        "services_saved": n["services_uncovered_real"] - a["services_uncovered_real"],
        "km_saved": round(sum(services[sid]["distance_km"] for sid in saved if sid in services)),
        "extra_charging_eur": extra,
        "extra_eur_per_saved_service": round(extra / saved_n, 2) if saved_n else None,
        "replan_seconds": agent.get("kpis", {}).get("replan_seconds"),
        "peak_power_kw": check["peak_power_kw"],
        "max_power_kw": check["max_power_kw"],
        "reserve_buses_used": sum(1 for x in agent.get("assignments", []) if int(x["bus_id"][1:]) > 30),
    }


def compare_plans(naive: dict, agent: dict) -> dict:
    """KPIs de la section 11 : {naive, agent, delta}, à afficher dans l'écran de résultats."""
    # Les deux plans sont jugés dans les mêmes conditions : la température de la nuit vécue,
    # celle du plan agent (le plan naïf ne regarde pas la météo, la sienne n'est que la prévision).
    temp = agent["forecast_min_temp_c"]
    n, a = _evaluate(naive, temp), _evaluate(agent, temp)
    return {
        "temp_c": temp,
        "naive": n,
        "agent": a,
        "delta": {
            "services_uncovered_real": n["services_uncovered_real"] - a["services_uncovered_real"],
            "charging_cost_eur": round(a["charging_cost_eur"] - n["charging_cost_eur"], 2),
        },
        "impact": _impact(naive, agent, n, a),
        "mape_history": mape_history(),
    }
