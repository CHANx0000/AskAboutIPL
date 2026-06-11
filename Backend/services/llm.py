import json
import re

from groq import AsyncGroq
from config import GROQ_API_KEY, GROQ_MODEL, SYSTEM_PROMPT
from db import get_connection
from normalize_entities import VENUE_ALIASES

_client = AsyncGroq(api_key=GROQ_API_KEY)

# ─────────────────────────────────────────────────────────────────────────────
# Tool definitions
# ─────────────────────────────────────────────────────────────────────────────

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_ipl_match_details",
            "description": (
                "Fetch IPL match details: date, venue, teams, toss, scorecard total, result, Player of the Match. "
                "Use for questions about a specific match. "
                "Use any combination of match_date, teams, stage_name, or year."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "match_date": {"type": "string", "description": "Date in YYYY-MM-DD format"},
                    "teams": {"type": "string", "description": "Team names, e.g. 'Mumbai Indians vs Chennai Super Kings'"},
                    "stage_name": {"type": "string", "description": "Stage name, e.g. 'Final', 'Qualifier 1', 'Eliminator'"},
                    "year": {"type": "integer", "description": "Season year, e.g. 2025"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_player_stats",
            "description": (
                "Fetch batting and bowling statistics for a specific player across their IPL career or a single season. "
                "Use for questions like: 'How many runs has Kohli scored?', 'What is Bumrah's wickets tally?', "
                "'Rohit Sharma IPL stats', 'Dhoni average', 'Warner 2016 season'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "player_name": {
                        "type": "string",
                        "description": "Player name or partial name, e.g. 'Kohli', 'Rohit Sharma', 'Bumrah', 'Dhoni'",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Filter to a single IPL season year, e.g. 2024. Omit for career totals.",
                    },
                },
                "required": ["player_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_season_leaders",
            "description": (
                "Fetch a ranked leaderboard for a stat category across a season or all-time. "
                "Use for questions like: 'Top run scorers in 2024', 'Most wickets all time', "
                "'Best economy bowlers', 'Most sixes in IPL', 'Highest strike rate openers'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "enum": ["runs", "wickets", "economy", "batting_avg", "strike_rate", "sixes", "fours"],
                        "description": "Stat to rank by",
                    },
                    "season": {
                        "type": "integer",
                        "description": "Filter to a specific season year. Omit for all-time leaders.",
                    },
                    "top_n": {
                        "type": "integer",
                        "description": "How many entries to return (default 10, max 20)",
                    },
                    "role": {
                        "type": "string",
                        "enum": ["batters", "bowlers", "openers"],
                        "description": "Restrict to openers only, or leave blank for all. Only relevant for batting stats.",
                    },
                },
                "required": ["category"],
            },
        },
    },
]


# ─────────────────────────────────────────────────────────────────────────────
# Helper: grounding footer appended to every tool result
# ─────────────────────────────────────────────────────────────────────────────

_GROUNDING_NOTE = (
    "\n\n[SYSTEM NOTE — NOT FOR USER]\n"
    "The data above is the COMPLETE factual record returned by the database. "
    "Do NOT add, infer, or fabricate any information not explicitly shown. "
    "Only present what is in the tool result. "
    "If the user asks for data not shown here, say it is not available."
)


# ─────────────────────────────────────────────────────────────────────────────
# Tool: get_ipl_match_details
# ─────────────────────────────────────────────────────────────────────────────

def _compute_innings_totals(innings_data: list) -> list[str]:
    summaries = []
    for innings in innings_data:
        team = innings.get("team", "Unknown")
        runs = wickets = 0
        for over in innings.get("overs", []):
            for d in over.get("deliveries", []):
                runs += d.get("runs", {}).get("total", 0)
                if "wickets" in d:
                    wickets += len(d["wickets"])
        summaries.append(f"{team}: {runs}/{wickets}")
    return summaries


