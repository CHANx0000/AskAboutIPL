import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL


def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:

            # Raw match data (already populated — 1243 rows)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    match_id  BIGINT PRIMARY KEY,
                    data      JSONB  NOT NULL
                )
            """)

            # ── Dimension / entity tables ────────────────────────────────────

            cur.execute("""
                CREATE TABLE IF NOT EXISTS seasons (
                    season_id  SERIAL PRIMARY KEY,
                    year       TEXT NOT NULL UNIQUE
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS teams (
                    team_id  SERIAL PRIMARY KEY,
                    name     TEXT NOT NULL UNIQUE
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS venues (
                    venue_id  SERIAL PRIMARY KEY,
                    name      TEXT NOT NULL UNIQUE,
                    city      TEXT
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS players (
                    player_id  SERIAL PRIMARY KEY,
                    name       TEXT NOT NULL UNIQUE
                )
            """)

        conn.commit()
