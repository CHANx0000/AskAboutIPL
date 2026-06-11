# AskAboutIPL — Query Design & Data Architecture

Brainstorming document.  
Goal: understand what query needs what data shape, and when to upgrade the technique.

---

## The Core Problem

All data right now is in **fundamental/raw form** — ball-by-ball JSON, one file per match.  
Every query that feels simple to a user ("how many runs did Kohli score?") is actually a full delivery scan under the hood.

The further you get from "who won match X" toward "who is the best powerplay batsman ever", the more pre-computation you need before the query even runs.

---

## Query Depth Framework

```
DEPTH 1 — Match facts           → Direct JSON field lookup
DEPTH 2 — Match scores          → Aggregate deliveries (one match)
DEPTH 3 — Player in a match     → Scan deliveries, filter by name
DEPTH 4 — Player in a season    → Scan deliveries across N matches
DEPTH 5 — Career / all-time     → Scan all 1243 matches
DEPTH 6 — Tactical / contextual → Intent, not just numbers
```

---

## Query Catalogue

### DEPTH 1 — Match facts ✅ (current SQL tool works)

| User Prompt | Data Path |
|---|---|
| Who won the 2026 Final? | `info.outcome.winner` |
| Where was the 2024 Final played? | `info.venue` |
| Who was POTM in the 2026 Q2? | `info.player_of_match` |
| Who won the toss in the 2023 Final? | `info.toss` |
| Which teams played on 2026-05-29? | `info.dates` + `info.teams` |
| What stage was the 2026 Eliminator? | `info.event.stage` |

**SQL shape:** `WHERE season = X AND stage ILIKE Y` → single row lookup.  
**Limitation:** Stage field is only filled for playoff matches; league matches have `stage = null`.

---

### DEPTH 2 — Match scores ✅ (current tool computes in Python)

| User Prompt | Computation |
|---|---|
| What was the score in the 2026 Final? | Sum `delivery.runs.total` per innings |
| How many wickets did GT lose in Q2? | Count `delivery.wickets` per innings |
| First innings total in 2026 Q1? | Same, filter `innings[0]` |
| By how many runs did CSK win the 2023 Final? | `outcome.by.runs` |

**SQL shape:** Unnest innings → overs → deliveries, GROUP BY team, SUM runs, COUNT wickets.  
**Limitation:** ~240 deliveries per match; fast for one match, slow if scanning many.

---

### DEPTH 3 — Player performance in a specific match ⚠️

> *"How many runs did V Kohli score in the 2026 IPL Final?"*  
> *"What were the bowling figures of Mohammed Siraj in the 2026 Q2?"*  
> *"Who scored the most runs in the 2026 Final?"*  
> *"Who took the most wickets in the 2026 Final?"*

**Current state:** Full match JSON (~77KB) is returned to the LLM. The LLM has to scan the delivery array mentally to compute the answer. This works for a single match but:
- Token cost is high
- LLM can miscount on large delivery arrays
- Not reliable for "who scored most" (requires ranking all batters)

**What's needed:** A `get_player_match_stats(match_id, player?)` SQL tool that pre-aggregates at query time:

```
For batter:  SUM(delivery.runs.batter) WHERE delivery.batter = player
For bowler:  COUNT(delivery.wickets), SUM(delivery.runs.total) WHERE delivery.bowler = player
             Calculate overs from legal ball count
```

**Still raw JSONB** — just moved the aggregation to SQL instead of LLM.

---

### DEPTH 4 — Contextual / positional queries 🔧

These are the hard ones the user raised. They require understanding *position in the innings*, not just player names.

---

#### Query A — Players who scored 30+ runs in a powerplay

> *"list of all players who scored 30 runs in the powerplay"*

**Powerplay = overs 0–5 (first 6 overs)**

**What the SQL must do:**
1. Unnest all 1243 matches → all innings → filter overs 0–5
2. Group by (batter, match, innings-team)
3. SUM batter runs in those overs
4. Filter HAVING SUM >= 30

**Complexity:** ~300K total deliveries across all matches. Full scan with JSONB unnesting.  
**Output:** List of (player, match, season, team, powerplay-runs, balls-faced)

**Limitation of raw JSONB:**  
No index on `ov->>'over'` or `delivery->>'batter'`. PostgreSQL must read every delivery of every match to answer this. On Supabase free tier this will be noticeably slow (5–15 seconds).

**What would make it fast:**  
A pre-computed `player_innings_stats` table where powerplay runs are already a column.

---

#### Query B — Opener scores in powerplay

> *"list of all openers score in powerplay"*

**Additional challenge beyond Query A:** You must first identify who the openers are.

**How to identify an opener:**  
The `batter` and `non_striker` on the very first delivery of an innings (over=0, delivery[0]) are the two openers.