def get_ipl_match_details(
    match_date: str = None,
    teams: str = None,
    stage_name: str = None,
    year: int = None,
) -> str:
    print(f"\n[5] TOOL | get_ipl_match_details() — date={match_date!r}  teams={teams!r}  stage={stage_name!r}  year={year!r}\n")

    conditions: list[str] = []
    params: list = []

    if match_date:
        conditions.append("data->'info'->'dates' @> %s::jsonb")
        params.append(json.dumps([match_date]))

    if teams:
        for team in re.split(r"\s+vs\s+", teams.strip(), flags=re.IGNORECASE):
            team = team.strip()
            if team:
                conditions.append("data->'info'->'teams' @> %s::jsonb")
                params.append(json.dumps([team]))

    if stage_name:
        conditions.append("data->'info'->'event'->>'stage' ILIKE %s")
        params.append(f"%{stage_name}%")

    if year:
        conditions.append("data->'info'->>'season' = %s")
        params.append(str(year))

    if not conditions:
        return "Please provide at least one filter: date, teams, stage, or year."

    where = " AND ".join(conditions)
    sql = f"SELECT match_id, data FROM matches WHERE {where} ORDER BY match_id LIMIT 5"
    print(f"    SQL : {sql}\n    params : {params}")

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                rows = cur.fetchall()
    except Exception as exc:
        print(f"    DB ERROR: {exc}")
        return f"Database error: {exc}"

    if not rows:
        return "No IPL matches found for the given criteria."

    results: list[str] = []
    for row in rows:
        data = row["data"]
        info = data.get("info", {})

        dates_str = ", ".join(info.get("dates", []))
        teams_str = " vs ".join(info.get("teams", []))
        raw_venue = info.get("venue", "Unknown venue")
        venue     = VENUE_ALIASES.get(raw_venue, raw_venue)
        season    = info.get("season", "Unknown")
        stage     = info.get("event", {}).get("stage", "")
        toss      = info.get("toss", {})
        outcome   = info.get("outcome", {})
        winner    = outcome.get("winner", "Unknown")
        by        = outcome.get("by", {})
        method    = outcome.get("method", "")
        pom       = ", ".join(info.get("player_of_match", []))

        by_str = ""
        if "runs" in by:
            by_str = f" by {by['runs']} runs"
        elif "wickets" in by:
            by_str = f" by {by['wickets']} wickets"
        if method:
            by_str += f" ({method})"

        innings_lines = _compute_innings_totals(data.get("innings", []))

        lines = [
            f"Match ID : {row['match_id']}",
            f"Date     : {dates_str}",
            f"Season   : {season}" + (f"  |  Stage: {stage}" if stage else ""),
            f"Teams    : {teams_str}",
            f"Venue    : {venue}",
            f"Toss     : {toss.get('winner', '')} won, chose to {toss.get('decision', '')}",
            f"Scores   : {' | '.join(innings_lines)}",
            f"Result   : {winner} won{by_str}",
        ]
        if pom:
            lines.append(f"Player of Match : {pom}")

        results.append("\n".join(lines))

    result_str = "\n\n---\n\n".join(results) + _GROUNDING_NOTE
    print(f"[6] TOOL RESULT →\n{result_str}\n")
    return result_str


# ─────────────────────────────────────────────────────────────────────────────
# Tool: get_player_stats
# ─────────────────────────────────────────────────────────────────────────────

