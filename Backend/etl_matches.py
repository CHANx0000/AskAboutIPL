"""
ETL: Populate relational fact tables from raw match JSONB.

Run for a single season first to validate, then all seasons:
  python etl_matches.py --season 2026
  python etl_matches.py --all

What this script does (per match):
  1. ALTER matches table — add FK / scalar columns if not present
  2. CREATE fact tables (innings, player_innings_stats, player_bowling_stats, player_team_season)
  3. For each match in the target season:
     a. Resolve FK ids (season, venue, team1, team2, winner, toss_winner)
     b. UPDATE matches with resolved FKs + scalar fields
     c. Upsert player_team_season from info.players squads
     d. Walk deliveries per innings → accumulate stats → insert fact rows
"""

import argparse
import sys
from collections import defaultdict

import psycopg2
from psycopg2.extras import RealDictCursor, execute_values
from config import DATABASE_URL
from normalize_entities import VENUE_ALIASES

# ── Phase bucketing (0-indexed over numbers) ─────────────────────────────────
# Powerplay : over 0–5    (overs 1–6 in cricket notation)
# Middle    : over 6–14   (overs 7–15)
# Death     : over 15–19  (overs 16–20)

def phase(over: int) -> str:
    if over <= 5:
        return "pp"
    if over <= 14:
        return "middle"
    return "death"


# ── Wicket kinds NOT credited to the bowler ───────────────────────────────────
NON_BOWLER_WICKETS = {"run out", "retired hurt", "obstructing the field", "retired out"}


# ─────────────────────────────────────────────────────────────────────────────
# Schema setup
# ─────────────────────────────────────────────────────────────────────────────

def migrate_schema(cur):
    """Add FK + scalar columns to matches and create all fact tables."""

    # matches — add columns for resolved FKs and match-level scalars
    for col_def in [
        "season_id        INT  REFERENCES seasons(season_id)",
        "venue_id         INT  REFERENCES venues(venue_id)",
        "team1_id         INT  REFERENCES teams(team_id)",
        "team2_id         INT  REFERENCES teams(team_id)",
        "winner_id        INT  REFERENCES teams(team_id)",
        "toss_winner_id   INT  REFERENCES teams(team_id)",
        "toss_decision    TEXT",
        "match_date       DATE",
        "stage            TEXT",
        "win_by_runs      INT",
        "win_by_wickets   INT",
        "dl_method        BOOLEAN DEFAULT FALSE",
    ]:
        col_name = col_def.split()[0]
        cur.execute(f"ALTER TABLE matches ADD COLUMN IF NOT EXISTS {col_name} {col_def.split(col_name, 1)[1].strip()}")

    # innings
    cur.execute("""
        CREATE TABLE IF NOT EXISTS innings (
            innings_id      SERIAL PRIMARY KEY,
            match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
            innings_num     INT     NOT NULL,
            is_super_over   BOOLEAN NOT NULL DEFAULT FALSE,
            batting_team_id INT     NOT NULL REFERENCES teams(team_id),
            total_runs      INT     NOT NULL DEFAULT 0,
            total_wickets   INT     NOT NULL DEFAULT 0,
            pp_runs         INT     NOT NULL DEFAULT 0,
            pp_wickets      INT     NOT NULL DEFAULT 0,
            middle_runs     INT     NOT NULL DEFAULT 0,
            middle_wickets  INT     NOT NULL DEFAULT 0,
            death_runs      INT     NOT NULL DEFAULT 0,
            death_wickets   INT     NOT NULL DEFAULT 0,
            UNIQUE (match_id, innings_num)
        )
    """)

    # player_innings_stats (batting)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_innings_stats (
            id              SERIAL  PRIMARY KEY,
            match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
            innings_id      INT     NOT NULL REFERENCES innings(innings_id),
            player_id       INT     NOT NULL REFERENCES players(player_id),
            batting_team_id INT     NOT NULL REFERENCES teams(team_id),
            is_opener       BOOLEAN NOT NULL DEFAULT FALSE,
            runs            INT     NOT NULL DEFAULT 0,
            balls           INT     NOT NULL DEFAULT 0,
            fours           INT     NOT NULL DEFAULT 0,
            sixes           INT     NOT NULL DEFAULT 0,
            pp_runs         INT     NOT NULL DEFAULT 0,
            pp_balls        INT     NOT NULL DEFAULT 0,
            middle_runs     INT     NOT NULL DEFAULT 0,
            middle_balls    INT     NOT NULL DEFAULT 0,
            death_runs      INT     NOT NULL DEFAULT 0,
            death_balls     INT     NOT NULL DEFAULT 0,
            dismissal_kind  TEXT,
            UNIQUE (innings_id, player_id)
        )
    """)

    # player_bowling_stats
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_bowling_stats (
            id              SERIAL  PRIMARY KEY,
            match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
            innings_id      INT     NOT NULL REFERENCES innings(innings_id),
            player_id       INT     NOT NULL REFERENCES players(player_id),
            bowling_team_id INT     NOT NULL REFERENCES teams(team_id),
            legal_balls     INT     NOT NULL DEFAULT 0,
            runs_conceded   INT     NOT NULL DEFAULT 0,
            wickets         INT     NOT NULL DEFAULT 0,
            pp_balls        INT     NOT NULL DEFAULT 0,
            pp_runs         INT     NOT NULL DEFAULT 0,
            pp_wickets      INT     NOT NULL DEFAULT 0,
            middle_balls    INT     NOT NULL DEFAULT 0,
            middle_runs     INT     NOT NULL DEFAULT 0,
            middle_wickets  INT     NOT NULL DEFAULT 0,
            death_balls     INT     NOT NULL DEFAULT 0,
            death_runs      INT     NOT NULL DEFAULT 0,
            death_wickets   INT     NOT NULL DEFAULT 0,
            UNIQUE (innings_id, player_id)
        )
    """)

    # player_team_season
    cur.execute("""
        CREATE TABLE IF NOT EXISTS player_team_season (
            player_id  INT  NOT NULL REFERENCES players(player_id),
            team_id    INT  NOT NULL REFERENCES teams(team_id),
            season_id  INT  NOT NULL REFERENCES seasons(season_id),
            PRIMARY KEY (player_id, team_id, season_id)
        )
    """)


