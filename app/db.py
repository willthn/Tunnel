"""Connexion Aiven PostgreSQL (A : Data 1). Une seule connexion réutilisée, TLS via PG_URI."""
import os
import threading

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

_conn: psycopg.Connection | None = None
_lock = threading.Lock()  # FastAPI sert les routes synchrones depuis plusieurs threads


def connect() -> psycopg.Connection:
    """Connexion partagée en autocommit, rouverte si elle est fermée ou coupée. sslmode=require via PG_URI."""
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn
    uri = os.getenv("PG_URI")
    if not uri:
        raise RuntimeError("PG_URI manquant : copier .env.example en .env et le remplir (console Aiven)")
    _conn = psycopg.connect(
        uri, row_factory=dict_row, autocommit=True, connect_timeout=15,
        # Détecte en ~1 min une connexion coupée en silence (pare-feu, inactivité) au lieu de bloquer.
        keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=3,
    )
    return _conn


def execute(sql: str, params=None, many: bool = False) -> list[dict]:
    """Exécute une requête et renvoie ses lignes (liste vide sans résultat).

    Si la connexion a été coupée (OperationalError), on la rouvre et on rejoue une fois :
    toutes nos écritures sont des upserts, donc rejouer est sans danger."""
    for attempt in (1, 2):
        try:
            with _lock, connect().cursor() as cur:
                if many:
                    cur.executemany(sql, params or [])
                else:
                    cur.execute(sql, params)
                return cur.fetchall() if cur.description else []
        except psycopg.OperationalError:
            close()
            if attempt == 2:
                raise
    return []


def close() -> None:
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
    _conn = None
