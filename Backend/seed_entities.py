"""
Reads all 1243 matches from Supabase JSONB and populates entity tables:
  seasons, teams, venues, players
Run once after init_db().
"""

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from config import DATABASE_URL


def seed():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    print("Fetching all matches...")
    cur.execute("SELECT data FROM matches")
    rows = cur.fetchall()
    print(f"  {len(rows)} matches loaded\n")

    seasons = set()
    teams   = set()
    venues  = set()
    players = set()

    for row in rows:
        info = row["data"].get("info", {})

        # seasons — coerce to str (some JSONs store year as int)
        season = info.get("season")
        if season:
            seasons.add(str(season))

        # teams
        for team in info.get("teams", []):
            teams.add(team)

        # venues
        venue = info.get("venue")
        if venue:
            venues.add(venue)

        # players — from squad list per team
        for player_list in info.get("players", {}).values():
            for p in player_list:
                players.add(p)

        # players — from every delivery (catches names not in squad list)
        for innings in row["data"].get("innings", []):
            for over in innings.get("overs", []):
                for d in over.get("deliveries", []):
                    players.add(d["batter"])
                    players.add(d["bowler"])
                    players.add(d["non_striker"])
                    for w in d.get("wickets", []):
                        if "player_out" in w:
                            players.add(w["player_out"])
                        for f in w.get("fielders", []):
                            if "name" in f:
                                players.add(f["name"])

    print(f"Unique seasons : {len(seasons)}")
    print(f"Unique teams   : {len(teams)}")
    print(f"Unique venues  : {len(venues)}")
    print(f"Unique players : {len(players)}\n")

    # INSERT with ON CONFLICT DO NOTHING — safe to re-run
    execute_values(cur,
        "INSERT INTO seasons (year) VALUES %s ON CONFLICT (year) DO NOTHING",
        [(s,) for s in sorted(seasons)]
    )

    execute_values(cur,
        "INSERT INTO teams (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        [(t,) for t in sorted(teams)]
    )

    execute_values(cur,
        "INSERT INTO venues (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        [(v,) for v in sorted(venues)]
    )

    execute_values(cur,
        "INSERT INTO players (name) VALUES %s ON CONFLICT (name) DO NOTHING",
        [(p,) for p in sorted(players)]
    )

    conn.commit()

    # Print what was inserted
    for table, col in [("seasons","year"), ("teams","name"), ("venues","name"), ("players","name")]:
        cur.execute(f"SELECT {col} FROM {table} ORDER BY {col}")
        result = cur.fetchall()
        print(f"\n── {table} ({len(result)} rows) ──")
        for r in result:
            print(f"  {r[col]}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    seed()
