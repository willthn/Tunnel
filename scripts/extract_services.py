# /// script
# requires-python = ">=3.11"
# dependencies = ["pandas"]
# ///
"""Extrait 30 services de bus du GTFS Divia (Dijon) vers data/services.csv.

Usage : uv run scripts/extract_services.py <dossier_gtfs> [service_id_jour]

Un « service » = une vacation d'un bus sur une ligne : allers-retours enchaînés
sur une demi-journée (matin ou après-midi). La distance vient des tracés
(shapes.txt, Haversine) car le GTFS n'a pas de shape_dist_traveled.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

GTFS = Path(sys.argv[1])
DAY_SERVICE = sys.argv[2] if len(sys.argv) > 2 else "13_1"  # jour de semaine
NIGHT_DATE = "2026-01-14"  # nuit planifiée (date des exemples du README)
N_SERVICES = 30
SKIP_ROUTES = {"4-93", "4-98", "4-99", "4-PB", "4-NAVC", "4-FNI"}  # non commerciaux
SHIFTS = {"matin": (4 * 60, 13 * 60), "apres-midi": (13 * 60, 22 * 60)}
MIN_KM = 60  # une vacation trop courte n'a pas d'intérêt pour le planning


def minutes(hms: str) -> int:
    h, m, _ = hms.split(":")
    return int(h) * 60 + int(m)


def hhmm(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def haversine(lat, lon):
    lat, lon = np.radians(lat), np.radians(lon)
    a = np.sin(np.diff(lat) / 2) ** 2 + np.cos(lat[:-1]) * np.cos(lat[1:]) * np.sin(np.diff(lon) / 2) ** 2
    return float(6371.0 * 2 * np.arcsin(np.sqrt(a)).sum())


routes = pd.read_csv(GTFS / "routes.txt", dtype=str)
bus = routes[(routes.route_type == "3") & ~routes.route_id.isin(SKIP_ROUTES)]

trips = pd.read_csv(GTFS / "trips.txt", dtype=str)
trips = trips[(trips.service_id == DAY_SERVICE) & trips.route_id.isin(bus.route_id)]

shapes = pd.read_csv(GTFS / "shapes.txt").sort_values(["shape_id", "shape_pt_sequence"])
shape_km = {sid: haversine(g.shape_pt_lat.values, g.shape_pt_lon.values) for sid, g in shapes.groupby("shape_id")}
trips["km"] = trips.shape_id.map(shape_km)
trips = trips.dropna(subset=["km"])

st = pd.read_csv(GTFS / "stop_times.txt", dtype=str, usecols=["trip_id", "arrival_time", "departure_time"])
st = st[st.trip_id.isin(trips.trip_id)]
bounds = st.groupby("trip_id").agg(start=("departure_time", "first"), end=("arrival_time", "last"))
trips = trips.join(bounds, on="trip_id")
trips["start_min"] = trips.start.map(minutes)
trips["end_min"] = trips.end.map(minutes)

# Les trips sont triés par ordre d'apparition ; on les trie par départ.
rows = []
for route_id, g in trips.groupby("route_id"):
    g = g.sort_values("start_min")
    line = bus.set_index("route_id").loc[route_id, "route_short_name"]
    for shift, (lo, hi) in SHIFTS.items():
        t = g[(g.start_min >= lo) & (g.start_min < hi)]
        # Un seul bus enchaîne les trips : on prend le suivant dès que le bus est libre.
        free_at, picked = -1, []
        for r in t.itertuples():
            if r.start_min >= free_at:
                picked.append(r)
                free_at = r.end_min + 5  # 5 min de battement au terminus
        if not picked:
            continue
        km = sum(r.km for r in picked)
        if km < MIN_KM:
            continue
        rows.append({
            "line": line, "shift": shift, "start_min": picked[0].start_min, "end_min": picked[-1].end_min,
            "distance_km": round(km, 1), "n_trips": len(picked),
        })

cand = pd.DataFrame(rows)
print(f"{len(cand)} vacations candidates sur {cand.line.nunique()} lignes")

# 30 vacations : les plus longues, au plus 2 par ligne (matin + après-midi).
cand = cand.sort_values("distance_km", ascending=False).head(N_SERVICES).sort_values(["start_min", "line"]).reset_index(drop=True)
cand["service_id"] = [f"S{i + 1:02d}" for i in range(len(cand))]
cand["start_time"] = cand.start_min.map(lambda m: f"{NIGHT_DATE} {hhmm(m)}:00" if m < 24 * 60 else None)
cand["end_time"] = cand.end_min.map(lambda m: f"{NIGHT_DATE} {hhmm(m)}:00" if m < 24 * 60 else None)
out = cand[["service_id", "line", "start_time", "end_time", "distance_km"]]

Path("data").mkdir(exist_ok=True)
out.to_csv("data/services.csv", index=False)
print(out.to_string(index=False))
print(f"\nDistance : min {out.distance_km.min()} / médiane {out.distance_km.median()} / max {out.distance_km.max()} km")
