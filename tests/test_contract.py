"""Test du contrat : chaque module renvoie la forme attendue par les autres.

À lancer avant chaque PR : uv run pytest
Si ce test passe avec ta vraie version, les deux autres ne sont pas cassés.
"""
from datetime import date
from typing import get_type_hints

from fastapi.testclient import TestClient

from app import contract, data, metrics, model
from app.agent import run_agent
from app.main import app
from app.planner import check_plan, run_planner

NIGHT = date.fromisoformat(contract.DEFAULT_NIGHT)


def assert_shape(obj: dict, typed: type) -> None:
    required = set(typed.__required_keys__)
    missing = required - obj.keys()
    assert not missing, f"{typed.__name__} : clés manquantes {missing}"
    for key, hint in get_type_hints(typed).items():
        if key in obj and hint in (float, int, str, bool):
            ok = isinstance(obj[key], (int, float)) if hint is float else isinstance(obj[key], hint)
            assert ok, f"{typed.__name__}.{key} : attendu {hint.__name__}, reçu {obj[key]!r}"


def test_data():
    for fn, typed in [
        (data.get_buses, contract.Bus),
        (data.get_services, contract.Service),
        (data.get_chargers, contract.Charger),
    ]:
        rows = fn()
        assert rows, f"{fn.__name__} renvoie une liste vide"
        for r in rows:
            assert_shape(r, typed)
    assert_shape(data.get_depot(), contract.Depot)
    for r in data.get_forecast(NIGHT):
        assert_shape(r, contract.WeatherPoint)
    for r in data.get_prices(NIGHT):
        assert_shape(r, contract.PricePoint)


def test_model():
    bus = data.get_buses()[0]["bus_id"]
    service = data.get_services()[0]["service_id"]
    warm = model.predict_consumption(bus, service, 15.0)
    cold = model.predict_consumption(bus, service, -5.0)
    assert 0 < warm <= cold, "il doit falloir plus d'énergie par grand froid"
    assert model.available_kwh(bus, -5.0) <= model.available_kwh(bus, 15.0)
    # record_outcome écrit dans data/ : couvert par tests/test_model.py (C).


def _assert_plan(plan: dict) -> None:
    assert_shape(plan, contract.Plan)
    assert_shape(plan["kpis"], contract.Kpis)
    for a in plan["assignments"]:
        assert_shape(a, contract.Assignment)
    for c in plan["charging"]:
        assert_shape(c, contract.Charging)


def test_planner_and_agent():
    for strategy in ("naive", "agent"):
        plan = run_planner({"night_date": contract.DEFAULT_NIGHT, "strategy": strategy})
        _assert_plan(plan)
        assert_shape(check_plan(plan), contract.PlanCheck)
    plan = run_agent({"night_date": contract.DEFAULT_NIGHT, "strategy": "agent", "temp_override_c": -5.0})
    _assert_plan(plan)
    assert plan["explanation"]


def test_metrics():
    naive = run_planner({"night_date": contract.DEFAULT_NIGHT, "strategy": "naive"})
    agent = run_planner({"night_date": contract.DEFAULT_NIGHT, "strategy": "agent"})
    comparison = metrics.compare_plans(naive, agent)
    assert_shape(comparison, contract.Comparison)
    for side in ("naive", "agent"):
        assert_shape(comparison[side], contract.PlanEvaluation)
    history = metrics.mape_history()
    assert history
    for p in history:
        assert_shape(p, contract.MapePoint)


def test_api():
    client = TestClient(app)
    assert client.get("/health").json()["ok"]
    _assert_plan(client.get("/plan", params={"strategy": "naive"}).json())
    body = client.post("/incident/cold-snap", json={"temp_c": -5.0}).json()
    assert {"naive", "agent", "comparison"} <= body.keys()
    assert client.get("/metrics/mape").json()
