"""Photo des prix spot day-ahead France (Energy-Charts) vers data/energy_price.csv, pour DATA_MODE=fake.

Usage : uv run python scripts/fetch_prices.py
En DATA_MODE=aiven, les prix sont lus dans Aiven (et récupérés en direct s'ils manquent).
"""
import csv
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import prices  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "energy_price.csv"
rows = prices.fetch_day_ahead(date(2025, 12, 1), date(2026, 2, 28))
with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ts", "eur_kwh", "tranche", "source"])
    for r in rows:
        w.writerow([r["ts"].isoformat(), r["eur_kwh"], r["tranche"], r["source"]])
print(f"{OUT} : {len(rows)} heures")
