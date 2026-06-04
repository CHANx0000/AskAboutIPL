import psycopg2
from psycopg2.extras import RealDictCursor

from config import DATABASE_URL


def get_connection():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id     SERIAL PRIMARY KEY,
                    name   TEXT    NOT NULL,
                    age    INTEGER NOT NULL,
                    gender TEXT    NOT NULL
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS matches (
                    match_id  BIGINT PRIMARY KEY,
                    data      JSONB  NOT NULL
                )
            """)
        conn.commit()
