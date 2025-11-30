# app/db.py
from __future__ import annotations

import psycopg
from typing import List, Dict, Any

from app.config import get_settings


def get_connection():
    """
    Devuelve una conexión a Postgres (Neon) usando psycopg.
    La conexión se abre cada vez que se llama.
    Para cosas más serias luego podemos usar pool.
    """
    settings = get_settings()
    return psycopg.connect(settings.DATABASE_URL)


def test_db_connection():
    """
    Prueba simple: hace SELECT 1 y lo imprime.
    La vamos a llamar en el startup de FastAPI.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1;")
                row = cur.fetchone()
                print(f"[DB TEST] Resultado SELECT 1 → {row}")
    except Exception as e:
        print("[DB TEST] Error conectando a la base de datos:", e)
        raise


def print_bots_table():
    """
    Imprime por consola todos los registros de la tabla `Bot`.
    Ajustá las columnas si tu tabla tiene otros nombres.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute('SELECT * FROM public."Bot";')
                rows = cur.fetchall()

                print(f"[DB BOTS] {len(rows)} registros en la tabla Bot:")

                for row in rows:
                    print(row)

                if not rows:
                    print("[DB BOTS] (sin registros)")
    except Exception as e:
        print("[DB BOTS] Error leyendo la tabla Bot:", e)
        raise


def list_all_tables():
    """
    Lista todas las tablas de la base (schemas de usuario)
    para ver cómo se llaman realmente.
    """
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT table_schema, table_name
                    FROM information_schema.tables
                    WHERE table_type = 'BASE TABLE'
                      AND table_schema NOT IN ('pg_catalog', 'information_schema')
                    ORDER BY table_schema, table_name;
                    """
                )
                rows = cur.fetchall()

                print("[DB TABLES] Tablas encontradas:")
                for schema, name in rows:
                    print(f" - {schema}.{name}")
    except Exception as e:
        print("[DB TABLES] Error listando tablas:", e)


def get_running_bots() -> list[dict]:
    """
    Devuelve una lista de bots que están en estado RUNNING
    en la tabla public."Bot".

    Columnas relevantes del modelo:
      - id              TEXT (cuid de Prisma)
      - instrument      TEXT
      - trendTimeframe  TEXT
      - signalTimeframe TEXT
      - status          BotStatus (enum)
      - isDeleted       BOOLEAN
    """
    bots: list[dict] = []

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    '''
                    SELECT
                        id,
                        instrument,
                        "trendTimeframe",
                        "signalTimeframe",
                        status
                    FROM public."Bot"
                    WHERE status = 'RUNNING'
                      AND "isDeleted" = FALSE;
                    '''
                )
                rows = cur.fetchall()
                print(f"[DB BOTS] {len(rows)} bots con status = RUNNING")

                for row in rows:
                    bot = {
                        "id": row[0],
                        "instrument": row[1],
                        "trend_tf": row[2],   # M30, H1, etc.
                        "jw_tf": row[3],      # M5, M15, etc.
                        "status": row[4],
                    }
                    print(f"  - {bot}")
                    bots.append(bot)

    except Exception as e:
        print("[DB BOTS] Error leyendo bots running:", e)

    return bots
