"""Tests des routes FastAPI (B). Repli sans LLM (AGENT_LLM=off), aucun réseau."""
import pytest
from fastapi.testclient import TestClient

from app import contract
from app.main import app

NIGHT = contract.DEFAULT_NIGHT


@pytest.fixture
def client():
    return TestClient(app)


def test_naive_and_agent_plans(client):
    naive = client.get("/plan", params={"strategy": "naive"}).json()
    agent = client.get("/plan", params={"strategy": "agent"}).json()
    assert naive["strategy"] == "naive" and "agent_trace" not in naive
    assert agent["strategy"] == "agent" and agent["agent_trace"][0]["tool"] == "refresh_forecast"


def test_unknown_strategy_is_rejected(client):
    assert client.get("/plan", params={"strategy": "magic"}).status_code == 422


def test_second_agent_plan_is_the_saved_one_without_replanning(client):
    first = client.get("/plan").json()
    second = client.get("/plan").json()
    assert [s["tool"] for s in second["agent_trace"]] == ["refresh_forecast", "detect_event"]
    assert second["assignments"] == first["assignments"]


def test_cold_snap_returns_both_plans_judged_at_the_same_temperature(client):
    body = client.post("/incident/cold-snap", json={"temp_c": -5.0}).json()
    assert {"naive", "agent", "comparison"} <= body.keys()
    assert body["naive"]["forecast_min_temp_c"] == body["agent"]["forecast_min_temp_c"] == -5.0
    assert body["comparison"]["temp_c"] == -5.0
    assert body["agent"]["agent_trace"] and "fallback" in body["agent"]
    tools = [s["tool"] for s in body["agent"]["agent_trace"]]
    assert tools[:2] == ["refresh_forecast", "detect_event"] and "assess_current_plan" in tools


def test_agent_beats_naive_in_the_cold_on_real_consumption(client):
    c = client.post("/incident/cold-snap", json={"temp_c": -5.0}).json()["comparison"]
    assert c["agent"]["services_uncovered_real"] < c["naive"]["services_uncovered_real"]


def test_reset_restores_the_normal_night_so_the_next_incident_is_detected(client):
    client.post("/incident/cold-snap", json={"temp_c": -5.0})
    normal = client.post("/reset").json()
    assert normal["fallback"] is True and normal["forecast_min_temp_c"] > 0
    again = client.post("/incident/cold-snap", json={"temp_c": -5.0}).json()
    assert "Événement détecté" in again["agent"]["agent_trace"][1]["summary"]
    assert client.get("/plan").json()["forecast_min_temp_c"] == -5.0  # l'incident est le plan en cours


def test_metrics_mape(client):
    history = client.get("/metrics/mape").json()
    assert history and {"night_date", "mape"} <= history[0].keys()


def test_health_reports_data_mode_and_llm(client):
    h = client.get("/health").json()
    assert h["ok"] is True and h["data_mode"] == contract.DATA_MODE
    assert h["llm"] is False and h["model"] == "claude-sonnet-5-5"  # AGENT_LLM=off sous pytest


@pytest.mark.parametrize("origin", [
    "https://wattshift.lovable.app",
    "https://abc-123.lovableproject.com",
    "https://preview.lovable.dev",
    "https://tunnel-wattshift.vercel.app",
    "https://tunnel-git-main-achille.vercel.app",
    "http://localhost:5173",
])
def test_cors_allows_lovable_and_localhost(client, origin):
    r = client.get("/health", headers={"Origin": origin})
    assert r.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("origin", [
    "https://evil.example.com",
    "https://vercel.app.evil.com",
    "http://tunnel.vercel.app",  # http, pas https
])
def test_cors_rejects_other_origins(client, origin):
    r = client.get("/health", headers={"Origin": origin})
    assert "access-control-allow-origin" not in r.headers


def test_unhandled_error_is_logged_and_returned_without_secrets(monkeypatch, caplog):
    from app import data

    def boom():
        raise ValueError("connexion postgres://user:motdepasse@host:5432/db refusée")

    monkeypatch.setattr(data, "get_buses", boom)
    r = TestClient(app, raise_server_exceptions=False).get("/plan", params={"strategy": "naive"})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert detail.startswith("ValueError") and "motdepasse" not in detail and "<masqué>" in detail
    assert any("Erreur non gérée" in rec.getMessage() for rec in caplog.records)
