# AskAboutIPL — ETL Design

Reads the 1243 raw match JSONs already in `matches.data` (JSONB)  
and populates the relational schema so every stat query is a simple SQL JOIN.

---

## What's Already Done

```
matches          1243 rows  raw JSONB — source of truth
seasons            19 rows  year, edition_num, start_year, end_year, champion
teams              19 rows  name, city, franchise_name, active
venues             38 rows  name, city
players           818 rows  name, registry_id
```

---

## Tables to Build (ETL target)

```
matches_meta      FK columns on matches (season_id, venue_id, team1_id, team2_id, winner_id ...)
innings           one row per innings per match
player_innings_stats   one row per batter per innings
player_bowling_stats   one row per bowler per innings
player_team_season     which player played for which team in which season
```

---

## Schema

### matches_meta (extends the existing matches table)

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
  ADD COLUMN IF NOT EXISTS stage          TEXT,
  ADD COLUMN IF NOT EXISTS win_by_runs    INT,
  ADD COLUMN IF NOT EXISTS win_by_wickets INT;
```

Source fields:
```
info.season          → season_id
info.venue           → venue_id   (use VENUE_ALIASES to resolve)
info.teams[0]        → team1_id
info.teams[1]        → team2_id
info.outcome.winner  → winner_id
info.toss.winner     → toss_winner_id
info.toss.decision   → toss_decision
info.dates[0]        → match_date
info.event.stage     → stage
info.outcome.by.runs        → win_by_runs
info.outcome.by.wickets     → win_by_wickets
```

---

### innings

```sql
CREATE TABLE IF NOT EXISTS innings (
  innings_id      SERIAL PRIMARY KEY,
  match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
  innings_num     INT     NOT NULL,   -- 1 or 2
  batting_team_id INT     NOT NULL REFERENCES teams(team_id),
  total_runs      INT,
  total_wickets   INT,
  pp_runs         INT,    -- overs 0-5
  pp_wickets      INT,
  middle_runs     INT,    -- overs 6-15
  middle_wickets  INT,
  death_runs      INT,    -- overs 16-19
  death_wickets   INT,
  UNIQUE (match_id, innings_num)
);
```

Source: walk `innings[n].overs[].deliveries[]`, bucket by over number.

---

### player_innings_stats

```sql
CREATE TABLE IF NOT EXISTS player_innings_stats (
  id              SERIAL PRIMARY KEY,
  match_id        BIGINT  NOT NULL REFERENCES matches(match_id),
  innings_id      INT     NOT NULL REFERENCES innings(innings_id),
  player_id       INT     NOT NULL REFERENCES players(player_id),
  batting_team_id INT     NOT NULL REFERENCES teams(team_id),
  is_opener       BOOLEAN NOT NULL DEFAULT FALSE,
  runs            INT     NOT NULL DEFAULT 0,
  balls           INT     NOT NULL DEFAULT 0,
  fours           INT     NOT NULL DEFAULT 0,
  sixes           INT     NOT NULL DEFAULT 0,
  pp_runs         INT     NOT NULL DEFAULT 0,   -- powerplay runs
  pp_balls        INT     NOT NULL DEFAULT 0,
  middle_runs     INT     NOT NULL DEFAULT 0,
  middle_balls    INT     NOT NULL DEFAULT 0,
  death_runs      INT     NOT NULL DEFAULT 0,   -- death over runs
  death_balls     INT     NOT NULL DEFAULT 0,
  dismissal_kind  TEXT,                         -- NULL = not out
  UNIQUE (innings_id, player_id)
);
```

**Opener detection:**
```
batter on over=0, delivery_index=0  → opener 1
non_striker on over=0, delivery_index=0 → opener 2
```

**Phase bucketing:**
```
over 0–5   → powerplay
over 6–15  → middle
over 16–19 → death
```

---

### player_bowling_stats

```sql
CREATE TABLE IF NOT EXISTS player_bowling_stats (
  id              SERIAL PRIMARY KEY,
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
);
```

**Legal ball:** delivery where `extras.wides` and `extras.noballs` are both absent.  
**Economy:** `(runs_conceded * 6.0) / legal_balls` — computed at query time, not stored.

---

### player_team_season

```sql
CREATE TABLE IF NOT EXISTS player_team_season (
  player_id  INT  NOT NULL REFERENCES players(player_id),
  team_id    INT  NOT NULL REFERENCES teams(team_id),
  season_id  INT  NOT NULL REFERENCES seasons(season_id),
  PRIMARY KEY (player_id, team_id, season_id)
);
```

Source: `info.players` — squad dict per team per match.  
Insert once per (player, team, season) combination — `ON CONFLICT DO NOTHING`.

---

## ETL Flow (per match)

```
For each match in matches:

  1. RESOLVE FKs
     season_id  ← SELECT season_id FROM seasons WHERE year = info.season
     venue_id   ← SELECT venue_id FROM venues WHERE name = VENUE_ALIASES.get(info.venue, info.venue)
     team1_id   ← SELECT team_id FROM teams WHERE name = info.teams[0]
     team2_id   ← SELECT team_id FROM teams WHERE name = info.teams[1]
     winner_id  ← SELECT team_id FROM teams WHERE name = info.outcome.winner  (NULL if no result)
     toss_id    ← SELECT team_id FROM teams WHERE name = info.toss.winner

  2. UPDATE matches SET (season_id, venue_id, team1_id, ...) WHERE match_id = X

  3. PLAYER_TEAM_SEASON
     for team_name, squad in info.players.items():
       for player_name in squad:
         INSERT INTO player_team_season (player_id, team_id, season_id)
         ON CONFLICT DO NOTHING

  4. For each innings[n]:

     a. WALK DELIVERIES — build accumulators:
        innings_totals   = {runs, wickets, pp_runs, pp_wickets, middle_*, death_*}
        batter_stats     = {player_id: {runs, balls, fours, sixes, pp_runs, pp_balls, ...}}
        bowler_stats     = {player_id: {legal_balls, runs_conceded, wickets, pp_*, death_*, ...}}
        openers          = (batter on first delivery, non_striker on first delivery)

     b. INSERT INTO innings → get innings_id

     c. INSERT INTO player_innings_stats for each batter
        set is_opener = player in openers

     d. INSERT INTO player_bowling_stats for each bowler
