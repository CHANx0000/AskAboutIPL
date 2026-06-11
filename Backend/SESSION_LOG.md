# AskAboutIPL — Session Log (ETL + Tool Calling)

Covers two sessions: ETL pipeline build (all seasons) and LLM tool expansion.

---

## 1. ETL — Populate Fact Tables

### Schema migration (matches table)

```sql
ALTER TABLE matches
  ADD COLUMN IF NOT EXISTS season_id       INT  REFERENCES seasons(season_id),
  ADD COLUMN IF NOT EXISTS venue_id        INT  REFERENCES venues(venue_id),
  ADD COLUMN IF NOT EXISTS team1_id        INT  REFERENCES teams(team_id),
  ADD COLUMN IF NOT EXISTS team2_id        INT  REFERENCES teams(team_id),
  ADD COLUMN IF NOT EXISTS winner_id       INT  REFERENCES teams(team_id),
  ADD COLUMN IF NOT EXISTS toss_winner_id  INT  REFERENCES teams(team_id),
  ADD COLUMN IF NOT EXISTS toss_decision   TEXT,
  ADD COLUMN IF NOT EXISTS match_date      DATE,
  ADD COLUMN IF NOT EXISTS stage           TEXT,
  ADD COLUMN IF NOT EXISTS win_by_runs     INT,
  ADD COLUMN IF NOT EXISTS win_by_wickets  INT,
  ADD COLUMN IF NOT EXISTS dl_method       BOOLEAN DEFAULT FALSE;
```

### Fact tables created

```sql
CREATE TABLE IF NOT EXISTS innings (
    innings_id      SERIAL PRIMARY KEY,
    match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
    innings_num     INT     NOT NULL,
    is_super_over   BOOLEAN NOT NULL DEFAULT FALSE,
    batting_team_id INT     NOT NULL REFERENCES teams(team_id),
    total_runs      INT, total_wickets INT,
    pp_runs INT, pp_wickets INT,
    middle_runs INT, middle_wickets INT,
    death_runs INT, death_wickets INT,
    UNIQUE (match_id, innings_num)
);

CREATE TABLE IF NOT EXISTS player_innings_stats (
    id              SERIAL PRIMARY KEY,
    match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
    innings_id      INT     NOT NULL REFERENCES innings(innings_id),
    player_id       INT     NOT NULL REFERENCES players(player_id),
    batting_team_id INT     NOT NULL REFERENCES teams(team_id),
    is_opener       BOOLEAN NOT NULL DEFAULT FALSE,
    runs INT, balls INT, fours INT, sixes INT,
    pp_runs INT, pp_balls INT,
    middle_runs INT, middle_balls INT,
    death_runs INT, death_balls INT,
    dismissal_kind  TEXT,
    UNIQUE (innings_id, player_id)
);

CREATE TABLE IF NOT EXISTS player_bowling_stats (
    id              SERIAL PRIMARY KEY,
    match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
    innings_id      INT     NOT NULL REFERENCES innings(innings_id),
    player_id       INT     NOT NULL REFERENCES players(player_id),
    bowling_team_id INT     NOT NULL REFERENCES teams(team_id),
    legal_balls INT, runs_conceded INT, wickets INT,
    pp_balls INT, pp_runs INT, pp_wickets INT,
    middle_balls INT, middle_runs INT, middle_wickets INT,
    death_balls INT, death_runs INT, death_wickets INT,
    UNIQUE (innings_id, player_id)
);

CREATE TABLE IF NOT EXISTS player_team_season (
    player_id  INT  NOT NULL REFERENCES players(player_id),
    team_id    INT  NOT NULL REFERENCES teams(team_id),
    season_id  INT  NOT NULL REFERENCES seasons(season_id),
    PRIMARY KEY (player_id, team_id, season_id)
);
```

### Phase boundaries (0-indexed over numbers)

```python
def phase(over: int) -> str:
    if over <= 5:   return "pp"      # Powerplay  overs 1-6
    if over <= 14:  return "middle"  # Middle     overs 7-15
    return "death"                   # Death      overs 16-20
```

### Legal ball / batter ball rule

```python
is_wide   = "wides"   in extras
is_noball = "noballs" in extras

# Bowler: legal ball if NOT wide and NOT no-ball
bowler.legal_balls += 0 if (is_wide or is_noball) else 1

# Batter: counts a ball faced if NOT a wide
batter.balls += 0 if is_wide else 1
```

### Wicket attribution

```python
NON_BOWLER_WICKETS = {"run out", "retired hurt", "obstructing the field", "retired out"}

for w in delivery.wickets:
    innings_totals.wickets += 1
    batter.dismissal_kind = w.kind
    if w.kind not in NON_BOWLER_WICKETS:
        bowler.wickets += 1
```

### ETL run commands

