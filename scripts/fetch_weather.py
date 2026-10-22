# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx"]
# ///
"""Télécharge la météo horaire réelle de Dijon (Open-Meteo, archive) vers data/weather.csv.

Usage : uv run scripts/fetch_weather.py

Hiver 2025-2026, heure locale. Le CSV est commité : c'est le repli si l'API bloque, et la
source de app/data.py en mode fake et de scripts/load_data.py (table weather_hourly).
"""
import csv
from pathlib import Path

import httpx

URL = "https://archive-api.open-meteo.com/v1/archive"
PARAMS = {
    "latitude": 47.32,
    "longitude": 5.04,
    "start_date": "2025-12-01",
    "end_date": "2026-02-28",
    "hourly": "temperature_2m",
    "timezone": "Europe/Paris",
}
OUT = Path(__file__).resolve().parent.parent / "data" / "weather.csv"

hourly = httpx.get(URL, params=PARAMS, timeout=30).raise_for_status().json()["hourly"]
with OUT.open("w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["ts", "temp_c"])
    for ts, t in zip(hourly["time"], hourly["temperature_2m"]):
        if t is not None:
            w.writerow([ts + ":00", t])
print(f"{OUT} : {len(hourly['time'])} heures")