```

---

## Delivery Walk Logic

```python
phase = "pp" if over <= 5 else "middle" if over <= 15 else "death"

# Batter accumulation
batter[d.batter].runs        += d.runs.batter
batter[d.batter].balls       += 1  # all deliveries count as faced
batter[d.batter].fours       += 1 if d.runs.batter == 4
batter[d.batter].sixes       += 1 if d.runs.batter == 6
batter[d.batter][phase_runs] += d.runs.batter
batter[d.batter][phase_balls]+= 1

# Bowler accumulation
is_wide   = "wides"   in d.extras
is_noball = "noballs" in d.extras
bowler[d.bowler].runs_conceded   += d.runs.total
bowler[d.bowler].legal_balls     += 0 if (is_wide or is_noball) else 1
bowler[d.bowler][phase_runs]     += d.runs.total
bowler[d.bowler][phase_balls]    += 0 if (is_wide or is_noball) else 1

# Wickets
for w in d.wickets:
  innings_totals.wickets += 1
  batter[d.batter].dismissal_kind = w.kind
  if w.kind not in ("run out", "retired hurt", "obstructing the field"):
    bowler[d.bowler].wickets += 1
    bowler[d.bowler][phase_wickets] += 1
```

---

## Script Run Order

```
1. python db.py            # init_db() — creates all tables (already run)
2. python seed_entities.py # seasons, teams, venues, players (already run)
3. python normalize_entities.py  # franchise_name, city, active, venue dedup (already run)
4. python enrich_players.py      # registry_id on players (already run)
5. python enrich_schema.py       # seasons edition_num, players profile columns (already run)

6. python etl_matches.py   ← NEXT — populate matches FK cols + innings + stats tables
```

---

## Alias Lookups Needed in ETL

Both dicts live in `normalize_entities.py` and will be imported by `etl_matches.py`:

```python
from normalize_entities import VENUE_ALIASES, TEAM_META
```

**VENUE_ALIASES** — maps raw JSON venue name → canonical venue name in the venues table.  
**TEAM_META** — all 19 team names are exact matches; no aliasing needed for team lookup.

---

## Expected Row Counts After ETL

```
matches FK cols        1243 rows updated
innings               ~2486 rows  (2 innings per match, some matches have super overs)
player_innings_stats  ~30,000–35,000 rows  (~25 batters across 2 innings per match)
player_bowling_stats  ~25,000–30,000 rows  (~20 bowlers across 2 innings per match)
player_team_season    ~5,000–6,000 rows   (squad sizes ~22 × 2 teams × 19 seasons)
```

---

## Hard Queries This Unlocks

```sql
-- Players with 30+ in powerplay (Query A)
SELECT p.name, COUNT(*) as times
FROM player_innings_stats pis
JOIN players p USING (player_id)
WHERE pis.pp_runs >= 30
GROUP BY p.name ORDER BY times DESC;

-- Opener powerplay scores (Query B)
SELECT p.name, ROUND(AVG(pp_runs), 1) avg_pp, SUM(pp_runs) total_pp
FROM player_innings_stats
JOIN players p USING (player_id)
WHERE is_opener = TRUE
GROUP BY p.name ORDER BY avg_pp DESC;

-- Bowlers who took MI's first wicket most (Query C)
-- Needs a first_wicket_events table — add to ETL as a bonus table.

-- Death over economy top 10 (Query D)
SELECT p.name,
       SUM(death_runs) AS runs,
       SUM(death_balls) AS balls,
       ROUND(SUM(death_runs) * 6.0 / NULLIF(SUM(death_balls), 0), 2) AS economy
FROM player_bowling_stats pbs
JOIN players p USING (player_id)
GROUP BY p.name
HAVING SUM(death_balls) >= 60
ORDER BY economy ASC LIMIT 10;
```