```bash
# Single season (validate first)
python etl_matches.py --season 2026

# All seasons
python etl_matches.py --all
```

### ETL results — all 1243 matches

| Table | Rows |
|-------|------|
| innings | 2,514 (28 super overs detected) |
| player_innings_stats | 18,842 |
| player_bowling_stats | 14,734 |
| player_team_season | 3,357 |

Zero warnings across all seasons.

---

## 2. Validation Queries

### Table row counts

```sql
SELECT COUNT(*) FROM innings;              -- 2514
SELECT COUNT(*) FROM player_innings_stats; -- 18842
SELECT COUNT(*) FROM player_bowling_stats; -- 14734
SELECT COUNT(*) FROM player_team_season;   -- 3357
```

### Query A — Most innings with 30+ powerplay runs

```sql
SELECT p.name, COUNT(*) AS times
FROM player_innings_stats pis
JOIN players p USING (player_id)
WHERE pis.pp_runs >= 30
GROUP BY p.name ORDER BY times DESC LIMIT 10;
-- DA Warner 40x, V Kohli 28x, YBK Jaiswal 24x, CH Gayle 24x ...
```

### Query B — Openers by average powerplay runs (min 20 innings)

```sql
SELECT p.name, ROUND(AVG(pp_runs)::numeric, 1) avg_pp, SUM(pp_runs) total_pp
FROM player_innings_stats
JOIN players p USING (player_id)
WHERE is_opener = TRUE
GROUP BY p.name
HAVING COUNT(*) >= 20
ORDER BY avg_pp DESC LIMIT 10;
-- V Suryavanshi 28.9, B Sai Sudharsan 23.6, MR Marsh 23.5 ...
```

### Query C — Death over economy (min 60 legal balls)

```sql
SELECT p.name,
       SUM(death_runs) AS runs,
       SUM(death_balls) AS balls,
       ROUND(SUM(death_runs) * 6.0 / NULLIF(SUM(death_balls), 0), 2) AS economy
FROM player_bowling_stats pbs
JOIN players p USING (player_id)
GROUP BY p.name
HAVING SUM(death_balls) >= 60
ORDER BY economy ASC LIMIT 10;
-- SP Narine 7.41 (1105 balls), DE Bollinger 7.62 ...
```

---

## 3. LLM Tool Calling

### Tool 1 — get_ipl_match_details (existing, updated)

Returns: date, venue, teams, toss, scorecard total, result, Player of the Match.

```python
get_ipl_match_details(match_date, teams, stage_name, year)
# Example: get_ipl_match_details(stage_name="Final", year=2025)
```

### Tool 2 — get_player_stats (new)

Player name resolved via three lookups (short name, full name in people_registry, aliases in people_names):

```sql
SELECT p.player_id, p.name
FROM players p
WHERE p.name ILIKE %s
   OR p.registry_id IN (SELECT identifier FROM people_registry WHERE name ILIKE %s)
   OR p.registry_id IN (SELECT identifier FROM people_names   WHERE name ILIKE %s)
ORDER BY LENGTH(p.name) LIMIT 3;
```

Batting stats query (super overs excluded):

```sql
SELECT
    COUNT(DISTINCT pis.match_id)                                           AS matches,
    COUNT(*)                                                                AS innings,
    COALESCE(SUM(pis.runs), 0)                                             AS runs,
    ROUND(SUM(pis.runs)::numeric /
          NULLIF(COUNT(*) FILTER (WHERE pis.dismissal_kind IS NOT NULL), 0), 2) AS batting_avg,
    ROUND(SUM(pis.runs) * 100.0 / NULLIF(SUM(pis.balls), 0), 2)           AS strike_rate,
    COUNT(*) FILTER (WHERE pis.runs >= 50 AND pis.runs < 100)              AS fifties,
    COUNT(*) FILTER (WHERE pis.runs >= 100)                                AS hundreds,
    MAX(pis.runs)                                                           AS high_score,
    COALESCE(SUM(pis.fours), 0)                                            AS fours,
    COALESCE(SUM(pis.sixes), 0)                                            AS sixes,
    COALESCE(SUM(pis.pp_runs), 0)   AS pp_runs,
    COALESCE(SUM(pis.middle_runs), 0) AS mid_runs,
    COALESCE(SUM(pis.death_runs), 0)  AS death_runs
FROM player_innings_stats pis
JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
-- optional: JOIN matches m ... JOIN seasons s ... WHERE s.year = %s
WHERE pis.player_id = %s;
```

Bowling stats query (super overs excluded):

