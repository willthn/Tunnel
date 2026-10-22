"""Modèle de consommation appris (Data 2).

kwh_per_km = base_bus + a * max(0, T_COLD - temp_c) + b_ligne
available  = battery * soh * soc * (1 - c * max(0, T_COLD - temp_c))

`a` et `b_ligne` sont appris par régression (moindres carrés, ridge léger sur les lignes) sur la
consommation observée. Aucun fichier d'état : tout passe par app.data (observations en lecture,
runs du modèle en écriture, coefficients dans coef_json). Le dernier run fait foi ; sans run, on
part d'un a priori volontairement imprécis.
"""
import csv
import time
from datetime import date
from pathlib import Path

import numpy as np

from app import data
from app.truth import T_COLD

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SEED_OBS = DATA_DIR / "consumption_obs.csv"  # graine du mode fake (la mémoire de data.py est vide au démarrage)

C_COLD = 0.010  # perte de capacité utile par °C sous le seuil (modèle ; la vérité est à 0,012)
PRIOR = {"a": 0.020, "b_line": {}}  # a priori avant toute observation
RIDGE = 0.05  # régularisation sur b_ligne (une ligne jamais vue reste à 0)

_coef_cache: dict | None = None
_ref_cache: dict = {}


# ---------- référentiels ----------
def _buses() -> dict[str, dict]:
    if "buses" not in _ref_cache:
        _ref_cache["buses"] = {b["bus_id"]: dict(b) for b in data.get_buses()}
    return _ref_cache["buses"]


def _services() -> dict[str, dict]:
    if "services" not in _ref_cache:
        _ref_cache["services"] = {s["service_id"]: dict(s) for s in data.get_services()}
    return _ref_cache["services"]


def reset_cache() -> None:
    """Oublie référentiels et coefficients (tests, ou changement de données)."""
    global _coef_cache
    _coef_cache = None
    _ref_cache.clear()


# ---------- observations et runs (via app.data) ----------
def _observations() -> list[dict]:
    obs = data.get_consumption_obs()
    if not obs and SEED_OBS.exists() and data.DATA_MODE == "fake":
        with open(SEED_OBS, encoding="utf-8") as f:  # mode fake : la mémoire de data.py démarre vide
            rows = [
                {**r, "temp_c": float(r["temp_c"]), "km": float(r["km"]), "kwh": float(r["kwh"])}
                for r in csv.DictReader(f)
            ]
        data.save_consumption_obs(rows)
        obs = data.get_consumption_obs()
    return [dict(o) for o in obs]


def _latest_runs() -> list[dict]:
    """Un run par nuit (le plus récent), dans l'ordre chronologique."""
    by_night: dict[str, dict] = {}
    for r in sorted(data.get_model_runs(), key=lambda r: (r["night_date"], r["run_id"])):
        by_night[r["night_date"]] = r
    return [by_night[k] for k in sorted(by_night)]


def ensure_trained() -> None:
    """Sans aucun run (démarrage du mode fake, base vide), rejoue les nuits observées dans l'ordre."""
    if _latest_runs():
        return
    for night in sorted({o["night_date"] for o in _observations()}):
        record_outcome(night)


# ---------- modèle ----------
def load_state() -> dict:
    """Coefficients courants : {a, b_line}, lus dans le dernier run (data.get_model_runs)."""
    global _coef_cache
    if _coef_cache is None:
        ensure_trained()
        runs = _latest_runs()
        _coef_cache = dict(runs[-1]["coef_json"]) if runs else {**PRIOR}
    return _coef_cache


def _predict_row(coef: dict, base: float, line: str, km: float, temp_c: float) -> float:
    return (base + coef["a"] * max(0.0, T_COLD - temp_c) + coef["b_line"].get(line, 0.0)) * km


def predict_consumption(bus_id: str, service_id: str, temp_c: float) -> float:
    """Énergie nécessaire (kWh) pour que ce bus assure ce service à cette température."""
    bus, svc = _buses()[bus_id], _services()[service_id]
    return _predict_row(load_state(), bus["base_kwh_per_km"], svc["line"], svc["distance_km"], temp_c)


def available_kwh(bus_id: str, temp_c: float, soc_morning: float = 1.0) -> float:
    """Énergie utilisable (kWh) au départ. `soc_morning` : charge au départ (1.0 = pleine)."""
    b = _buses()[bus_id]
    return b["battery_kwh"] * b["soh"] * soc_morning * (1 - C_COLD * max(0.0, T_COLD - temp_c))


def _fit(rows: list[dict]) -> dict:
    """Régression de (kWh/km - base_bus) sur [froid, ligne]."""
    if not rows:
        return {**PRIOR}
    lines = sorted({r["line"] for r in rows})
    X = np.zeros((len(rows), 1 + len(lines)))
    y = np.zeros(len(rows))
    buses = _buses()
    for i, r in enumerate(rows):
        X[i, 0] = max(0.0, T_COLD - r["temp_c"])
        X[i, 1 + lines.index(r["line"])] = 1.0
        y[i] = r["kwh"] / r["km"] - buses[r["bus_id"]]["base_kwh_per_km"]
    reg = np.diag([0.0] + [RIDGE] * len(lines))
    w = np.linalg.solve(X.T @ X + reg, X.T @ y)
    return {"a": float(w[0]), "b_line": {ln: float(w[1 + j]) for j, ln in enumerate(lines)}}


def _mape(coef: dict, rows: list[dict]) -> float:
    buses = _buses()
    errs = [
        abs(_predict_row(coef, buses[r["bus_id"]]["base_kwh_per_km"], r["line"], r["km"], r["temp_c"]) - r["kwh"]) / r["kwh"]
        for r in rows
    ]
    return 100.0 * float(np.mean(errs))


def record_outcome(night: date | str) -> dict:
    """Intègre la consommation observée d'une nuit : mesure l'erreur du modèle AVANT d'apprendre
    (MAPE honnête), puis réentraîne sur toutes les nuits jusqu'à celle-ci et enregistre un run
    (data.save_model_run, coefficients dans coef_json).

    Retourne {run_id, night_date, mape (en %), coef}. Rejouer une nuit donne les mêmes coefficients."""
    global _coef_cache
    night = str(night)
    obs = _observations()
    before = [r for r in obs if r["night_date"] < night]
    today = [r for r in obs if r["night_date"] == night]
    if not today:
        raise ValueError(f"aucune consommation observée pour la nuit {night}")
    mape = _mape(_fit(before), today)  # prédit avec ce qu'on savait la veille
    coef = _fit(before + today)
    run = {"run_id": f"run-{night}-{time.time_ns()}", "night_date": night, "mape": round(mape, 2), "coef_json": coef}
    data.save_model_run(run)
    _coef_cache = None
    return {"run_id": run["run_id"], "night_date": night, "mape": run["mape"], "coef": coef}
