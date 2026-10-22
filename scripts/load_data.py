"""Crée les tables dans Aiven PostgreSQL et les remplit (A : Data 1).

Usage : uv run python scripts/load_data.py [--start 2026-01-01] [--end 2026-01-15] [--skip-weather]

Idempotent : relançable à volonté (CREATE IF NOT EXISTS + upserts).
Les CSV de data/ ne servent qu'à l'import initial du référentiel (services, flotte, chargeurs,
dépôt, consommation observée, runs du modèle). La météo, elle, est récupérée en direct chez les
4 fournisseurs (app/weather.py) : elle n'est jamais lue dans un CSV.
"""
import argparse
import csv
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from psycopg.types.json import Jsonb  # noqa: E402

from app import aiven, data  # noqa: E402
from app.db import close, connect  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"


def read_csv(name: str) -> list[dict]:
    path = DATA / name
    if not path.exists():
        print(f"  (absent : data/{name})")
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def upsert(table: str, columns: list[str], pk: list[str], rows: list[dict]) -> int:
    if not rows:
        return 0
    names = ", ".join(columns)
    values = ", ".join(f"%({c})s" for c in columns)
    updates = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in pk) or f"{pk[0]} = EXCLUDED.{pk[0]}"
    sql = f"INSERT INTO {table} ({names}) VALUES ({values}) ON CONFLICT ({', '.join(pk)}) DO UPDATE SET {updates}"
    with connect().cursor() as cur:
        cur.executemany(sql, rows)
    return len(rows)


def count(table: str) -> int:
    with connect().cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")
        return cur.fetchone()["n"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01", help="début de la fenêtre météo et prix")
    ap.add_argument("--end", default="2026-01-15")
    ap.add_argument("--reference-start", default="2025-01-01", help="hiver précédent (seule période où NOAA a des données)")
    ap.add_argument("--reference-end", default="2025-01-15")
    ap.add_argument("--skip-weather", action="store_true")
    args = ap.parse_args()
    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)

    print("1. Schéma")
    with connect().cursor() as cur:
        cur.execute((ROOT / "db" / "schema.sql").read_text(encoding="utf-8"))

    print("2. Référentiel (import initial depuis data/*.csv)")
    upsert("services", ["service_id", "line", "start_time", "end_time", "distance_km"], ["service_id"], read_csv("services.csv"))
    upsert("buses", ["bus_id", "battery_kwh", "soh", "soc_evening", "base_kwh_per_km"], ["bus_id"], read_csv("buses.csv"))
    upsert("chargers", ["charger_id", "power_kw", "status"], ["charger_id"], read_csv("chargers.csv"))
    depot_path = DATA / "depot.json"
    if depot_path.exists():
        upsert("depot", ["depot_id", "max_power_kw"], ["depot_id"],
               [{"depot_id": "main", "max_power_kw": json.loads(depot_path.read_text())["max_power_kw"]}])

    print("3. Mémoire du modèle (consommation observée, runs) : le jeu de départ remplace l'existant")
    obs = read_csv("consumption_obs.csv")
    if obs:  # sinon des nuits retirées du CSV resteraient en base, avec des runs appris dessus
        with connect().cursor() as cur:
            cur.execute("DELETE FROM consumption_obs")
            cur.execute("DELETE FROM model_runs")
        print("   runs effacés : relancer DATA_MODE=aiven uv run python scripts/replay_nights.py")
    upsert("consumption_obs", ["night_date", "bus_id", "service_id", "line", "temp_c", "km", "kwh"],
           ["night_date", "bus_id", "service_id"], obs)
    runs = [{**r, "coef_json": Jsonb(json.loads(r["coef_json"]))} for r in read_csv("model_runs.csv")]
    upsert("model_runs", ["run_id", "night_date", "mape", "coef_json"], ["run_id"], runs)

    print(f"4. Prix de l'électricité (3 tranches) du {start} au {end}")
    hours = [datetime.combine(start - timedelta(days=1), datetime.min.time()) + timedelta(hours=h)
             for h in range(((end - start).days + 2) * 24)]
    prices = [{**p, "ts": datetime.fromisoformat(p["ts"])} for p in map(data.hourly_price, hours)]
    upsert("energy_price", ["ts", "eur_kwh", "tranche"], ["ts"], prices)

    if not args.skip_weather:
        for label, a, b in (("fenêtre de démo", start, end),
                            ("hiver précédent (référence NOAA)", date.fromisoformat(args.reference_start), date.fromisoformat(args.reference_end))):
            print(f"5. Météo en direct, {label} : {a} -> {b}")
            for r in aiven.ingest_weather(a, b):
                status = f"ERREUR {r['error']}" if r["error"] else f"{r['rows']} lignes"
                print(f"   {r['provider']:<22} {status}")
        print("   Écart prévision vs mesures Météo-France (°C) :", aiven.forecast_error(start, end))

    print("Résumé")
    for t in ["services", "buses", "chargers", "depot", "consumption_obs", "model_runs",
              "energy_price", "weather_obs", "weather_hourly", "plans"]:
        print(f"   {t:<16} {count(t)}")
    close()


if __name__ == "__main__":
    main()
