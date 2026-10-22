"""Tests du planificateur (B). Aucun réseau, aucune clé.

Deux niveaux :
- invariants sur les vraies données de data/ (aucun bus en double, plafond du dépôt respecté...) ;
- comportements ciblés (réserve, exclusion, check_plan) sur une petite flotte synthétique.
"""
from datetime import datetime, timedelta

import pytest

from app import contract, data, model, planner
from app.planner import check_plan, run_planner

NIGHT = contract.DEFAULT_NIGHT


def _plan(strategy="agent", **extra):
    return run_planner({"night_date": NIGHT, "strategy": strategy, **extra})


def _runs(plan):
    return [
        (datetime.fromisoformat(c["start"]), datetime.fromisoformat(c["end"]), c)
        for c in plan["charging"]
    ]


# --- invariants sur les données réelles -----------------------------------------

def test_every_service_is_assigned_or_uncovered_exactly_once():
    plan = _plan(temp_override_c=-5.0)
    assigned = [a["service_id"] for a in plan["assignments"]]
    all_ids = [s["service_id"] for s in data.get_services()]
    assert len(assigned) == len(set(assigned))
    assert sorted(assigned + plan["uncovered_services"]) == sorted(all_ids)


def test_a_bus_serves_at_most_one_service():
    buses = [a["bus_id"] for a in _plan(temp_override_c=-5.0)["assignments"]]
    assert len(buses) == len(set(buses))


def test_no_assignment_has_negative_margin():
    for a in _plan(temp_override_c=-5.0)["assignments"]:
        assert a["margin_pct"] >= 0
        assert a["available_kwh"] >= a["predicted_kwh"]


def test_depot_power_cap_is_respected_at_every_instant():
    plan = _plan(temp_override_c=-5.0)
    cap = data.get_depot()["max_power_kw"]
    runs = _runs(plan)
    for t, _, _ in runs:
        load = sum(
            c["kwh"] / ((e - s).total_seconds() / 3600)
            for s, e, c in runs
            if s <= t < e
        )
        assert load <= cap + 1e-6


def test_one_bus_per_charger_and_no_bus_on_two_chargers_at_once():
    runs = _runs(_plan(temp_override_c=-5.0))
    for i, (s1, e1, c1) in enumerate(runs):
        for s2, e2, c2 in runs[i + 1:]:
            if s1 < e2 and s2 < e1:
                assert c1["charger_id"] != c2["charger_id"]
                assert c1["bus_id"] != c2["bus_id"]


def test_charging_stays_in_window_and_never_overfills_a_battery():
    plan = _plan(temp_override_c=-5.0)
    first_slot = datetime.fromisoformat(NIGHT + "T00:00:00") - timedelta(hours=3)  # 21h la veille
    buses = {b["bus_id"]: b for b in data.get_buses()}
    services = {s["service_id"]: s for s in data.get_services()}
    by_bus = {a["bus_id"]: a for a in plan["assignments"]}
    totals: dict[str, float] = {}
    for s, e, c in _runs(plan):
        assert s >= first_slot
        start = datetime.fromisoformat(services[by_bus[c["bus_id"]]["service_id"]]["start_time"])
        assert e <= datetime.combine(datetime.fromisoformat(NIGHT).date(), start.time())
        totals[c["bus_id"]] = totals.get(c["bus_id"], 0.0) + c["kwh"]
    for bus_id, kwh in totals.items():
        b = buses[bus_id]
        assert kwh <= b["battery_kwh"] * b["soh"] * (1 - b["soc_evening"]) + 1e-6


def test_charging_cost_matches_kpi_and_prices():
    plan = _plan(temp_override_c=-5.0)
    assert plan["kpis"]["charging_cost_eur"] == pytest.approx(
        sum(c["cost_eur"] for c in plan["charging"]), abs=0.1
    )
    assert all(c["cost_eur"] > 0 for c in plan["charging"])


def test_warm_night_covers_everything_with_target_margin():
    plan = _plan(temp_override_c=15.0)
    assert plan["uncovered_services"] == []
    assert all(a["margin_pct"] >= 15 - 0.1 for a in plan["assignments"])


def test_naive_plans_with_15_degrees_agent_with_the_real_temperature():
    naive, agent = _plan("naive", temp_override_c=-5.0), _plan("agent", temp_override_c=-5.0)
    assert naive["forecast_min_temp_c"] == agent["forecast_min_temp_c"] == -5.0
    n = {a["service_id"]: a["predicted_kwh"] for a in naive["assignments"]}
    cold_need = {a["service_id"]: a["predicted_kwh"] for a in agent["assignments"]}
    common = set(n) & set(cold_need)
    assert common
    # À -5°C l'agent prévoit plus d'énergie que le plan naïf, pour le même service.
    assert sum(cold_need[s] for s in common) > sum(n[s] for s in common)


