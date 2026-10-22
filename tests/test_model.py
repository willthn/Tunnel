"""Tests du modèle et des métriques (Data 2). Lancer : uv run --with numpy --with pytest pytest tests/test_model.py"""
import json

import pytest

from app import data, metrics, model, truth


@pytest.fixture(autouse=True)
def replayed(monkeypatch):
    """Magasin en mémoire isolé : les tests n'écrivent jamais dans l'état partagé de app.data."""
    runs, obs = [], []
    monkeypatch.setattr(data, "get_model_runs", lambda: list(runs))
    monkeypatch.setattr(data, "save_model_run", runs.append)
    monkeypatch.setattr(data, "get_consumption_obs", lambda: list(obs))
    monkeypatch.setattr(data, "save_consumption_obs", obs.extend)
    model.reset_cache()
    out = [model.record_outcome(n) for n in truth.load_truth()["history_nights"]]
    yield out
    model.reset_cache()


def test_mape_decreases(replayed):
    mapes = [r["mape"] for r in replayed]
    assert mapes[0] > 2 * mapes[-1]
    assert mapes[-1] < 6.0


def test_learns_cold_coefficient(replayed):
    assert abs(replayed[-1]["coef"]["a"] - truth.load_truth()["a_cold"]) < 0.004


def test_prediction_grows_when_colder():
    assert model.predict_consumption("B01", "S01", -5) > model.predict_consumption("B01", "S01", 15)


def test_available_shrinks_when_colder():
    assert model.available_kwh("B01", -5) < model.available_kwh("B01", 15)
    assert model.available_kwh("B01", 15, soc_morning=0.5) == pytest.approx(0.5 * model.available_kwh("B01", 15))


def test_record_outcome_is_idempotent(replayed):
    nights = list(truth.load_truth()["history_nights"])
    again = model.record_outcome(nights[1])
    assert again["mape"] == replayed[1]["mape"]
    assert len(metrics.mape_history()) == 3  # un point par nuit malgré le doublon


def test_coefficients_stored_in_runs(replayed):
    assert model.load_state()["a"] == pytest.approx(replayed[-1]["coef"]["a"])
    assert "a" in data.get_model_runs()[-1]["coef_json"]


def test_naive_holds_in_mild_weather():
    """À +3°C un plan naïf (consommation sans froid, marge 15 %) ne doit pas s'effondrer."""
    svc = model._services()
    cold = model.predict_consumption("B20", "S01", 3.0) / model.predict_consumption("B20", "S01", 15.0)
    assert cold < 1.12


def test_unknown_night_raises():  # noqa: D103
    with pytest.raises(ValueError):
        model.record_outcome("2030-01-01")


def test_mape_history_chronological(replayed):
    h = metrics.mape_history()
    assert [r["night_date"] for r in h] == sorted(r["night_date"] for r in h)
    assert len(h) == 3


def _plan(strategy, bus_for, temp=-5.0, uncovered=()):
    return {
        "strategy": strategy, "forecast_min_temp_c": temp, "charging": [], "uncovered_services": list(uncovered),
        "assignments": [{"bus_id": b, "service_id": s} for s, b in bus_for.items()],
        "kpis": {"charging_cost_eur": 10.0, "replan_seconds": 1.0},
    }


def test_compare_plans_flags_dry_bus():
    # B02 (petite batterie, SoC faible) sur le service le plus long par -5°C : panne sèche réelle
    long_svc = max(model._services(), key=lambda s: model._services()[s]["distance_km"])
    weak = min(model._buses(), key=lambda b: model.available_kwh(b, -5, model._buses()[b]["soc_evening"]))
    strong = max(model._buses(), key=lambda b: model.available_kwh(b, -5, 1.0))
    bad = _plan("naive", {long_svc: weak})
    good = _plan("agent", {})
    good["charging"] = []
    r = metrics.compare_plans(bad, good)
    assert r["naive"]["services_uncovered_real"] == 1
    assert r["agent"]["services_uncovered_real"] == 0
    assert r["delta"]["services_uncovered_real"] == 1
    assert json.dumps(r)  # sérialisable pour l'API
