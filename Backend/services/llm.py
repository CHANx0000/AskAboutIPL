import json
import re

from groq import AsyncGroq
from config import GROQ_API_KEY, GROQ_MODEL, SYSTEM_PROMPT
from db import get_connection
from normalize_entities import VENUE_ALIASES

_client = AsyncGroq(api_key=GROQ_API_KEY)

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_ipl_match_details",
            "description": (
                "Fetch IPL match details from the database. "
                "Use any combination of match_date, teams, stage_name, or year. "
                "At least one parameter should be provided."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "match_date": {
                        "type": "string",
                        "description": "Date of the match in YYYY-MM-DD format",
                    },
                    "teams": {
                        "type": "string",
                        "description": "Team names, e.g. 'Mumbai Indians vs Chennai Super Kings' or just 'Mumbai Indians'",
                    },
                    "stage_name": {
                        "type": "string",
                        "description": "Tournament stage, e.g. 'Final', 'Qualifier 1', 'Eliminator'",
                    },
                    "year": {
                        "type": "integer",
                        "description": "IPL season year, e.g. 2023",
                    },
                },
                "required": [],
            },
        },
    }
]


def _compute_innings_totals(innings_data: list) -> list[str]:
    summaries = []
    for innings in innings_data:
        team = innings.get("team", "Unknown")
        runs = 0
        wickets = 0
        for over in innings.get("overs", []):
            for delivery in over.get("deliveries", []):
                runs += delivery.get("runs", {}).get("total", 0)
                if "wickets" in delivery:
                    wickets += len(delivery["wickets"])
        summaries.append(f"{team}: {runs}/{wickets}")
    return summaries


def get_ipl_match_details(
    match_date: str = None,
    teams: str = None,
    stage_name: str = None,
    year: int = None,
) -> str:
    print()
    print(f"[5] TOOL | get_ipl_match_details() — date={match_date!r}  teams={teams!r}  stage={stage_name!r}  year={year!r}")
    print()

    # ── Build WHERE clause dynamically ───────────────────────────────────────
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

    print(f"    SQL : {sql}")
    print(f"    params : {params}")

    # ── Execute ───────────────────────────────────────────────────────────────
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

    # ── Format results ────────────────────────────────────────────────────────
    results: list[str] = []
    for row in rows:
        data = row["data"]
        info = data.get("info", {})

        dates_str   = ", ".join(info.get("dates", []))
        teams_str   = " vs ".join(info.get("teams", []))
        raw_venue   = info.get("venue", "Unknown venue")
        venue       = VENUE_ALIASES.get(raw_venue, raw_venue)
        season      = info.get("season", "Unknown")
        stage       = info.get("event", {}).get("stage", "")
        toss        = info.get("toss", {})
        outcome     = info.get("outcome", {})
        winner      = outcome.get("winner", "Unknown")
        by          = outcome.get("by", {})
        method      = outcome.get("method", "")
        pom         = ", ".join(info.get("player_of_match", []))

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

    result_str = "\n\n---\n\n".join(results)
    print(f"[6] TOOL RESULT →\n{result_str}\n")
    return result_str


async def chat_completion(message: str, history: list[dict]) -> str:
    """Send a message to the Groq LLM and return the assistant reply."""

    print()
    print(f"[3] BACKEND → LLM SERVICE | chat_completion() called")
    print(f"    message  : '{message}'")
    print(f"    history  : {len(history)} previous turn(s)")
    print()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *history,
        {"role": "user", "content": message},
    ]

    print()
    print(f"[4] LLM SERVICE → GROQ API | First request")
    print(f"    model : {GROQ_MODEL}  |  tools : {[t['function']['name'] for t in tools]}")
    print()

    response = await _client.chat.completions.create(
        model=GROQ_MODEL,
        messages=messages,
        max_tokens=1024,
        temperature=0.7,
        tools=tools,
        tool_choice="auto",
    )

    assistant_message = response.choices[0].message

    # ── Tool-call path ────────────────────────────────────────────────────────
    if assistant_message.tool_calls:
        print()
        print(f"[4b] Model requested tools: {[tc.function.name for tc in assistant_message.tool_calls]}")
        print()

        messages.append(assistant_message)

        for tool_call in assistant_message.tool_calls:
            args = json.loads(tool_call.function.arguments)
            print(f"[4c] Dispatching '{tool_call.function.name}' with args: {args}")

            if tool_call.function.name == "get_ipl_match_details":
                result = get_ipl_match_details(
                    match_date=args.get("match_date"),
                    teams=args.get("teams"),
                    stage_name=args.get("stage_name"),
                    year=args.get("year"),
                )
            else:
                result = f"Unknown tool: {tool_call.function.name}"

            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })

        print()
        print(f"[7] LLM SERVICE → GROQ API | Second request with tool result ({len(messages)} msgs)")
        print()

        final = await _client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            max_tokens=1024,
            temperature=0.7,
        )

        final_content = final.choices[0].message.content
        print(f"[8] Final reply: '{final_content}'")
        return final_content

    # ── Direct reply path ─────────────────────────────────────────────────────
    print(f"[4b] Direct reply: '{assistant_message.content}'")
    return assistant_message.content
