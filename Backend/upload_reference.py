"""
Uploads Cricsheet reference CSVs to Supabase as read-only lookup tables.

  people_registry  — one row per person (from people.csv)
                     identifier, name, unique_name, key_cricinfo, key_bcci, key_cricketarchive

  people_names     — all name variants per person (from names.csv)
                     identifier, name

These tables are never modified by the ETL — treat as read-only.
Link to players table via:  players.registry_id = people_registry.identifier
"""

import csv
import psycopg2
from psycopg2.extras import execute_values
from config import DATABASE_URL

PEOPLE_CSV = "data/people.csv"
NAMES_CSV  = "data/names.csv"


def create_tables(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS people_registry (
            identifier          TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            unique_name         TEXT,
            key_cricinfo        TEXT,
            key_bcci            TEXT,
            key_cricketarchive  TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS people_names (
            id          SERIAL PRIMARY KEY,
            identifier  TEXT NOT NULL REFERENCES people_registry(identifier),
            name        TEXT NOT NULL,
            UNIQUE (identifier, name)
        )
    """)


def upload_people(cur):
    rows = []
    with open(PEOPLE_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((
                r["identifier"],
                r["name"],
                r["unique_name"]          or None,
                r["key_cricinfo"]         or None,
                r["key_bcci"]             or None,
                r["key_cricketarchive"]   or None,
            ))

    execute_values(cur, """
        INSERT INTO people_registry
            (identifier, name, unique_name, key_cricinfo, key_bcci, key_cricketarchive)
        VALUES %s
        ON CONFLICT (identifier) DO NOTHING
    """, rows)

    print(f"  people_registry : {cur.rowcount} inserted  ({len(rows)} rows in CSV)")


def upload_names(cur):
    rows = []
    with open(NAMES_CSV, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append((r["identifier"], r["name"]))

    execute_values(cur, """
        INSERT INTO people_names (identifier, name)
        VALUES %s
        ON CONFLICT (identifier, name) DO NOTHING
    """, rows)

    print(f"  people_names    : {cur.rowcount} inserted  ({len(rows)} rows in CSV)")


def main():
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()

    print("Creating tables...")
    create_tables(cur)
    conn.commit()

    print("Uploading people_registry...")
    upload_people(cur)

    print("Uploading people_names...")
    upload_names(cur)

    conn.commit()

    # Quick verification
    for table in ("people_registry", "people_names"):
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  {table}: {cur.fetchone()[0]} rows in Supabase")

    # Sample join to players
    print("\nSample — players linked to registry:")
    cur.execute("""
        SELECT p.name, pr.unique_name, pr.key_cricinfo
        FROM players p
        JOIN people_registry pr ON pr.identifier = p.registry_id
        WHERE pr.key_cricinfo IS NOT NULL
        ORDER BY p.name
        LIMIT 8
    """)
    for r in cur.fetchall():
        print(f"  {r[0]:<30} {r[1]:<30} cricinfo:{r[2]}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
