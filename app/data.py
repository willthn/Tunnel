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

from app import aiven
from app.contract import (
    DATA_MODE,
    Bus,
    Charger,
    Depot,
    ConsumptionObs,
    ModelRun,
    PricePoint,
    Plan,
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
    return _fake_buses() if DATA_MODE == "fake" else aiven.select("buses")


def get_services() -> list[Service]:
    return _fake_services() if DATA_MODE == "fake" else aiven.select("services")


def get_chargers() -> list[Charger]:
    return _fake_chargers() if DATA_MODE == "fake" else aiven.select("chargers")


def get_depot() -> Depot:
    return _fake_depot() if DATA_MODE == "fake" else aiven.depot()


def get_forecast(night: date) -> list[WeatherPoint]:
    """Températures horaires de 18h la veille à 8h le jour de `night`."""
    return _fake_forecast(night) if DATA_MODE == "fake" else aiven.forecast(night)


def refresh_forecast(night: date) -> dict:
    """Va chercher la météo fraîche chez les fournisseurs et renvoie ce qu'on en retient.

    {"min_temp_c": float, "sources": [{"provider", "rows", "error"}], "fetched_at": str}.
    Mode fake : aucun appel réseau, lit data/weather.csv."""
    if DATA_MODE == "fake":
        report = [{"provider": "data/weather.csv", "rows": len(_fake_forecast(night)), "error": None}]
    else:
        report = aiven.ingest_weather(night - timedelta(days=1), night)
    return {
        "min_temp_c": min(p["temp_c"] for p in get_forecast(night)),
        "sources": report,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
    }


def get_prices(night: date) -> list[PricePoint]:
    """Prix horaires de 18h la veille à 8h le jour de `night`."""
    return _fake_prices(night) if DATA_MODE == "fake" else aiven.window("energy_price", night)


def save_plan(plan: dict, explanation: str) -> None:
    if DATA_MODE == "fake":
        _fake_plans.append({**plan, "explanation": explanation})
        return
    aiven.save_plan(plan, explanation)


def get_plan(night_date: str, strategy: str) -> Plan | None:
    """Dernier plan sauvegardé pour cette nuit et cette stratégie, avec son explanation."""
    if DATA_MODE == "fake":
        for plan in reversed(_fake_plans):
            if plan["night_date"] == night_date and plan["strategy"] == strategy:
                return plan  # type: ignore[return-value]
        return None
    return aiven.get_plan(night_date, strategy)  # type: ignore[return-value]


# --- Ajouts proposés pour C (à valider) : sans eux, C devrait écrire dans data.py ---

def get_consumption_obs() -> list[ConsumptionObs]:
    return list(_fake_obs) if DATA_MODE == "fake" else aiven.select("consumption_obs")


def save_consumption_obs(rows: list[ConsumptionObs]) -> None:
    if DATA_MODE == "fake":
        _fake_obs.extend(rows)
        return
    aiven.save_consumption_obs(rows)  # type: ignore[arg-type]


def get_model_runs() -> list[ModelRun]:
    return list(_fake_runs) if DATA_MODE == "fake" else aiven.select("model_runs")


def save_model_run(run: ModelRun) -> None:
    if DATA_MODE == "fake":
        _fake_runs.append(run)
        return
    aiven.save_model_run(run)  # type: ignore[arg-type]


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
    """Météo réelle de Dijon (data/weather.csv) si elle couvre la nuit, sinon nuit froide factice."""
    hours = {ts.isoformat() for ts in _night_hours(night)}
    real = [
        {"ts": r["ts"], "temp_c": float(r["temp_c"])}
        for r in _read_csv("weather.csv") or []
        if r["ts"] in hours
    ]
    if len(real) == len(hours):
        return real
    # Repli : 2°C à 18h, minimum vers 6h à -6°C.
    temps = [2, 1, 0, -1, -2, -2.5, -3, -3.5, -4, -4.5, -5, -5.5, -6, -5.5, -4]
    return [{"ts": ts.isoformat(), "temp_c": float(t)} for ts, t in zip(_night_hours(night), temps)]


def hourly_price(ts: datetime) -> PricePoint:
    """Tarif en 3 tranches : creuses 22h-6h, pointe 18h-20h, pleines le reste du temps."""
    h = ts.hour
    if h >= 22 or h < 6:
        tranche, price = "creuse", 0.13
    elif 18 <= h < 20:
        tranche, price = "pointe", 0.28
    else:
        tranche, price = "pleine", 0.20
    return {"ts": ts.isoformat(), "eur_kwh": price, "tranche": tranche}  # type: ignore[typeddict-item]


def _fake_prices(night: date) -> list[PricePoint]:
    return [hourly_price(ts) for ts in _night_hours(night)]