**What the SQL must do:**
1. Find first delivery of every innings → extract batter + non_striker (opener1, opener2)
2. Run the powerplay aggregation (same as Query A)
3. JOIN: only keep rows where batter was opener1 or opener2 for that match/innings

**Extra complexity:** A player can be opener in some matches but bat lower in others (especially across seasons or when teams change playing XI). This query gives per-match opener scores, which is correct.

**Edge case:** If an opener is dismissed in the powerplay, the non-striker who came in is NOT an opener. The query correctly handles this because it checks if the *batter* was one of the original two, not every batter who faced in overs 0–5.

---

#### Query C — Bowlers who broke MI's first wicket most (last 5 seasons)

> *"list of bowlers who broke the first wicket most times for MI in last 5 seasons"*

**"First wicket" = first dismissal in an innings where Mumbai Indians are batting.**

**What the SQL must do:**
1. Filter innings where `inn.team = 'Mumbai Indians'`
2. Filter seasons 2022–2026
3. Find all deliveries with `delivery.wickets` exists
4. Among those, rank them by (over, delivery-position) within each match
5. Keep only rank = 1 (first wicket of the innings)
6. GROUP BY bowler, COUNT

**What makes this hard:**  
You need `ROW_NUMBER() OVER (PARTITION BY match_id ORDER BY over, delivery_position)` on a derived result from JSONB unnesting. That's a window function on top of an unnested JSON — no index helps here.

**Seasons format note:**  
Some seasons are stored as `"2007/08"`, `"2009/10"`. From 2011 onward they're `"2022"`, `"2023"` etc. Last 5 seasons = `'2022', '2023', '2024', '2025', '2026'`.

---

#### Query D — Top 10 bowlers with least runs in death overs

> *"Need a list of 10 players who gave less runs in death overs (last 4 overs)"*

**Death overs = overs 16–19 (last 4 overs of a 20-over innings)**

**What the SQL must do:**
1. Filter deliveries in overs 16–19
2. Group by bowler
3. SUM all runs (batter runs + extras)
4. COUNT legal balls (exclude wides and no-balls)
5. Calculate economy: (runs × 6) / legal_balls
6. Apply minimum threshold (e.g., 60+ legal balls = 10 overs min) to exclude small samples
7. ORDER BY economy ASC, LIMIT 10

**Why minimum threshold matters:**  
A bowler who bowled 1 death over with 0 runs would top the list. You need minimum overs to make the stat meaningful. 60 legal balls ≈ 10 overs is a reasonable IPL-career minimum.

**Output:** bowler, total_runs, overs_bowled, economy_rate

---

### DEPTH 5 — Career / season aggregates 🔧📚

| User Prompt | Challenge |
|---|---|
| Who scored most runs in IPL history? | Scan all deliveries, GROUP BY batter |
| Virat Kohli's IPL batting average | total_runs / dismissals across career |
| Orange Cap winner per season | Top batter per season |
| Which team won most titles? | Count Final wins per team |
| Head-to-head: MI vs CSK | All matches where both teams played |

**These are technically possible in SQL** but scan the full delivery table every time.  
The right fix is pre-computation (see below).

---

### DEPTH 6 — Tactical / narrative 📚 (needs RAG)

| User Prompt | Why SQL fails |
|---|---|
| Who is the best finisher in IPL? | "Best finisher" is a concept, not a column |
| Compare Kohli and Rohit as batsmen | Needs narrative, not just numbers |
| Why does CSK do well in finals? | Contextual, historical pattern |
| Which bowlers are best against left-handers? | Complex subgrouping + semantic framing |
| Tell me about Jasprit Bumrah's death bowling | Qualitative + statistical combined |

---

## Data Architecture Progression

### Stage 1 — Raw JSONB queries (where you are now)

```
User query → LLM extracts params → SQL on raw JSON → LLM formats answer
```

**Good for:** Depth 1–2  
**Breaks at:** Depth 3+ (slow, unreliable aggregation, token waste)

---

### Stage 2 — Pre-computed relational tables

Run a one-time ETL from the 1243 match JSONs into a proper schema:

```sql
player_innings_stats
  (match_id, batting_team, batter, runs, balls, fours, sixes,
   pp_runs, pp_balls, middle_runs, death_runs, dismissal_kind)

player_bowling_stats
  (match_id, bowling_team, bowler, overs, runs, wickets,
   pp_runs, pp_wickets, death_runs, death_wickets, economy)

innings_summary
  (match_id, innings_num, batting_team, total_runs, wickets,
   pp_score, pp_wickets, death_score, death_wickets)

season_player_stats
  (season, player, matches, runs, avg, sr, wickets, economy)
```

**Now Queries A–D become simple JOINs + GROUP BY.**  
No JSONB unnesting at query time.  
Response time: milliseconds instead of seconds.

