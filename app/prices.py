"""Prix de l'électricité : marché spot day-ahead France, source publique (A : Data 1).

Source : Energy-Charts (Fraunhofer ISE), https://api.energy-charts.info/price?bzn=FR
Données Bundesnetzagentur / SMARD, licence CC BY 4.0, sans clé.

Le marché est au pas de 15 min depuis octobre 2025 (1 h avant) : on fait la moyenne par heure.
Prix en €/kWh de gros, hors acheminement et taxes. La tranche est déduite du classement des
heures de chaque jour : les 8 moins chères sont « creuse », les 3 plus chères « pointe ».
"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

URL = "https://api.energy-charts.info/price"
SOURCE = "energy-charts-fr-day-ahead"
PARIS = ZoneInfo("Europe/Paris")
CHEAP_HOURS, PEAK_HOURS = 8, 3


def fetch_day_ahead(start: date, end: date) -> list[dict]:
    """Prix horaires du `start` au `end` inclus, heure locale : [{ts, eur_kwh, tranche, source}]."""
    params = {"bzn": "FR", "start": start.isoformat(), "end": (end + timedelta(days=1)).isoformat()}
    payload = httpx.get(URL, params=params, timeout=30).raise_for_status().json()
    by_hour: dict[datetime, list[float]] = defaultdict(list)
    for ts, eur_mwh in zip(payload["unix_seconds"], payload["price"]):
        if eur_mwh is None:
            continue
        local = datetime.fromtimestamp(ts, PARIS).replace(tzinfo=None)
        if start <= local.date() <= end:
            by_hour[local.replace(minute=0, second=0)].append(eur_mwh)
    rows = [
        {"ts": h, "eur_kwh": round(sum(v) / len(v) / 1000, 5), "source": SOURCE}
        for h, v in sorted(by_hour.items())
    ]
    return _with_tranches(rows)


def _with_tranches(rows: list[dict]) -> list[dict]:
    by_day: dict[date, list[dict]] = defaultdict(list)
    for r in rows:
        by_day[r["ts"].date()].append(r)
    for day_rows in by_day.values():
        ranked = sorted(day_rows, key=lambda r: r["eur_kwh"])
        for i, r in enumerate(ranked):
            r["tranche"] = "creuse" if i < CHEAP_HOURS else "pointe" if i >= len(ranked) - PEAK_HOURS else "pleine"
    return rows
