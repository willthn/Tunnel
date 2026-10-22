"""Accès aux données (A : Data 1).

DATA_MODE=fake : services lus dans data/services.csv (vrai GTFS Dijon), le reste
généré en dur. DATA_MODE=aiven : tout est lu et écrit dans Aiven PostgreSQL.
Les signatures ne changent pas d'un mode à l'autre.
"""
import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

from app.contract import (
    DATA_MODE,
    Bus,
    Charger,
    Depot,
    ConsumptionObs,
    ModelRun,
    PricePoint,
    Service,
    WeatherPoint,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Stockage en mémoire du mode fake (perdu au redémarrage, c'est voulu).
_fake_plans: list[dict] = []
_fake_obs: list[ConsumptionObs] = []
_fake_runs: list[ModelRun] = []


# --- API publique (signatures README section 9) ---------------------------------

def get_buses() -> list[Bus]:
    return _fake_buses() if DATA_MODE == "fake" else _aiven_select("buses")


def get_services() -> list[Service]:
    return _fake_services() if DATA_MODE == "fake" else _aiven_select("services")


def get_chargers() -> list[Charger]:
    return _fake_chargers() if DATA_MODE == "fake" else _aiven_select("chargers")


def get_depot() -> Depot:
    return _fake_depot() if DATA_MODE == "fake" else _aiven_select("depot")[0]


def get_forecast(night: date) -> list[WeatherPoint]:
    """Températures horaires de 18h la veille à 8h le jour de `night`."""
    return _fake_forecast(night) if DATA_MODE == "fake" else _aiven_range("weather_hourly", night)


def get_prices(night: date) -> list[PricePoint]:
    """Prix horaires de 18h la veille à 8h le jour de `night`."""
    return _fake_prices(night) if DATA_MODE == "fake" else _aiven_range("energy_price", night)


def save_plan(plan: dict, explanation: str) -> None:
    if DATA_MODE == "fake":
        _fake_plans.append({**plan, "explanation": explanation})
        return
    raise NotImplementedError("A : écrire dans la table plans")


# --- Ajouts proposés pour C (à valider) : sans eux, C devrait écrire dans data.py ---

def get_consumption_obs() -> list[ConsumptionObs]:
    return list(_fake_obs) if DATA_MODE == "fake" else _aiven_select("consumption_obs")


def save_consumption_obs(rows: list[ConsumptionObs]) -> None:
    if DATA_MODE == "fake":
        _fake_obs.extend(rows)
        return
    raise NotImplementedError("A : insérer dans consumption_obs")


def get_model_runs() -> list[ModelRun]:
    return list(_fake_runs) if DATA_MODE == "fake" else _aiven_select("model_runs")


def save_model_run(run: ModelRun) -> None:
    if DATA_MODE == "fake":
        _fake_runs.append(run)
        return
    raise NotImplementedError("A : insérer dans model_runs")


# --- Mode fake -------------------------------------------------------------------

def _read_csv(name: str) -> list[dict] | None:
    path = DATA_DIR / name
    if not path.exists():
        return None
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _fake_services() -> list[Service]:
    rows = _read_csv("services.csv") or []
    return [
        {
            "service_id": r["service_id"],
            "line": r["line"],
            "start_time": r["start_time"].replace(" ", "T"),
            "end_time": r["end_time"].replace(" ", "T"),
            "distance_km": float(r["distance_km"]),
        }
        for r in rows
    ]


def _fake_buses() -> list[Bus]:
    rows = _read_csv("buses.csv")  # produit par C, prioritaire s'il existe
    if rows:
        return [
            {
                "bus_id": r["bus_id"],
                "battery_kwh": float(r["battery_kwh"]),
                "soh": float(r["soh"]),
                "soc_evening": float(r["soc_evening"]),
                "base_kwh_per_km": float(r["base_kwh_per_km"]),
            }
            for r in rows
        ]
    rng = random.Random(42)
    return [
        {
            "bus_id": f"B{i:02d}",
            "battery_kwh": float(rng.choice([350, 400, 450])),
            "soh": round(rng.uniform(0.80, 1.00), 2),
            "soc_evening": round(rng.uniform(0.15, 0.45), 2),
            "base_kwh_per_km": round(rng.uniform(1.1, 1.4), 2),
        }
        for i in range(1, 31)
    ]


def _fake_chargers() -> list[Charger]:
    rows = _read_csv("chargers.csv")  # produit par C, prioritaire s'il existe
    if rows:
        return [
            {"charger_id": r["charger_id"], "power_kw": float(r["power_kw"]), "status": r["status"]}
            for r in rows
        ]
    return [
        {"charger_id": f"C{i:02d}", "power_kw": 150.0 if i <= 4 else 100.0, "status": "ok"}
        for i in range(1, 11)
    ]


def _fake_depot() -> Depot:
    path = DATA_DIR / "depot.json"  # produit par scripts/simulate_data.py (C)
    max_kw = json.loads(path.read_text())["max_power_kw"] if path.exists() else 806
    return {"max_power_kw": float(max_kw)}


def _night_hours(night: date) -> list[datetime]:
    start = datetime.combine(night - timedelta(days=1), datetime.min.time()) + timedelta(hours=18)
    return [start + timedelta(hours=h) for h in range(15)]  # 18h à 8h inclus


def _fake_forecast(night: date) -> list[WeatherPoint]:
    # Nuit froide plausible : 2°C à 18h, minimum vers 6h à -6°C.
    temps = [2, 1, 0, -1, -2, -2.5, -3, -3.5, -4, -4.5, -5, -5.5, -6, -5.5, -4]
    return [{"ts": ts.isoformat(), "temp_c": float(t)} for ts, t in zip(_night_hours(night), temps)]


def _fake_prices(night: date) -> list[PricePoint]:
    def tranche(h: int) -> tuple[str, float]:
        if h >= 22 or h < 6:
            return "creuse", 0.13
        if 18 <= h < 20:
            return "pointe", 0.28
        return "pleine", 0.20

    out: list[PricePoint] = []
    for ts in _night_hours(night):
        name, price = tranche(ts.hour)
        out.append({"ts": ts.isoformat(), "eur_kwh": price, "tranche": name})  # type: ignore[typeddict-item]
    return out


# --- Mode aiven (A : à implémenter) -------------------------------------------------

def _aiven_select(table: str) -> list:
    raise NotImplementedError(f"A : SELECT * FROM {table} via PG_URI")


def _aiven_range(table: str, night: date) -> list:
    raise NotImplementedError(f"A : SELECT {table} entre 18h la veille et 8h le {night}")