def test_plan_follows_the_contract_shape_and_ids():
    plan = _plan()
    assert plan["plan_id"] == f"{NIGHT}-agent"
    assert plan["strategy"] == "agent"
    assert set(plan["kpis"]) == {"services_uncovered", "charging_cost_eur", "replan_seconds"}
    assert plan["kpis"]["services_uncovered"] == len(plan["uncovered_services"])


# --- comportements ciblés sur une petite flotte synthétique ---------------------

@pytest.fixture
def small_fleet(monkeypatch):
    """1 service de 100 km. B01 est trop faible, B02 est le seul titulaire solide, B31 et B32 sont des réserves."""
    def bus(i, kwh, soh, soc):
        return {"bus_id": f"B{i:02d}", "battery_kwh": kwh, "soh": soh, "soc_evening": soc,
                "base_kwh_per_km": 1.2}

    buses = [bus(1, 220, 0.80, 0.3), bus(2, 450, 1.0, 0.3), bus(31, 450, 1.0, 0.3), bus(32, 450, 1.0, 0.3)]
    services = [
        {"service_id": "S01", "line": "L1", "start_time": f"{NIGHT}T05:00:00",
         "end_time": f"{NIGHT}T13:00:00", "distance_km": 100.0}
    ]
    monkeypatch.setattr(data, "get_buses", lambda: buses)
    monkeypatch.setattr(data, "get_services", lambda: services)
    monkeypatch.setattr(model, "predict_consumption", lambda b, s, t: 1.2 * 100 + 2.0 * max(0, 15 - t))
    monkeypatch.setattr(
        model, "available_kwh",
        lambda b, t, soc=1.0: next(x for x in buses if x["bus_id"] == b)["battery_kwh"]
        * next(x for x in buses if x["bus_id"] == b)["soh"] * soc,
    )
    return buses, services


def test_reserve_bus_is_used_only_when_no_titular_can_cover(small_fleet, monkeypatch):
    # B02 (titulaire) est exclu : la réserve doit prendre le service.
    plan = _plan(temp_override_c=-5.0, exclude_buses=["B01", "B02"])
    assert [a["bus_id"] for a in plan["assignments"]] == ["B31"]
    # Sans exclusion, un titulaire solide passe avant les réserves.
    plan = _plan(temp_override_c=-5.0)
    assert plan["assignments"][0]["bus_id"] == "B02"


def test_use_reserve_false_leaves_the_service_uncovered(small_fleet):
    plan = _plan(temp_override_c=-5.0, exclude_buses=["B01", "B02"], use_reserve=False)
    assert plan["assignments"] == []
    assert plan["uncovered_services"] == ["S01"]


def test_exclude_buses_is_respected(small_fleet):
    plan = _plan(temp_override_c=-5.0, exclude_buses=["B02"])
    assert all(a["bus_id"] != "B02" for a in plan["assignments"])


# --- check_plan ---------------------------------------------------------------

def _charge(bus, charger, start, end, kwh):
    return {"bus_id": bus, "charger_id": charger, "start": start, "end": end, "kwh": kwh, "cost_eur": 1.0}


def _bare_plan(**over):
    plan = {
        "plan_id": "x", "night_date": NIGHT, "strategy": "agent", "forecast_min_temp_c": -5.0,
        "assignments": [], "charging": [], "uncovered_services": [],
        "kpis": {"services_uncovered": 0, "charging_cost_eur": 0.0, "replan_seconds": 0.0},
        "explanation": "",
    }
    plan.update(over)
    return plan


def test_check_plan_ok_on_a_valid_plan():
    result = check_plan(_plan(temp_override_c=15.0))
    assert result["ok"] is True
    assert result["issues"] == []
    assert 0 < result["peak_power_kw"] <= result["max_power_kw"]


def test_check_plan_reports_uncovered_and_low_margin():
    plan = _bare_plan(
        uncovered_services=["S29"],
        assignments=[{"bus_id": "B07", "service_id": "S12", "predicted_kwh": 300.0,
                      "available_kwh": 310.0, "margin_pct": 3.2}],
    )
    result = check_plan(plan)
    assert result["ok"] is False
    assert result["uncovered_services"] == ["S29"]
    assert result["low_margin_buses"] == ["B07"]
    assert any("S29" in i for i in result["issues"])


def test_check_plan_detects_depot_overload_and_charger_clash():
    d = f"{NIGHT}T01:00:00"
    e = f"{NIGHT}T02:00:00"
    cap = data.get_depot()["max_power_kw"]
    plan = _bare_plan(charging=[
        _charge("B01", "C01", d, e, cap),
        _charge("B02", "C02", d, e, 50.0),
        _charge("B03", "C02", d, e, 10.0),
    ])
    result = check_plan(plan)
    assert result["ok"] is False
    assert result["peak_power_kw"] == pytest.approx(cap + 60.0)
    assert any("puissance" in i.lower() for i in result["issues"])
    assert any("C02" in i for i in result["issues"])


def test_module_exposes_the_agent_tools():
    assert callable(planner.run_planner) and callable(planner.check_plan)
