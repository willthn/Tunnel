"""Connexion Aiven PostgreSQL (A : Data 1). Une seule connexion réutilisée, TLS via PG_URI."""
import os

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row

load_dotenv()

_conn: psycopg.Connection | None = None


def connect() -> psycopg.Connection:
    """Connexion partagée en autocommit. PG_URI vient de .env (jamais commité), sslmode=require."""
    global _conn
    if _conn is not None and not _conn.closed:
        return _conn
    uri = os.getenv("PG_URI")
    if not uri:
        raise RuntimeError("PG_URI manquant : copier .env.example en .env et le remplir (console Aiven)")
    _conn = psycopg.connect(uri, row_factory=dict_row, autocommit=True, connect_timeout=15)
    return _conn


def close() -> None:
    global _conn
    if _conn is not None and not _conn.closed:
        _conn.close()
    _conn = None