**When to build this:** Before implementing the RAG layer. Clean, structured data makes embedding and retrieval far more accurate.

---

### Stage 3 — Semantic search (Vector / RAG)

**Use when:** The user's question expresses *intent or concept*, not a structured filter.

| Question type | Example | Technique |
|---|---|---|
| "Best X" | "Best death bowler in IPL" | Embed player stat summaries → rank by similarity to "death bowling expertise" |
| "Player profile" | "Tell me about Suryakumar Yadav's batting" | Pre-write career summaries → chunk → embed → retrieve |
| "Match narrative" | "Describe the 2023 IPL Final" | Pre-write match summaries → embed → retrieve |
| "Trend" | "How has powerplay scoring changed over the years?" | Pre-compute yearly stats → embed trend text |

**What to pre-compute and embed:**
```
per player:  "career_summary_{player}.txt"
             "season_{year}_{player}_stats.txt"

per match:   "match_{id}_summary.txt"
             (who batted well, key moments, match context)

per team:    "team_{name}_history.txt"
             (season-by-season performance, strengths)
```

**Vector DB options:** pgvector (already on Supabase — zero extra infra), Pinecone, Chroma  
**Embedding model:** OpenAI text-embedding-3-small, or a local model like `all-MiniLM-L6-v2`

---

### Stage 4 — Hybrid search (SQL filter + vector re-rank)

**Use when:** Question has both a *structured filter* and a *semantic intent*.

```
"Best death bowler from last 3 seasons"
  → SQL: filter season IN (2024, 2025, 2026), get candidate bowlers
  → Vector: re-rank candidates by similarity to "death bowling" profile
  → LLM: synthesise final answer

"Young batsmen who improved their powerplay game in 2025"
  → SQL: filter season=2025, age < 25
  → Vector: match against "powerplay improvement" concept
```

**This is the most powerful pattern for a sports chatbot.** It combines factual precision (SQL) with intent understanding (vector).

---

### Stage 5 — Knowledge Graph

**Use when:** Questions traverse *relationships between entities* across time.

```
Nodes:    Player, Team, Match, Season, Venue, Award
Edges:    played_for(player, team, season)
          played_in(player, match)
          dismissed(bowler, batter, match, over, kind)
          won_with(player, team, season)
          coach_of(person, team, season)
```

**Queries that need a graph:**

| Prompt | Why graph |
|---|---|
| Players who won IPL with 3 different teams | Traverse `won_with` edges, count distinct teams |
| Kohli's record against RR bowlers across career | `dismissed(bowler is from RR)` edge traversal |
| Ex-MI players now at RCB in 2026 | `played_for(MI, year<2025)` ∩ `played_for(RCB, 2026)` |
| Which coaches led multiple teams to titles? | `coach_of` → `won_with` join |
| Head-to-head: Bumrah vs Gayle (balls, dismissals) | Direct edge: `dismissed(Bumrah, Gayle)` |

**Graph DBs:** Neo4j, Amazon Neptune, or Postgres with recursive CTEs for simpler cases.  
**For learning:** Start with recursive SQL CTEs on the pre-computed relational tables (Stage 2) to simulate graph traversal before adding a dedicated graph DB.

---

## Summary: What to Build in Which Order

```
NOW        Raw JSONB SQL         → Depth 1–2 queries working ✅
NEXT       player_innings_stats  → Depth 3–4, instant aggregations
           + bowling_stats table   (one ETL script, run once)

AFTER      pgvector on Supabase  → Depth 5–6, embed pre-written summaries
           + player/match text     Add semantic endpoint to chatbot

LATER      Hybrid retrieval      → SQL pre-filter + vector re-rank
           Knowledge graph        For relationship/traversal queries
```

**The single highest-leverage step right now:**  
Write the ETL script that reads all 1243 match JSONs and inserts into `player_innings_stats` and `player_bowling_stats`. Every downstream stage becomes significantly easier once you have clean, indexed relational data.

---

## The 4 Hard Queries — Status Table

| Query | Depth | Current state | Bottleneck | Fix |
|---|---|---|---|---|
| Players with 30+ in powerplay | 4 | 🔧 possible, slow | Full delivery scan | Pre-compute `pp_runs` column |
| Opener scores in powerplay | 4 | 🔧 possible, complex | Opener identification + join | Pre-compute opener flag in innings stats |
| Bowlers who broke MI's first wicket | 4 | 🔧 possible, complex | Window fn on JSONB unnest | Pre-compute first-wicket events table |
| Top 10 economical in death overs | 4 | 🔧 possible, slow | Full scan + min-sample filter | Pre-compute `death_economy` column |

All four are solvable in SQL today — but they will be slow and brittle until the relational tables are built.