def get_player_stats(player_name: str, season: int = None) -> str:
    print(f"\n[5] TOOL | get_player_stats() — player={player_name!r}  season={season!r}\n")

    search = f"%{player_name}%"
    season_join  = "JOIN matches m ON m.match_id = pis.match_id JOIN seasons s ON s.season_id = m.season_id" if season else ""
    season_where = "AND s.year = %s" if season else ""

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                # Resolve player — search by short name OR full name in people_registry
                cur.execute("""
                    SELECT p.player_id, p.name
                    FROM players p
                    WHERE p.name ILIKE %s
                       OR p.registry_id IN (
                           SELECT identifier FROM people_registry WHERE name ILIKE %s
                       )
                       OR p.registry_id IN (
                           SELECT identifier FROM people_names WHERE name ILIKE %s
                       )
                    ORDER BY LENGTH(p.name)
                    LIMIT 3
                """, (search, search, search))
                player_rows = cur.fetchall()

                if not player_rows:
                    return f"No player found matching '{player_name}'."

                # Use the best match (shortest name = most specific)
                player_id   = player_rows[0]["player_id"]
                player_name_db = player_rows[0]["name"]

                other_matches = [r["name"] for r in player_rows[1:]]
                header = f"Player : {player_name_db}"
                if other_matches:
                    header += f"  (also matched: {', '.join(other_matches)})"
                if season:
                    header += f"\nSeason : {season}"
                else:
                    header += "\nScope  : Career (all seasons)"

                # ── Batting stats ─────────────────────────────────────────────
                bat_params = [player_id]
                if season:
                    bat_params.append(str(season))

                cur.execute(f"""
                    SELECT
                        COUNT(DISTINCT pis.match_id)                                           AS matches,
                        COUNT(*)                                                                AS innings,
                        COALESCE(SUM(pis.runs), 0)                                             AS runs,
                        ROUND(SUM(pis.runs)::numeric /
                              NULLIF(COUNT(*) FILTER (WHERE pis.dismissal_kind IS NOT NULL), 0),
                              2)                                                                AS batting_avg,
                        ROUND(SUM(pis.runs) * 100.0 / NULLIF(SUM(pis.balls), 0), 2)           AS strike_rate,
                        COUNT(*) FILTER (WHERE pis.runs >= 50 AND pis.runs < 100)              AS fifties,
                        COUNT(*) FILTER (WHERE pis.runs >= 100)                                AS hundreds,
                        MAX(pis.runs)                                                           AS high_score,
                        COALESCE(SUM(pis.fours), 0)                                            AS fours,
                        COALESCE(SUM(pis.sixes), 0)                                            AS sixes,
                        COALESCE(SUM(pis.pp_runs), 0)                                          AS pp_runs,
                        COALESCE(SUM(pis.middle_runs), 0)                                      AS mid_runs,
                        COALESCE(SUM(pis.death_runs), 0)                                       AS death_runs
                    FROM player_innings_stats pis
                    JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
                    {season_join.replace('pis.match_id', 'pis.match_id')}
                    WHERE pis.player_id = %s
                    {season_where}
                """, bat_params)
                bat = cur.fetchone()

                # ── Bowling stats ─────────────────────────────────────────────
                bow_season_join  = "JOIN matches m ON m.match_id = pbs.match_id JOIN seasons s ON s.season_id = m.season_id" if season else ""
                bow_season_where = "AND s.year = %s" if season else ""
                bow_params = [player_id]
                if season:
                    bow_params.append(str(season))

                cur.execute(f"""
                    SELECT
                        COUNT(DISTINCT pbs.match_id)                                             AS matches,
                        COALESCE(SUM(pbs.legal_balls), 0)                                        AS balls,
                        COALESCE(SUM(pbs.runs_conceded), 0)                                      AS runs_conceded,
                        COALESCE(SUM(pbs.wickets), 0)                                            AS wickets,
                        ROUND(SUM(pbs.runs_conceded)::numeric /
                              NULLIF(SUM(pbs.wickets), 0), 2)                                    AS bowling_avg,
                        ROUND(SUM(pbs.runs_conceded) * 6.0 /
                              NULLIF(SUM(pbs.legal_balls), 0), 2)                                AS economy,
                        ROUND(SUM(pbs.legal_balls)::numeric /
                              NULLIF(SUM(pbs.wickets), 0), 2)                                    AS bowling_sr
                    FROM player_bowling_stats pbs
                    JOIN innings i ON i.innings_id = pbs.innings_id AND i.is_super_over = FALSE
                    {bow_season_join}
                    WHERE pbs.player_id = %s
                    {bow_season_where}
                """, bow_params)
                bow = cur.fetchone()

    except Exception as exc:
        print(f"    DB ERROR: {exc}")
        return f"Database error: {exc}"

    lines = [header, ""]

    # Batting
    if bat and bat["innings"] and bat["innings"] > 0:
        lines.append("── BATTING ──")
        lines.append(f"Matches   : {bat['matches']}   Innings : {bat['innings']}")
        lines.append(f"Runs      : {bat['runs']}   Avg : {bat['batting_avg'] or '—'}   SR : {bat['strike_rate'] or '—'}")
        lines.append(f"50s / 100s: {bat['fifties']} / {bat['hundreds']}   High Score : {bat['high_score']}")
        lines.append(f"4s / 6s   : {bat['fours']} / {bat['sixes']}")
        lines.append(f"Phase runs: PP={bat['pp_runs']}  Mid={bat['mid_runs']}  Death={bat['death_runs']}")
    else:
        lines.append("── BATTING ──\n  No batting data found.")

    lines.append("")

    # Bowling
    if bow and bow["balls"] and bow["balls"] > 0:
        lines.append("── BOWLING ──")
        lines.append(f"Matches   : {bow['matches']}   Balls : {bow['balls']}")
        lines.append(f"Wickets   : {bow['wickets']}   Avg : {bow['bowling_avg'] or '—'}   Economy : {bow['economy'] or '—'}   SR : {bow['bowling_sr'] or '—'}")
        lines.append(f"Runs given: {bow['runs_conceded']}")
    else:
        lines.append("── BOWLING ──\n  No bowling data found.")

    result_str = "\n".join(lines) + _GROUNDING_NOTE
    print(f"[6] TOOL RESULT →\n{result_str}\n")
    return result_str