```sql
SELECT
    COUNT(DISTINCT pbs.match_id)                                             AS matches,
    COALESCE(SUM(pbs.legal_balls), 0)                                        AS balls,
    COALESCE(SUM(pbs.runs_conceded), 0)                                      AS runs_conceded,
    COALESCE(SUM(pbs.wickets), 0)                                            AS wickets,
    ROUND(SUM(pbs.runs_conceded)::numeric / NULLIF(SUM(pbs.wickets), 0), 2) AS bowling_avg,
    ROUND(SUM(pbs.runs_conceded) * 6.0  / NULLIF(SUM(pbs.legal_balls), 0), 2) AS economy,
    ROUND(SUM(pbs.legal_balls)::numeric  / NULLIF(SUM(pbs.wickets), 0), 2)  AS bowling_sr
FROM player_bowling_stats pbs
JOIN innings i ON i.innings_id = pbs.innings_id AND i.is_super_over = FALSE
WHERE pbs.player_id = %s;
```

### Tool 3 — get_season_leaders (new)

```python
get_season_leaders(category, season=None, top_n=10, role=None)
# category: "runs" | "wickets" | "economy" | "batting_avg" | "strike_rate" | "sixes" | "fours"
# role: "openers" (batting only)
```

Runs leaderboard example:

```sql
SELECT p.name, SUM(pis.runs) AS value,
       COUNT(DISTINCT pis.match_id) AS matches,
       ROUND(SUM(pis.runs)::numeric /
             NULLIF(COUNT(*) FILTER (WHERE pis.dismissal_kind IS NOT NULL), 0), 2) AS avg,
       ROUND(SUM(pis.runs) * 100.0 / NULLIF(SUM(pis.balls), 0), 2) AS sr,
       MAX(pis.runs) AS hs
FROM player_innings_stats pis
JOIN players p USING (player_id)
JOIN innings i ON i.innings_id = pis.innings_id AND i.is_super_over = FALSE
-- optional: JOIN matches m ... JOIN seasons s ... WHERE s.year = %s
WHERE TRUE
GROUP BY p.name
HAVING SUM(pis.runs) > 0
ORDER BY value DESC LIMIT %s;
```

Minimum thresholds:
- `economy` / `batting_avg` — min 60 legal balls / min 10 innings
- `strike_rate` — min 100 balls

---

## 4. Issues Found and Fixed

### Issue 1 — "ipl final 25" not understood
**Cause:** LLM didn't infer 2-digit year `25` → `2025`.  
**Fix:** System prompt rule: `"25" = 2025, "24" = 2024` etc.; NEVER ask to clarify.

### Issue 2 — "more details" hallucinated scorecards
**Cause:** System prompt grounding rules don't hold across follow-up turns.  
**Fix:** Appended grounding note **inside every tool result** — LLM sees it at generation time.

```
[SYSTEM NOTE — NOT FOR USER]
The data above is the COMPLETE factual record returned by the database.
Do NOT add, infer, or fabricate any information not explicitly shown.
Only present what is in the tool result.
If the user asks for data not shown here, say it is not available.
```

### Issue 3 — Super over runs inflating career stats
**Cause:** `player_innings_stats` included super over deliveries.  
**Symptom:** Kohli career runs showed 9346 vs official 9336 (+10).  
**Fix:** All stat queries now join `innings` with `AND i.is_super_over = FALSE`.

**Before / After — V Kohli career:**

| Stat | Before | After | Official |
|------|--------|-------|----------|
| Runs | 9346 | **9336** | 9336 ✅ |
| SR | 134.86 | **134.80** | 134.80 ✅ |
| 50s/100s | 68/9 | 68/9 | 68/9 ✅ |
| HS | 113 | 113 | 113 ✅ |

Remaining gap: 8 matches missing from Cricsheet dataset (coverage, not a code issue).

---

## 5. System Prompt (current)

```
You are AskAboutIPL...

YEAR INFERENCE RULES:
- 2-digit year abbreviations: "25" = 2025, "24" = 2024 etc.
- "latest", "most recent", "current" = year=2026
- NEVER ask to clarify the year — infer and call the tool

TOOL SELECTION:
1. get_ipl_match_details  → who won, toss, venue, scorecard total, POM
2. get_player_stats       → player runs/wickets/average/economy/50s/100s
3. get_season_leaders     → top scorers, most wickets, best economy leaderboards

STRICT OUTPUT RULES:
- ONLY use facts from tool result
- NEVER invent player scores, figures, or partnerships
- NEVER split a total score (190/9) into individual contributions
```

---

## 6. File Reference

| File | Purpose |
|------|---------|
| `etl_matches.py` | Main ETL — run with `--season YEAR` or `--all` |
| `ETL_DESIGN.md` | Full schema DDL and ETL flow spec |
| `ETL_SEASON.md` | Per-season run log (issues, edge cases, results) |
| `services/llm.py` | LLM chat completion + 3 tool implementations |
| `normalize_entities.py` | `VENUE_ALIASES`, `TEAM_META` used by ETL and LLM |
