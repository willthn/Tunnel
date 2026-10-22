"""Monde simulé : le « vrai » modèle de consommation, caché au modèle appris.

Sert uniquement à générer la consommation observée (scripts/simulate_data.py) et à
évaluer les plans en backtest (app/metrics.py). Jamais utilisé par l'agent ni par le planificateur.
"""
import json
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
TRUTH_PATH = DATA_DIR / "sim_truth.json"

# Perte de capacité utile par °C sous 5°C (chimie de la batterie, chauffage)
TRUE_C_COLD = 0.012


@lru_cache(maxsize=1)
def load_truth() -> dict:
    return json.loads(TRUTH_PATH.read_text(encoding="utf-8"))


def true_kwh_per_km(base_kwh_per_km: float, line: str, temp_c: float) -> float:
    t = load_truth()
    return base_kwh_per_km + t["a_cold"] * max(0.0, 15 - temp_c) + t["b_line"].get(line, 0.0)


def true_available_kwh(battery_kwh: float, soh: float, soc: float, temp_c: float) -> float:
    return battery_kwh * soh * soc * (1 - TRUE_C_COLD * max(0.0, 5 - temp_c))