# ─────────────────────────────────────────────────────────────────────────────
# Tool: get_season_leaders
# ─────────────────────────────────────────────────────────────────────────────

def get_season_leaders(
    category: str,
    season: int = None,
    top_n: int = 10,
    role: str = None,
) -> str:
    print(f"\n[5] TOOL | get_season_leaders() — category={category!r}  season={season!r}  top_n={top_n}  role={role!r}\n")

    top_n = min(int(top_n or 10), 20)
    season_label = str(season) if season else "All-time"

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:

                season_join_bat  = "JOIN matches m ON m.match_id = pis.match_id JOIN seasons s ON s.season_id = m.season_id" if season else ""
                season_where_bat = "AND s.year = %s" if season else ""
                season_join_bow  = "JOIN matches m ON m.match_id = pbs.match_id JOIN seasons s ON s.season_id = m.season_id" if season else ""
                season_where_bow = "AND s.year = %s" if season else ""

                opener_where = "AND pis.is_opener = TRUE" if role == "openers" else ""

                rows = []

                if category == "runs":
                    params = ([str(season)] if season else [])
                    cur.execute(f"""
                        SELECT p.name, SUM(pis.runs) AS value,
                               COUNT(DISTINCT pis.match_id) AS matches,
                               ROUND(SUM(pis.runs)::numeric /
                                     NULLIF(COUNT(*) FILTER (WHERE pis.dismissal_kind IS NOT NULL), 0), 2) AS avg,
                               ROUND(SUM(pis.runs) * 100.0 / NULLIF(SUM(pis.balls), 0), 2) AS sr,
                               MAX(pis.runs) AS hs
                        FROM player_innings_stats pis
                        JOIN players p USING (player_id)
                        JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
                        {season_join_bat}
                        WHERE TRUE {season_where_bat} {opener_where}
                        GROUP BY p.name
                        HAVING SUM(pis.runs) > 0
                        ORDER BY value DESC
                        LIMIT %s
                    """, params + [top_n])
                    rows = cur.fetchall()
                    header = f"Top {top_n} Run Scorers — {season_label}"
                    col_header = f"{'#':<4} {'Player':<28} {'Runs':>6} {'Avg':>7} {'SR':>7} {'HS':>5} {'M':>4}"
                    fmt = lambda i, r: f"{i:<4} {r['name']:<28} {r['value']:>6} {str(r['avg'] or '—'):>7} {str(r['sr'] or '—'):>7} {r['hs']:>5} {r['matches']:>4}"

                elif category == "sixes":
                    params = ([str(season)] if season else [])
                    cur.execute(f"""
                        SELECT p.name, SUM(pis.sixes) AS value,
                               COUNT(DISTINCT pis.match_id) AS matches
                        FROM player_innings_stats pis
                        JOIN players p USING (player_id)
                        JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
                        {season_join_bat}
                        WHERE TRUE {season_where_bat} {opener_where}
                        GROUP BY p.name
                        HAVING SUM(pis.sixes) > 0
                        ORDER BY value DESC
                        LIMIT %s
                    """, params + [top_n])
                    rows = cur.fetchall()
                    header = f"Top {top_n} Six Hitters — {season_label}"
                    col_header = f"{'#':<4} {'Player':<28} {'6s':>6} {'Matches':>8}"
                    fmt = lambda i, r: f"{i:<4} {r['name']:<28} {r['value']:>6} {r['matches']:>8}"

                elif category == "fours":
                    params = ([str(season)] if season else [])
                    cur.execute(f"""
                        SELECT p.name, SUM(pis.fours) AS value,
                               COUNT(DISTINCT pis.match_id) AS matches
                        FROM player_innings_stats pis
                        JOIN players p USING (player_id)
                        JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
                        {season_join_bat}
                        WHERE TRUE {season_where_bat} {opener_where}
                        GROUP BY p.name
                        HAVING SUM(pis.fours) > 0
                        ORDER BY value DESC
                        LIMIT %s
                    """, params + [top_n])
                    rows = cur.fetchall()
                    header = f"Top {top_n} Four Hitters — {season_label}"
                    col_header = f"{'#':<4} {'Player':<28} {'4s':>6} {'Matches':>8}"
                    fmt = lambda i, r: f"{i:<4} {r['name']:<28} {r['value']:>6} {r['matches']:>8}"

                elif category == "batting_avg":
                    params = ([str(season)] if season else [])
                    cur.execute(f"""
                        SELECT p.name,
                               ROUND(SUM(pis.runs)::numeric /
                                     NULLIF(COUNT(*) FILTER (WHERE pis.dismissal_kind IS NOT NULL), 0), 2) AS value,
                               SUM(pis.runs) AS runs,
                               COUNT(*) AS innings,
                               COUNT(DISTINCT pis.match_id) AS matches
                        FROM player_innings_stats pis
                        JOIN players p USING (player_id)
                        JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
                        {season_join_bat}
                        WHERE TRUE {season_where_bat} {opener_where}
                        GROUP BY p.name
                        HAVING COUNT(*) >= 10
                        ORDER BY value DESC NULLS LAST
                        LIMIT %s
                    """, params + [top_n])
                    rows = cur.fetchall()
                    header = f"Top {top_n} Batting Averages — {season_label} (min 10 innings)"
                    col_header = f"{'#':<4} {'Player':<28} {'Avg':>7} {'Runs':>6} {'Inn':>5}"
                    fmt = lambda i, r: f"{i:<4} {r['name']:<28} {str(r['value'] or '—'):>7} {r['runs']:>6} {r['innings']:>5}"

                elif category == "strike_rate":
                    params = ([str(season)] if season else [])
                    cur.execute(f"""
                        SELECT p.name,
                               ROUND(SUM(pis.runs) * 100.0 / NULLIF(SUM(pis.balls), 0), 2) AS value,
                               SUM(pis.runs) AS runs,
                               COUNT(DISTINCT pis.match_id) AS matches
                        FROM player_innings_stats pis
                        JOIN players p USING (player_id)
                        JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
                        {season_join_bat}
                        WHERE TRUE {season_where_bat} {opener_where}
                        GROUP BY p.name
                        HAVING SUM(pis.balls) >= 100
                        ORDER BY value DESC NULLS LAST
                        LIMIT %s
                    """, params + [top_n])
                    rows = cur.fetchall()
                    header = f"Top {top_n} Strike Rates — {season_label} (min 100 balls)"
                    col_header = f"{'#':<4} {'Player':<28} {'SR':>7} {'Runs':>6} {'M':>4}"
                    fmt = lambda i, r: f"{i:<4} {r['name']:<28} {str(r['value'] or '—'):>7} {r['runs']:>6} {r['matches']:>4}"

                elif category == "wickets":
                    params = ([str(season)] if season else [])
                    cur.execute(f"""
                        SELECT p.name, SUM(pbs.wickets) AS value,
                               ROUND(SUM(pbs.runs_conceded) * 6.0 /
                                     NULLIF(SUM(pbs.legal_balls), 0), 2) AS economy,
                               ROUND(SUM(pbs.runs_conceded)::numeric /
                                     NULLIF(SUM(pbs.wickets), 0), 2) AS avg,
                               COUNT(DISTINCT pbs.match_id) AS matches
                        FROM player_bowling_stats pbs
                        JOIN players p USING (player_id)
                        JOIN innings i ON i.innings_id = pbs.innings_id AND i.is_super_over = FALSE
                        {season_join_bow}
                        WHERE TRUE {season_where_bow}
                        GROUP BY p.name
                        HAVING SUM(pbs.wickets) > 0
                        ORDER BY value DESC
                        LIMIT %s
                    """, params + [top_n])
                    rows = cur.fetchall()
                    header = f"Top {top_n} Wicket Takers — {season_label}"
                    col_header = f"{'#':<4} {'Player':<28} {'Wkts':>6} {'Avg':>7} {'Econ':>7} {'M':>4}"
                    fmt = lambda i, r: f"{i:<4} {r['name']:<28} {r['value']:>6} {str(r['avg'] or '—'):>7} {str(r['economy'] or '—'):>7} {r['matches']:>4}"

                elif category == "economy":
                    params = ([str(season)] if season else [])
                    cur.execute(f"""
                        SELECT p.name,
                               ROUND(SUM(pbs.runs_conceded) * 6.0 /
                                     NULLIF(SUM(pbs.legal_balls), 0), 2) AS value,
                               SUM(pbs.wickets) AS wickets,
                               SUM(pbs.legal_balls) AS balls,
                               COUNT(DISTINCT pbs.match_id) AS matches
                        FROM player_bowling_stats pbs
                        JOIN players p USING (player_id)
                        JOIN innings i ON i.innings_id = pbs.innings_id AND i.is_super_over = FALSE
                        {season_join_bow}
                        WHERE TRUE {season_where_bow}
                        GROUP BY p.name
                        HAVING SUM(pbs.legal_balls) >= 60
                        ORDER BY value ASC NULLS LAST
                        LIMIT %s
                    """, params + [top_n])
                    rows = cur.fetchall()
                    header = f"Best Economy Rates — {season_label} (min 60 legal balls)"
                    col_header = f"{'#':<4} {'Player':<28} {'Econ':>7} {'Wkts':>6} {'Balls':>6} {'M':>4}"
                    fmt = lambda i, r: f"{i:<4} {r['name']:<28} {str(r['value'] or '—'):>7} {r['wickets']:>6} {r['balls']:>6} {r['matches']:>4}"

                else:
                    return f"Unknown category: {category}. Use one of: runs, wickets, economy, batting_avg, strike_rate, sixes, fours."

    except Exception as exc:
        print(f"    DB ERROR: {exc}")
        return f"Database error: {exc}"

    if not rows:
        return f"No data found for category='{category}' season={season_label}."

    lines = [header, col_header, "─" * len(col_header)]
    for i, r in enumerate(rows, 1):
        lines.append(fmt(i, r))

    result_str = "\n".join(lines) + _GROUNDING_NOTE
    print(f"[6] TOOL RESULT →\n{result_str}\n")
    return result_str


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ─────────────────────────────────────────────────────────────────────────────

