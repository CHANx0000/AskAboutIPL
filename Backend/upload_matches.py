#!/usr/bin/env python3
"""Upload all IPL match JSON files to Supabase.

Usage:
    python upload_matches.py
"""

import json
import sys
from pathlib import Path

import psycopg2
from psycopg2.extras import execute_values

sys.path.insert(0, str(Path(__file__).parent))
from config import DATABASE_URL

DATA_DIR = Path(__file__).parent / "data" / "ipl_json"
BATCH_SIZE = 50


def create_table(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                match_id  BIGINT PRIMARY KEY,
                data      JSONB  NOT NULL
            )
        """)
    conn.commit()
    print("Table 'matches' ready.")


def upload(conn: psycopg2.extensions.connection) -> None:
    files = sorted(DATA_DIR.glob("*.json"))
    total = len(files)

    if total == 0:
        print(f"No JSON files found in {DATA_DIR}")
        return

    print(f"Found {total} JSON files — uploading in batches of {BATCH_SIZE}…\n")

    uploaded = 0
    skipped  = 0

    for i in range(0, total, BATCH_SIZE):
        batch = files[i : i + BATCH_SIZE]
        rows: list[tuple[int, str]] = []

        for f in batch:
            try:
                match_id = int(f.stem)
                data     = json.loads(f.read_text(encoding="utf-8"))
                rows.append((match_id, json.dumps(data)))
            except (ValueError, json.JSONDecodeError) as exc:
                print(f"  skip {f.name}: {exc}")
                skipped += 1

        if rows:
            with conn.cursor() as cur:
                execute_values(
                    cur,
                    """
                    INSERT INTO matches (match_id, data)
                    VALUES %s
                    ON CONFLICT (match_id) DO UPDATE
                        SET data = EXCLUDED.data
                    """,
                    rows,
                )
            conn.commit()
            uploaded += len(rows)

        done = min(i + BATCH_SIZE, total)
        bar  = int(done / total * 30)
        print(
            f"\r  [{'█' * bar}{'░' * (30 - bar)}] {done}/{total}"
            f"  uploaded={uploaded}  skipped={skipped}",
            end="",
            flush=True,
        )

    print(f"\n\nDone — {uploaded} matches uploaded, {skipped} skipped.")


def main() -> None:
    print("Connecting to Supabase…")
    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as exc:
        print(f"Connection failed: {exc}")
        sys.exit(1)

    try:
        create_table(conn)
        upload(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
