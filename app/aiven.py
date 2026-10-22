"""Mode DATA_MODE=aiven : requêtes SQL sur Aiven PostgreSQL (A : Data 1).

Appelé uniquement par app/data.py. Les valeurs sortent au même format que le mode fake :
NUMERIC en float, DATE et TIMESTAMP en chaînes ISO.
"""
from datetime import date, datetime, timedelta
from decimal import Decimal

from psycopg.types.json import Jsonb

from app import weather
from app.db import connect

ORDER = {
    "buses": "bus_id", "services": "start_time, service_id", "chargers": "charger_id",
    "consumption_obs": "night_date, bus_id, service_id", "model_runs": "night_date",
}


def _clean(value):
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _rows(sql: str, params: tuple = ()) -> list[dict]:
    with connect().cursor() as cur:
        cur.execute(sql, params)
        return [{k: _clean(v) for k, v in row.items()} for row in cur.fetchall()]


def select(table: str) -> list[dict]:
    return _rows(f"SELECT * FROM {table} ORDER BY {ORDER[table]}")  # table : constante interne, jamais saisie


def depot() -> dict:
    rows = _rows("SELECT max_power_kw FROM depot ORDER BY depot_id LIMIT 1")
    if not rows:
        raise LookupError("table depot vide : lancer scripts/load_data.py")
    return rows[0]


def night_window(night: date) -> tuple[datetime, datetime]:
    """De 18h la veille à 8h le jour de `night`."""
    start = datetime.combine(night - timedelta(days=1), datetime.min.time()) + timedelta(hours=18)
    return start, start + timedelta(hours=14)


def window(table: str, night: date) -> list[dict]:
    start, end = night_window(night)
    return _rows(f"SELECT * FROM {table} WHERE ts BETWEEN %s AND %s ORDER BY ts", (start, end))


def forecast(night: date) -> list[dict]:
    """Température retenue (weather_hourly). Si la nuit n'est pas encore en base, on va la chercher."""
    rows = window("weather_hourly", night)
    if not rows:
        ingest_weather(night - timedelta(days=1), night)
        rows = window("weather_hourly", night)
    if not rows:
        raise LookupError(f"aucune météo disponible pour la nuit du {night}")
    return rows


def save_plan(plan: dict, explanation: str) -> None:
    with connect().cursor() as cur:
        cur.execute(
            """INSERT INTO plans (plan_id, night_date, strategy, payload, explanation)
               VALUES (%s, %s, %s, %s, %s)
               ON CONFLICT (plan_id) DO UPDATE
               SET payload = EXCLUDED.payload, explanation = EXCLUDED.explanation, created_at = now()""",
            (plan["plan_id"], plan["night_date"], plan["strategy"], Jsonb({**plan, "explanation": explanation}), explanation),
        )


def get_plan(night_date: str, strategy: str) -> dict | None:
    rows = _rows(
        """SELECT payload, explanation FROM plans WHERE night_date = %s AND strategy = %s
           ORDER BY created_at DESC LIMIT 1""",
        (night_date, strategy),
    )
    return {**rows[0]["payload"], "explanation": rows[0]["explanation"]} if rows else None


def save_consumption_obs(rows: list[dict]) -> None:
    with connect().cursor() as cur:
        cur.executemany(
            """INSERT INTO consumption_obs (night_date, bus_id, service_id, line, temp_c, km, kwh)
               VALUES (%(night_date)s, %(bus_id)s, %(service_id)s, %(line)s, %(temp_c)s, %(km)s, %(kwh)s)
               ON CONFLICT (night_date, bus_id, service_id) DO UPDATE
               SET line = EXCLUDED.line, temp_c = EXCLUDED.temp_c, km = EXCLUDED.km, kwh = EXCLUDED.kwh""",
            rows,
        )


def save_model_run(run: dict) -> None:
    with connect().cursor() as cur:
        cur.execute(
            """INSERT INTO model_runs (run_id, night_date, mape, coef_json) VALUES (%s, %s, %s, %s)
               ON CONFLICT (run_id) DO UPDATE
               SET night_date = EXCLUDED.night_date, mape = EXCLUDED.mape, coef_json = EXCLUDED.coef_json""",
            (run["run_id"], run["night_date"], run["mape"], Jsonb(run["coef_json"])),
        )


# --- Météo : sources multiples -> weather_obs -> weather_hourly ---------------------

def store_weather_obs(rows: list[dict]) -> None:
    with connect().cursor() as cur:
        cur.executemany(
            """INSERT INTO weather_obs (ts, source, kind, temp_c, fetched_at)
               VALUES (%(ts)s, %(source)s, %(kind)s, %(temp_c)s, now())
               ON CONFLICT (ts, source, kind) DO UPDATE SET temp_c = EXCLUDED.temp_c, fetched_at = now()""",
            rows,
        )


def consolidate_weather(start: date, end: date) -> int:
    """weather_hourly = médiane des prévisions ; à défaut, moyenne des mesures puis de la réanalyse."""
    lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()) + timedelta(days=1)
    with connect().cursor() as cur:
        for kinds, conflict in ((("forecast",), "DO UPDATE SET temp_c = EXCLUDED.temp_c"),
                                (("observed",), "DO NOTHING"), (("reanalysis",), "DO NOTHING")):
            cur.execute(
                f"""INSERT INTO weather_hourly (ts, temp_c)
                    SELECT ts, percentile_cont(0.5) WITHIN GROUP (ORDER BY temp_c)
                    FROM weather_obs WHERE kind = ANY(%s) AND ts >= %s AND ts < %s GROUP BY ts
                    ON CONFLICT (ts) {conflict}""",
                (list(kinds), lo, hi),
            )
        cur.execute("SELECT count(*) AS n FROM weather_hourly WHERE ts >= %s AND ts < %s", (lo, hi))
        return cur.fetchone()["n"]


def ingest_weather(start: date, end: date) -> list[dict]:
    """Interroge les 4 fournisseurs, écrit weather_obs, recalcule weather_hourly. Renvoie le rapport."""
    rows, report = weather.fetch_all(start, end)
    if rows:
        store_weather_obs(rows)
        consolidate_weather(start, end)
    return report


def forecast_error(start: date, end: date) -> dict:
    """Écart absolu moyen (°C) de chaque prévision par rapport aux mesures Météo-France."""
    lo, hi = datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()) + timedelta(days=1)
    rows = _rows(
        """SELECT f.source, avg(abs(f.temp_c - o.temp_c)) AS mae_c, count(*) AS hours
           FROM weather_obs f JOIN weather_obs o ON o.ts = f.ts AND o.kind = 'observed' AND o.source = %s
           WHERE f.kind = 'forecast' AND f.ts >= %s AND f.ts < %s GROUP BY f.source ORDER BY f.source""",
        (weather.MF_SOURCE, lo, hi),
    )
    return {r["source"]: {"mae_c": round(r["mae_c"], 2), "hours": r["hours"]} for r in rows}