def _dispatch(name: str, args: dict) -> str:
    if name == "get_ipl_match_details":
        return get_ipl_match_details(
            match_date=args.get("match_date"),
            teams=args.get("teams"),
            stage_name=args.get("stage_name"),
            year=args.get("year"),
        )
    if name == "get_player_stats":
        return get_player_stats(
            player_name=args["player_name"],
            season=args.get("season"),
        )
    if name == "get_season_leaders":
        return get_season_leaders(
            category=args["category"],
            season=args.get("season"),
            top_n=args.get("top_n", 10),
            role=args.get("role"),
        )
    return f"Unknown tool: {name}"


# ─────────────────────────────────────────────────────────────────────────────
# LLM chat completion
# ─────────────────────────────────────────────────────────────────────────────

async def chat_completion(message: str, history: list[dict]) -> str:
    print(f"\n[3] BACKEND → LLM SERVICE | chat_completion() called")
    print(f"    message  : '{message}'")
    print(f"    history  : {len(history)} previous turn(s)\n")

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": message},
    ]

    print(f"\n[4] LLM SERVICE → GROQ API | First request")
    print(f"    model : {GROQ_MODEL}  |  tools : {[t['function']['name'] for t in tools]}\n")

    response = await _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
        tools=tools,
        tool_choice="auto",
    )

    assistant_message = response.choices[0].message

    if assistant_message.tool_calls:
        print(f"\n[4b] Model requested tools: {[tc.function.name for tc in assistant_message.tool_calls]}\n")
        messages.append(assistant_message)

        for tool_call in assistant_message.tool_calls:
            args = json.loads(tool_call.function.arguments)
            print(f"[4c] Dispatching '{tool_call.function.name}' with args: {args}")
            result = _dispatch(tool_call.function.name, args)
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

        print(f"\n[7] LLM SERVICE → GROQ API | Second request with tool result ({len(messages)} msgs)\n")

        final = await _client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0.7,
        )

        final_content = final.choices[0].message.content
        print(f"[8] Final reply: '{final_content}'")
        return final_content

    print(f"[4b] Direct reply: '{assistant_message.content}'")
    return assistant_message.content
