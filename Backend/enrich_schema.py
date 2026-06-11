"""
Enriches the seasons and players tables with additional columns.

Seasons:
  edition_num  — IPL edition number (1 = 2008, 19 = 2026)
  start_year   — calendar year the season started
  end_year     — calendar year the season ended (differs for split seasons)
  champion     — winner of that season's Final (pulled from matches table)

Players:
  full_name      — complete legal name (not in match JSON — needs external source)
  dob            — date of birth
  nationality    — country
  batting_style  — right/left hand
  bowling_style  — right/left arm pace/spin

  These profile columns are NULL until populated via an external API
  (Cricinfo, ESPNcricinfo, or Cricsheet people.csv).
  Use registry_id to link and fetch from those sources.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL


# ── Season metadata ───────────────────────────────────────────────────────────
# (year_str, edition_num, start_year, end_year)

SEASON_META = [
    ("2007/08",  1, 2007, 2008),
    ("2009",     2, 2009, 2009),
    ("2009/10",  3, 2009, 2010),
    ("2011",     4, 2011, 2011),
    ("2012",     5, 2012, 2012),
    ("2013",     6, 2013, 2013),
    ("2014",     7, 2014, 2014),
    ("2015",     8, 2015, 2015),
    ("2016",     9, 2016, 2016),
    ("2017",    10, 2017, 2017),
    ("2018",    11, 2018, 2018),
    ("2019",    12, 2019, 2019),
    ("2020/21", 13, 2020, 2021),
    ("2021",    14, 2021, 2021),
    ("2022",    15, 2022, 2022),
    ("2023",    16, 2023, 2023),
    ("2024",    17, 2024, 2024),
    ("2025",    18, 2025, 2025),
    ("2026",    19, 2026, 2026),
]


def enrich():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # ── 1. Seasons — add columns ──────────────────────────────────────────────
    for col, dtype in [
        ("edition_num", "INT"),
        ("start_year",  "INT"),
        ("end_year",    "INT"),
        ("champion",    "TEXT"),
    ]:
        cur.execute(f"ALTER TABLE seasons ADD COLUMN IF NOT EXISTS {col} {dtype}")

    # Populate edition_num, start_year, end_year from the static mapping
    for year_str, edition, start, end in SEASON_META:
        cur.execute("""
            UPDATE seasons
            SET edition_num = %s, start_year = %s, end_year = %s
            WHERE year = %s
        """, (edition, start, end, year_str))

    # Derive champion from the Finals match for each season
    cur.execute("""
        UPDATE seasons s
        SET champion = (
            SELECT data->'info'->'outcome'->>'winner'
            FROM matches m
            WHERE m.data->'info'->>'season'  = s.year
              AND m.data->'info'->'event'->>'stage' ILIKE '%final%'
              AND m.data->'info'->'outcome'->>'winner' IS NOT NULL
            ORDER BY m.match_id DESC
            LIMIT 1
        )
    """)

    conn.commit()

    cur.execute("""
        SELECT edition_num, year, start_year, end_year, champion
        FROM seasons ORDER BY edition_num
    """)
    rows = cur.fetchall()
    print(f"── seasons ({len(rows)} rows) ──")
    print(f"  {'#':<4} {'YEAR':<10} {'START':<7} {'END':<7} CHAMPION")
    print(f"  {'─'*4} {'─'*10} {'─'*7} {'─'*7} {'─'*30}")
    for r in rows:
        print(f"  {r['edition_num']:<4} {r['year']:<10} {r['start_year']:<7} {r['end_year']:<7} {r['champion'] or '—'}")

    # ── 2. Players — add profile columns ─────────────────────────────────────
    for col, dtype in [
        ("full_name",     "TEXT"),
        ("dob",           "DATE"),
        ("nationality",   "TEXT"),
        ("batting_style", "TEXT"),
        ("bowling_style", "TEXT"),
    ]:
        cur.execute(f"ALTER TABLE players ADD COLUMN IF NOT EXISTS {col} {dtype}")

    conn.commit()

    cur.execute("""
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_name = 'players'
        ORDER BY ordinal_position
    """)
    cols = cur.fetchall()
    print(f"\n── players schema ──")
    for c in cols:
        print(f"  {c['column_name']:<20} {c['data_type']}")

    print("""
  NOTE: full_name, dob, nationality, batting_style, bowling_style are NULL.
  Populate via registry_id using:
    - Cricsheet people.csv  (free, links registry_id → full name + DOB)
    - ESPNcricinfo API      (batting/bowling style, nationality)
""")

    cur.close()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    enrich()
