"""Modèle de consommation appris (Data 2).

kwh_per_km = base_bus + a * max(0, 15 - temp_c) + b_ligne
available  = battery * soh * soc * (1 - c * max(0, 5 - temp_c))

`a` et `b_ligne` sont appris par régression (moindres carrés, ridge léger sur les lignes) sur la
consommation observée. Avant la première nuit, on part d'un a priori volontairement imprécis.
Les référentiels (bus, services) viennent de app.data (Data 1) si présent, sinon des CSV de data/.
"""
import csv
import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import numpy as np

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
STATE_PATH = DATA_DIR / "model_state.json"
RUNS_PATH = DATA_DIR / "model_runs.csv"
OBS_PATH = DATA_DIR / "consumption_obs.csv"

C_COLD = 0.010  # perte de capacité utile par °C sous 5°C (modèle ; la vérité est à 0,012)
PRIOR = {"a": 0.010, "b_line": {}}  # a priori avant toute observation
RIDGE = 0.05  # régularisation sur b_ligne (une ligne jamais vue reste à 0)


# ---------- référentiels ----------
def _read_csv(name: str) -> list[dict]:
    with open(DATA_DIR / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def _buses() -> dict[str, dict]:
    try:
        from app import data  # Data 1

        rows = data.get_buses()
    except ImportError:
        rows = _read_csv("buses.csv")
    return {r["bus_id"]: {k: (float(v) if k != "bus_id" else v) for k, v in r.items()} for r in rows}


@lru_cache(maxsize=1)
def _services() -> dict[str, dict]:
    try:
        from app import data  # Data 1

        rows = data.get_services()
    except ImportError:
        rows = _read_csv("services.csv")
    return {r["service_id"]: {"line": r["line"], "distance_km": float(r["distance_km"])} for r in rows}


# ---------- modèle ----------
def load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    return {**PRIOR, "n_obs": 0, "nights": []}


def _predict_row(coef: dict, base: float, line: str, km: float, temp_c: float) -> float:
    return (base + coef["a"] * max(0.0, 15 - temp_c) + coef["b_line"].get(line, 0.0)) * km


def predict_consumption(bus_id: str, service_id: str, temp_c: float) -> float:
    """Énergie nécessaire (kWh) pour que ce bus assure ce service à cette température."""
    bus, svc = _buses()[bus_id], _services()[service_id]
    return _predict_row(load_state(), bus["base_kwh_per_km"], svc["line"], svc["distance_km"], temp_c)


def available_kwh(bus_id: str, temp_c: float, soc_morning: float = 1.0) -> float:
    """Énergie utilisable (kWh) au départ. `soc_morning` : charge au départ (1.0 = pleine)."""
    b = _buses()[bus_id]
    return b["battery_kwh"] * b["soh"] * soc_morning * (1 - C_COLD * max(0.0, 5 - temp_c))


def _observations() -> list[dict]:
    rows = _read_csv("consumption_obs.csv")
    for r in rows:
        for k in ("temp_c", "km", "kwh"):
            r[k] = float(r[k])
    return rows


def _fit(rows: list[dict]) -> dict:
    """Régression de (kWh/km - base_bus) sur [froid, ligne]."""
    if not rows:
        return {**PRIOR}
    lines = sorted({r["line"] for r in rows})
    X = np.zeros((len(rows), 1 + len(lines)))
    y = np.zeros(len(rows))
    buses = _buses()
    for i, r in enumerate(rows):
        X[i, 0] = max(0.0, 15 - r["temp_c"])
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
    (MAPE honnête), puis réentraîne sur toutes les nuits jusqu'à celle-ci et sauvegarde.

    Retourne {run_id, night_date, mape (en %), coef}. Idempotent : rejouer une nuit donne le même résultat."""
    night = str(night)
    obs = _observations()
    before = [r for r in obs if r["night_date"] < night]
    today = [r for r in obs if r["night_date"] == night]
    if not today:
        raise ValueError(f"aucune consommation observée pour la nuit {night}")
    mape = _mape(_fit(before), today)  # prédit avec ce qu'on savait la veille
    coef = _fit(before + today)
    nights = sorted({r["night_date"] for r in before} | {night})
    STATE_PATH.write_text(
        json.dumps({**coef, "n_obs": len(before) + len(today), "nights": nights}, indent=2) + "\n", encoding="utf-8"
    )
    run = {"run_id": f"run-{night}", "night_date": night, "mape": round(mape, 2), "coef": coef}
    _upsert_run(run)
    return run


def _upsert_run(run: dict) -> None:
    runs = {r["night_date"]: r for r in _read_csv("model_runs.csv")} if RUNS_PATH.exists() else {}
    runs[run["night_date"]] = {
        "run_id": run["run_id"], "night_date": run["night_date"], "mape": run["mape"],
        "coef_json": json.dumps(run["coef"]),
    }
    with open(RUNS_PATH, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["run_id", "night_date", "mape", "coef_json"])
        w.writeheader()
        w.writerows(runs[k] for k in sorted(runs))


def reset() -> None:
    """Repart de l'a priori (efface l'état et l'historique des runs)."""
    STATE_PATH.unlink(missing_ok=True)
    RUNS_PATH.unlink(missing_ok=True)