# ─────────────────────────────────────────────────────────────────────────────
# FK resolution helpers
# ─────────────────────────────────────────────────────────────────────────────

def _lookup(cur, table, id_col, name_col, name):
    if name is None:
        return None
    cur.execute(f"SELECT {id_col} FROM {table} WHERE {name_col} = %s", (name,))
    row = cur.fetchone()
    if row is None:
        return None
    return row[id_col]


def resolve_match_fks(cur, info: dict) -> dict:
    season_str = str(info.get("season", ""))
    raw_venue  = info.get("venue", "")
    venue_name = VENUE_ALIASES.get(raw_venue, raw_venue)
    teams      = info.get("teams", [None, None])
    outcome    = info.get("outcome", {})
    toss       = info.get("toss", {})

    season_id      = _lookup(cur, "seasons", "season_id", "year", season_str)
    venue_id       = _lookup(cur, "venues",  "venue_id",  "name", venue_name)
    team1_id       = _lookup(cur, "teams",   "team_id",   "name", teams[0] if len(teams) > 0 else None)
    team2_id       = _lookup(cur, "teams",   "team_id",   "name", teams[1] if len(teams) > 1 else None)
    winner_id      = _lookup(cur, "teams",   "team_id",   "name", outcome.get("winner"))
    toss_winner_id = _lookup(cur, "teams",   "team_id",   "name", toss.get("winner"))

    dates    = info.get("dates", [])
    match_date = dates[0] if dates else None
    stage    = info.get("event", {}).get("stage", None)
    by       = outcome.get("by", {})
    method   = outcome.get("method", "")

    return {
        "season_id":      season_id,
        "venue_id":       venue_id,
        "team1_id":       team1_id,
        "team2_id":       team2_id,
        "winner_id":      winner_id,
        "toss_winner_id": toss_winner_id,
        "toss_decision":  toss.get("decision"),
        "match_date":     match_date,
        "stage":          stage,
        "win_by_runs":    by.get("runs"),
        "win_by_wickets": by.get("wickets"),
        "dl_method":      method == "D/L",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Delivery walk
# ─────────────────────────────────────────────────────────────────────────────

def _empty_batter():
    return {
        "runs": 0, "balls": 0, "fours": 0, "sixes": 0,
        "pp_runs": 0, "pp_balls": 0,
        "middle_runs": 0, "middle_balls": 0,
        "death_runs": 0, "death_balls": 0,
        "dismissal_kind": None,
    }

def _empty_bowler():
    return {
        "legal_balls": 0, "runs_conceded": 0, "wickets": 0,
        "pp_balls": 0, "pp_runs": 0, "pp_wickets": 0,
        "middle_balls": 0, "middle_runs": 0, "middle_wickets": 0,
        "death_balls": 0, "death_runs": 0, "death_wickets": 0,
    }

def _empty_innings_totals():
    return {
        "total_runs": 0, "total_wickets": 0,
        "pp_runs": 0, "pp_wickets": 0,
        "middle_runs": 0, "middle_wickets": 0,
        "death_runs": 0, "death_wickets": 0,
    }


def walk_innings(innings_data: dict) -> tuple[dict, dict, dict, tuple]:
    """
    Walk all deliveries in one innings.
    Returns:
        totals       — innings-level phase aggregates
        batters      — {name: stats_dict}
        bowlers      — {name: stats_dict}
        openers      — (batter_name, non_striker_name) from first delivery
    """
    totals  = _empty_innings_totals()
    batters : dict[str, dict] = defaultdict(_empty_batter)
    bowlers : dict[str, dict] = defaultdict(_empty_bowler)
    openers = (None, None)
    first_delivery = True

    for over_obj in innings_data.get("overs", []):
        over_num = over_obj["over"]
        ph = phase(over_num)

        for d in over_obj.get("deliveries", []):
            batter_name  = d.get("batter",    "")
            bowler_name  = d.get("bowler",    "")
            non_striker  = d.get("non_striker", "")
            runs_obj     = d.get("runs", {})
            extras       = d.get("extras", {})
            wickets_list = d.get("wickets", [])

            batter_runs = runs_obj.get("batter", 0)
            total_runs  = runs_obj.get("total",  0)

            is_wide   = "wides"   in extras
            is_noball = "noballs" in extras

            # Opener detection — first delivery of the innings
            if first_delivery:
                openers = (batter_name, non_striker)
                first_delivery = False

            # ── Innings totals
            totals["total_runs"] += total_runs
            totals[f"{ph}_runs"] += total_runs

            # ── Batter stats
            b = batters[batter_name]
            b["runs"]  += batter_runs
            b["fours"] += 1 if batter_runs == 4 else 0
            b["sixes"] += 1 if batter_runs == 6 else 0
            b[f"{ph}_runs"] += batter_runs
            if not is_wide:
                b["balls"] += 1
                b[f"{ph}_balls"] += 1

            # ── Bowler stats
            bw = bowlers[bowler_name]
            bw["runs_conceded"] += total_runs
            bw[f"{ph}_runs"] += total_runs
            if not (is_wide or is_noball):
                bw["legal_balls"]    += 1
                bw[f"{ph}_balls"]    += 1

            # ── Wickets
            for w in wickets_list:
                kind = w.get("kind", "")
                totals["total_wickets"] += 1
                totals[f"{ph}_wickets"] += 1
                batters[batter_name]["dismissal_kind"] = kind
                if kind not in NON_BOWLER_WICKETS:
                    bw["wickets"]           += 1
                    bw[f"{ph}_wickets"]     += 1

    return totals, batters, bowlers, openers


# ─────────────────────────────────────────────────────────────────────────────
# Per-match ETL
# ─────────────────────────────────────────────────────────────────────────────

def etl_match(cur, match_id: int, data: dict, warn_log: list) -> dict:
    info = data.get("info", {})

    # 1. Resolve FKs
    fks = resolve_match_fks(cur, info)

    missing = [k for k, v in fks.items() if v is None and k not in ("win_by_runs", "win_by_wickets", "winner_id", "stage")]
    if missing:
        warn_log.append(f"  WARN match {match_id}: unresolved FKs {missing}")

    # 2. UPDATE matches
    cur.execute("""
        UPDATE matches SET
            season_id      = %(season_id)s,
            venue_id       = %(venue_id)s,
            team1_id       = %(team1_id)s,
            team2_id       = %(team2_id)s,
            winner_id      = %(winner_id)s,
            toss_winner_id = %(toss_winner_id)s,
            toss_decision  = %(toss_decision)s,
            match_date     = %(match_date)s,
            stage          = %(stage)s,
            win_by_runs    = %(win_by_runs)s,
            win_by_wickets = %(win_by_wickets)s,
            dl_method      = %(dl_method)s
        WHERE match_id = %(match_id)s
    """, {**fks, "match_id": match_id})

    # 3. player_team_season
    season_id = fks["season_id"]
    if season_id is not None:
        squad_dict = info.get("players", {})
        for team_name, player_names in squad_dict.items():
            team_id = _lookup(cur, "teams", "team_id", "name", team_name)
            if team_id is None:
                warn_log.append(f"  WARN match {match_id}: team '{team_name}' not in teams table")
                continue
            pts_rows = []
            for pname in player_names:
                pid = _lookup(cur, "players", "player_id", "name", pname)
                if pid is None:
                    warn_log.append(f"  WARN match {match_id}: player '{pname}' not in players table")
                    continue
                pts_rows.append((pid, team_id, season_id))
            if pts_rows:
                execute_values(cur, """
                    INSERT INTO player_team_season (player_id, team_id, season_id)
                    VALUES %s ON CONFLICT DO NOTHING
                """, pts_rows)

    # 4. Walk innings
    innings_list = data.get("innings", [])
    counters = {"innings": 0, "batter_rows": 0, "bowler_rows": 0}

    for idx, innings_data in enumerate(innings_list):
        innings_num   = idx + 1
        batting_team  = innings_data.get("team", "")
        is_super_over = innings_data.get("super_over", False)

        batting_team_id = _lookup(cur, "teams", "team_id", "name", batting_team)
        bowling_team_id = fks["team1_id"] if batting_team_id == fks["team2_id"] else fks["team2_id"]

        if batting_team_id is None:
            warn_log.append(f"  WARN match {match_id} innings {innings_num}: batting team '{batting_team}' not found")
            continue

        totals, batters, bowlers, openers = walk_innings(innings_data)

        # INSERT innings row
        cur.execute("""
            INSERT INTO innings
                (match_id, innings_num, is_super_over, batting_team_id,
                 total_runs, total_wickets,
                 pp_runs, pp_wickets, middle_runs, middle_wickets, death_runs, death_wickets)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (match_id, innings_num) DO UPDATE SET
                total_runs = EXCLUDED.total_runs,
                total_wickets = EXCLUDED.total_wickets,
                pp_runs = EXCLUDED.pp_runs, pp_wickets = EXCLUDED.pp_wickets,
                middle_runs = EXCLUDED.middle_runs, middle_wickets = EXCLUDED.middle_wickets,
                death_runs = EXCLUDED.death_runs, death_wickets = EXCLUDED.death_wickets
            RETURNING innings_id
        """, (
            match_id, innings_num, is_super_over, batting_team_id,
            totals["total_runs"], totals["total_wickets"],
            totals["pp_runs"], totals["pp_wickets"],
            totals["middle_runs"], totals["middle_wickets"],
            totals["death_runs"], totals["death_wickets"],
        ))
        innings_id = cur.fetchone()["innings_id"]
        counters["innings"] += 1

        # INSERT player_innings_stats (batters)
        batter_rows = []
        for pname, s in batters.items():
            pid = _lookup(cur, "players", "player_id", "name", pname)
            if pid is None:
                warn_log.append(f"  WARN match {match_id} innings {innings_num}: batter '{pname}' not in players")
                continue
            batter_rows.append((
                match_id, innings_id, pid, batting_team_id,
                pname in openers,
                s["runs"], s["balls"], s["fours"], s["sixes"],
                s["pp_runs"], s["pp_balls"],
                s["middle_runs"], s["middle_balls"],
                s["death_runs"], s["death_balls"],
                s["dismissal_kind"],
            ))

        if batter_rows:
            execute_values(cur, """
                INSERT INTO player_innings_stats
                    (match_id, innings_id, player_id, batting_team_id, is_opener,
                     runs, balls, fours, sixes,
                     pp_runs, pp_balls, middle_runs, middle_balls, death_runs, death_balls,
                     dismissal_kind)
                VALUES %s
                ON CONFLICT (innings_id, player_id) DO NOTHING
            """, batter_rows)
            counters["batter_rows"] += len(batter_rows)

        # INSERT player_bowling_stats (bowlers)
        bowler_rows = []
        for pname, s in bowlers.items():
            pid = _lookup(cur, "players", "player_id", "name", pname)
            if pid is None:
                warn_log.append(f"  WARN match {match_id} innings {innings_num}: bowler '{pname}' not in players")
                continue
            bowler_rows.append((
                match_id, innings_id, pid, bowling_team_id,
                s["legal_balls"], s["runs_conceded"], s["wickets"],
                s["pp_balls"], s["pp_runs"], s["pp_wickets"],
                s["middle_balls"], s["middle_runs"], s["middle_wickets"],
                s["death_balls"], s["death_runs"], s["death_wickets"],
            ))

        if bowler_rows:
            execute_values(cur, """
                INSERT INTO player_bowling_stats
                    (match_id, innings_id, player_id, bowling_team_id,
                     legal_balls, runs_conceded, wickets,
                     pp_balls, pp_runs, pp_wickets,
                     middle_balls, middle_runs, middle_wickets,
                     death_balls, death_runs, death_wickets)
                VALUES %s
                ON CONFLICT (innings_id, player_id) DO NOTHING
            """, bowler_rows)
            counters["bowler_rows"] += len(bowler_rows)

    return counters


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def run(season_filter: str | None = None):
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur  = conn.cursor()

    print("── Migrating schema …")
    migrate_schema(cur)
    conn.commit()
    print("   done\n")

    # Fetch matches for the target season(s)
    if season_filter:
        cur.execute(
            "SELECT match_id, data FROM matches WHERE data->'info'->>'season' = %s ORDER BY match_id",
            (season_filter,)
        )
    else:
        cur.execute("SELECT match_id, data FROM matches ORDER BY match_id")

    rows = cur.fetchall()
    print(f"── Processing {len(rows)} matches (season={season_filter or 'ALL'}) …\n")

    total_innings = total_batters = total_bowlers = 0
    warn_log: list[str] = []

    for i, row in enumerate(rows, 1):
        counters = etl_match(cur, row["match_id"], row["data"], warn_log)
        total_innings += counters["innings"]
        total_batters += counters["batter_rows"]
        total_bowlers += counters["bowler_rows"]
        if i % 10 == 0 or i == len(rows):
            print(f"  [{i:>4}/{len(rows)}]  innings={total_innings}  batting_rows={total_batters}  bowling_rows={total_bowlers}")
        conn.commit()

    print(f"\n── Summary ──")
    print(f"  Matches processed : {len(rows)}")
    print(f"  Innings inserted  : {total_innings}")
    print(f"  Batter rows       : {total_batters}")
    print(f"  Bowler rows       : {total_bowlers}")

    if warn_log:
        print(f"\n── Warnings ({len(warn_log)}) ──")
        for w in warn_log[:50]:
            print(w)
        if len(warn_log) > 50:
            print(f"  … and {len(warn_log) - 50} more")
    else:
        print("\n  No warnings.")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AskAboutIPL ETL — populate fact tables from match JSONB")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--season", metavar="YEAR", help="Process one season, e.g. 2026 or 2007/08")
    group.add_argument("--all",    action="store_true", help="Process all seasons")
    args = parser.parse_args()

    run(season_filter=None if args.all else args.season)
