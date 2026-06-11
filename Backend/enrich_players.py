"""
Enriches the players table with Cricsheet registry IDs.

info.registry.people maps every player name → stable 8-char hex ID.
This ID is consistent across all match files and can link to external
databases (CricInfo, ESPNcricinfo, etc.).

Run after seed_entities.py.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL


def enrich():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # ── 1. Add registry_id column (indexed but not unique — name variants share an ID) ──
    cur.execute("ALTER TABLE players ADD COLUMN IF NOT EXISTS registry_id TEXT")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_players_registry_id ON players(registry_id)")
    conn.commit()

    # ── 2. Collect registry mappings from all matches ─────────────────────────
    print("Scanning registry across all matches...")
    cur.execute("SELECT data->'info'->'registry'->'people' AS people FROM matches")
    rows = cur.fetchall()

    registry: dict[str, str] = {}
    conflicts: dict[str, set] = {}

    for row in rows:
        people = row["people"] or {}
        for name, rid in people.items():
            if name in registry and registry[name] != rid:
                # Same name, different ID — flag it
                conflicts.setdefault(name, {registry[name]}).add(rid)
            else:
                registry[name] = rid

    print(f"  {len(registry)} unique name→id mappings found")
    if conflicts:
        print(f"  {len(conflicts)} name conflicts (same name, different IDs):")
        for name, ids in conflicts.items():
            print(f"    '{name}' → {ids}")

    # ── 3. Update players table ───────────────────────────────────────────────
    updated = 0
    not_found = []

    for name, rid in registry.items():
        cur.execute(
            "UPDATE players SET registry_id = %s WHERE name = %s AND (registry_id IS NULL OR registry_id = %s)",
            (rid, name, rid)
        )
        if cur.rowcount:
            updated += 1
        else:
            # Name in registry but not in players table (likely an umpire or official)
            cur.execute("SELECT 1 FROM players WHERE name = %s", (name,))
            if not cur.fetchone():
                not_found.append(name)

    conn.commit()

    # ── 4. Report ─────────────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) AS total FROM players")
    total = cur.fetchone()["total"]

    cur.execute("SELECT COUNT(*) AS with_id FROM players WHERE registry_id IS NOT NULL")
    with_id = cur.fetchone()["with_id"]

    cur.execute("SELECT COUNT(*) AS without_id FROM players WHERE registry_id IS NULL")
    without_id = cur.fetchone()["without_id"]

    print(f"\n── players table ──")
    print(f"  Total players  : {total}")
    print(f"  With registry  : {with_id}")
    print(f"  Without registry: {without_id}")

    if not_found:
        print(f"\n  Names in registry not in players table ({len(not_found)}) — likely umpires/officials:")
        for n in sorted(not_found):
            print(f"    {n}")

    # ── 5. Sample output ──────────────────────────────────────────────────────
    print(f"\n── sample (10 players) ──")
    cur.execute("""
        SELECT name, registry_id
        FROM players
        WHERE registry_id IS NOT NULL
        ORDER BY name
        LIMIT 10
    """)
    for r in cur.fetchall():
        print(f"  {r['name']:<35} {r['registry_id']}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    enrich()
