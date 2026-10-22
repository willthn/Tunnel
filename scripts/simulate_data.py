"""Génère la flotte (40 bus : 30 titulaires + 10 de réserve), les chargeurs et la consommation observée (données simulées, section 7 du README).

Usage : uv run python scripts/simulate_data.py

Écrit dans data/ : buses.csv, chargers.csv, depot.json, sim_truth.json, consumption_obs.csv.
Reproductible (graine fixe). La consommation observée suit un modèle « vrai » caché + bruit :
le modèle appris (app/model.py) doit le retrouver nuit après nuit.
"""
import csv
import json
import random
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import data, truth  # noqa: E402

DATA = ROOT / "data"
rng = random.Random(42)

def night_min_temp(night: str) -> float:
    """Température minimale réelle de la nuit (météo de Dijon, data/weather.csv via app.data)."""
    return min(p["temp_c"] for p in data.get_forecast(date.fromisoformat(night)))


# Nuits d'apprentissage : deux vraies nuits froides et une nuit plus douce, météo réelle de Dijon
HISTORY_NIGHTS = {n: night_min_temp(n) for n in ("2026-01-01", "2026-01-05", "2026-01-09")}
# Nuit de démo : prévision réelle (douce) ; le bouton « vague de froid » la force à -5°C
DEMO_NIGHT = {"2026-01-14": night_min_temp("2026-01-14")}
NOISE = 0.04  # bruit relatif sur la consommation observée

services = list(csv.DictReader(open(DATA / "services.csv", encoding="utf-8")))
lines = sorted({s["line"] for s in services})

# --- Flotte : 40 bus 12 m ---
# B01 à B30 : titulaires (un par service) ; B31 à B40 : réserve (le planificateur le déduit de l'identifiant).
# Quelques titulaires fragiles (SoH bas, petite batterie) deviennent insuffisants par grand froid sur les
# services longs ; les réserves, en bon état, peuvent les remplacer.
WEAK_TITULAIRES = {3, 8, 12, 17, 21, 26}  # numéros de bus fragiles
buses = []
for i in range(1, 41):
    reserve = i > 30
    weak = i in WEAK_TITULAIRES
    buses.append({
        "bus_id": f"B{i:02d}",
        "battery_kwh": rng.choice([350, 380]) if weak else rng.choice([420, 450]) if reserve else rng.choice([350, 380, 400, 420, 450]),
        "soh": round(rng.uniform(0.80, 0.85) if weak else rng.uniform(0.95, 1.00) if reserve else rng.uniform(0.80, 1.00), 2),
        "soc_evening": round(rng.uniform(0.20, 0.55), 2),  # charge au retour au dépôt
        "base_kwh_per_km": round(rng.uniform(1.10, 1.40), 2),  # à 15°C
    })

# --- Chargeurs : 10 de 100 à 150 kW, puissance max du dépôt inférieure à la somme ---
chargers = [{"charger_id": f"C{i:02d}", "power_kw": rng.choice([100, 120, 150]), "status": "ok"} for i in range(1, 11)]
depot = {"max_power_kw": int(sum(c["power_kw"] for c in chargers) * 0.65)}

# --- Vérité cachée ---
truth_params = {
    "a_cold": 0.050,  # kWh/km en plus par °C sous 5°C (truth.T_COLD)
    "b_line": {ln: round(rng.uniform(-0.10, 0.15), 3) for ln in lines},
    "noise_pct": NOISE,
    "history_nights": HISTORY_NIGHTS,
    "demo_night": DEMO_NIGHT,
    "demo_override_c": -5.0,
}


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


write_csv(DATA / "buses.csv", buses)
write_csv(DATA / "chargers.csv", chargers)
(DATA / "depot.json").write_text(json.dumps(depot, indent=2) + "\n", encoding="utf-8")
(DATA / "sim_truth.json").write_text(json.dumps(truth_params, indent=2) + "\n", encoding="utf-8")
truth.load_truth.cache_clear()

# --- Consommation observée : chaque nuit, 30 des 40 bus sont affectés aux 30 services ---
obs = []
for night, temp in HISTORY_NIGHTS.items():
    order = list(range(len(buses)))
    rng.shuffle(order)  # seuls les 30 premiers roulent cette nuit
    for svc, bi in zip(services, order):
        bus = buses[bi]
        km = float(svc["distance_km"])
        kwh = truth.true_kwh_per_km(bus["base_kwh_per_km"], svc["line"], temp) * km * (1 + rng.gauss(0, NOISE))
        obs.append({
            "night_date": night, "bus_id": bus["bus_id"], "service_id": svc["service_id"], "line": svc["line"],
            "temp_c": temp, "km": km, "kwh": round(kwh, 1),
        })
write_csv(DATA / "consumption_obs.csv", obs)  # graine pour le mode fake et pour load_data.py
if data.DATA_MODE == "aiven":
    data.save_consumption_obs([{**o} for o in obs])  # écriture directe dans Aiven
print(f"{len(buses)} bus, {len(chargers)} chargeurs (dépôt max {depot['max_power_kw']} kW), {len(obs)} observations sur {len(HISTORY_NIGHTS)} nuits")
