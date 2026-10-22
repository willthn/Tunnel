"""Récupération de la météo de Dijon auprès de 4 fournisseurs (A : Data 1).

Aucune clé n'est nécessaire. Chaque ligne renvoyée a la forme
{"ts": datetime (heure locale Europe/Paris, naïve), "source": str, "kind": str, "temp_c": float}.

| kind       | source                        | fournisseur                                      |
|------------|-------------------------------|--------------------------------------------------|
| forecast   | meteofrance-arome, ecmwf-ifs, | Open-Meteo (modèles Météo-France, ECMWF, NOAA GFS) |
|            | gfs                           |                                                  |
| reanalysis | copernicus-era5               | Copernicus ERA5, servi par Open-Meteo            |
| observed   | noaa-isd-07280                | NOAA NCEI, station Dijon-Longvic (ISD)           |
| observed   | meteofrance-21473001          | Météo-France, données climatologiques horaires   |

Une source en panne n'arrête pas les autres : `fetch_all` renvoie aussi un rapport par source.
"""
import csv
import gzip
import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

PARIS = ZoneInfo("Europe/Paris")
UTC = ZoneInfo("UTC")
LAT, LON = 47.32, 5.04
TIMEOUT = 40

FORECAST_MODELS = {  # modèle Open-Meteo -> nom de source en base
    "meteofrance_seamless": "meteofrance-arome",
    "ecmwf_ifs025": "ecmwf-ifs",
    "gfs_seamless": "gfs",
}
NOAA_STATION = "07280099999"  # Dijon-Longvic
NOAA_SOURCE = "noaa-isd-07280"
MF_STATION = "21473001"  # Dijon-Longvic
MF_SOURCE = "meteofrance-21473001"
MF_FILES = {  # (première année, dernière année) -> fichier ouvert du département 21
    (2020, 2024): "H_21_previous-2020-2024.csv.gz",
    (2025, 2026): "H_21_latest-2025-2026.csv.gz",
}
MF_BASE = "https://meteofrance.s3.sbg.io.cloud.ovh.net/data/synchro_ftp/BASE/HOR/"
HISTORICAL_AFTER_DAYS = 7  # au-delà, les prévisions viennent de l'archive des prévisions passées


def _row(ts: datetime, source: str, kind: str, temp_c: float) -> dict:
    return {"ts": ts, "source": source, "kind": kind, "temp_c": float(temp_c)}


def _open_meteo(url: str, start: date, end: date, models: str) -> dict:
    params = {
        "latitude": LAT, "longitude": LON, "hourly": "temperature_2m", "models": models,
        "start_date": start.isoformat(), "end_date": end.isoformat(), "timezone": "Europe/Paris",
    }
    return httpx.get(url, params=params, timeout=TIMEOUT).raise_for_status().json()["hourly"]


def fetch_forecasts(start: date, end: date) -> list[dict]:
    """Prévisions des 3 modèles. Nuit passée : prévisions archivées ; sinon prévision en direct."""
    old = end < date.today() - timedelta(days=HISTORICAL_AFTER_DAYS)
    url = (
        "https://historical-forecast-api.open-meteo.com/v1/forecast"
        if old else "https://api.open-meteo.com/v1/forecast"
    )
    hourly = _open_meteo(url, start, end, ",".join(FORECAST_MODELS))
    rows = []
    for model, source in FORECAST_MODELS.items():
        col = hourly.get(f"temperature_2m_{model}") or hourly.get("temperature_2m")
        for ts, t in zip(hourly["time"], col or []):
            if t is not None:
                rows.append(_row(datetime.fromisoformat(ts), source, "forecast", t))
    return rows


def fetch_era5(start: date, end: date) -> list[dict]:
    hourly = _open_meteo("https://archive-api.open-meteo.com/v1/archive", start, end, "era5")
    col = hourly.get("temperature_2m_era5") or hourly["temperature_2m"]
    return [
        _row(datetime.fromisoformat(ts), "copernicus-era5", "reanalysis", t)
        for ts, t in zip(hourly["time"], col) if t is not None
    ]


