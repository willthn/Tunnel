"""Modèle de consommation (C : Data 2). VERSION FACTICE, à remplacer par C.

Formules du README section 10 avec des coefficients fixes. La vraie version
apprend a, b_ligne et c par régression sur consumption_obs.
"""
from datetime import date

from app import data

A_COLD = 0.03       # kWh/km par degré sous 15°C
C_BATTERY = 0.01    # perte de capacité par degré sous 5°C
SOC_MORNING = 0.95  # charge supposée au départ, après recharge


def predict_consumption(bus_id: str, service_id: str, temp_c: float) -> float:
    """kWh nécessaires pour que `bus_id` assure `service_id` à `temp_c`."""
    bus = _by_id(data.get_buses(), "bus_id", bus_id)
    service = _by_id(data.get_services(), "service_id", service_id)
    kwh_per_km = bus["base_kwh_per_km"] + A_COLD * max(0.0, 15 - temp_c)
    return round(kwh_per_km * service["distance_km"], 1)


def available_kwh(bus_id: str, temp_c: float) -> float:
    """kWh utilisables au départ du dépôt, batterie rechargée."""
    bus = _by_id(data.get_buses(), "bus_id", bus_id)
    cold_loss = C_BATTERY * max(0.0, 5 - temp_c)
    return round(bus["battery_kwh"] * bus["soh"] * SOC_MORNING * (1 - cold_loss), 1)


def record_outcome(night: date) -> dict:
    """Ajoute la consommation observée de `night`, réentraîne, renvoie la nouvelle MAPE."""
    return {
        "night_date": night.isoformat(),
        "mape": 8.0,
        "coef_json": {"a": A_COLD, "c": C_BATTERY, "b_ligne": {}},
    }


def _by_id(rows: list, key: str, value: str) -> dict:
    for r in rows:
        if r[key] == value:
            return r
    raise KeyError(f"{key}={value} introuvable")