def fetch_noaa(start: date, end: date) -> list[dict]:
    """ISD global-hourly : températures en dixièmes de °C, rapports toutes les 30 min (heure UTC).

    La station Dijon-Longvic n'a plus de données après le 2025-08-24 : au-delà, la liste est vide."""
    params = {
        "dataset": "global-hourly", "stations": NOAA_STATION, "dataTypes": "TMP", "format": "csv",
        "startDate": (start - timedelta(days=1)).isoformat(), "endDate": (end + timedelta(days=1)).isoformat(),
    }
    resp = httpx.get("https://www.ncei.noaa.gov/access/services/data/v1", params=params, timeout=TIMEOUT)
    resp.raise_for_status()
    rows: dict[datetime, dict] = {}
    for r in csv.DictReader(io.StringIO(resp.text)):
        raw, _, quality = r["TMP"].partition(",")
        if raw.startswith("+9999") or raw.startswith("-9999") or quality not in {"0", "1", "4", "5", "9"}:
            continue  # valeur manquante ou douteuse
        utc = datetime.fromisoformat(r["DATE"]).replace(tzinfo=UTC)
        local = utc.astimezone(PARIS).replace(tzinfo=None)
        hour = local.replace(minute=0, second=0)
        if local.minute == 0 or hour not in rows:  # préférer le rapport pile à l'heure
            rows[hour] = _row(hour, NOAA_SOURCE, "observed", int(raw) / 10)
    return [r for h, r in sorted(rows.items()) if start <= h.date() <= end]


_mf_cache: dict[str, list[tuple[datetime, float]]] = {}  # fichier -> [(heure UTC, T)] de la station


def _meteofrance_station_rows(name: str) -> list[tuple[datetime, float]]:
    """Télécharge (≈ 40 s, 10 Mo) puis garde seulement la station Dijon-Longvic. Mis en cache."""
    if name in _mf_cache:
        return _mf_cache[name]
    last: Exception | None = None
    for _ in range(3):
        try:
            resp = httpx.get(MF_BASE + name, timeout=httpx.Timeout(300, connect=20), follow_redirects=True)
            resp.raise_for_status()
            break
        except httpx.HTTPError as e:
            last = e
    else:
        raise last  # type: ignore[misc]
    out = []
    with gzip.open(io.BytesIO(resp.content), "rt", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter=";"):
            if r["NUM_POSTE"] == MF_STATION and r["T"]:
                out.append((datetime.strptime(r["AAAAMMJJHH"], "%Y%m%d%H").replace(tzinfo=UTC), float(r["T"])))
    _mf_cache[name] = out
    return out


def fetch_meteofrance(start: date, end: date) -> list[dict]:
    """Fichier ouvert horaire du département 21 (UTC), filtré sur la station Dijon-Longvic."""
    rows = []
    for (y0, y1), name in MF_FILES.items():
        if end.year < y0 or start.year > y1:
            continue
        for utc, t in _meteofrance_station_rows(name):
            local = utc.astimezone(PARIS).replace(tzinfo=None)
            if start <= local.date() <= end:
                rows.append(_row(local, MF_SOURCE, "observed", t))
    return rows


FETCHERS = {
    "open-meteo forecast": fetch_forecasts,
    "copernicus era5": fetch_era5,
    "noaa ncei": fetch_noaa,
    "meteo-france": fetch_meteofrance,
}


LIVE = ("open-meteo forecast",)  # rafraîchissement à l'exécution : rapide (~1 s). Les mesures (10 Mo) se chargent avec load_data.py


def fetch_all(start: date, end: date, only: tuple[str, ...] | None = None) -> tuple[list[dict], list[dict]]:
    """Interroge les fournisseurs (tous, ou ceux de `only`). Renvoie (lignes, rapport par fournisseur)."""
    rows, report = [], []
    for name, fn in FETCHERS.items():
        if only is not None and name not in only:
            continue
        try:
            got = fn(start, end)
            rows += got
            report.append({"provider": name, "rows": len(got), "error": None})
        except Exception as e:  # une source en panne ne bloque pas les autres
            report.append({"provider": name, "rows": 0, "error": f"{type(e).__name__}: {e}"})
    return rows, report
