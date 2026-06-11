# AskAboutIPL — ETL Season Log

Running log of decisions, edge cases, and fixes discovered while running `etl_matches.py` season by season.
Update this file after each season run before moving to the next.

---

## Conventions

| Symbol | Meaning |
|--------|---------|
| ✅ | Resolved / working correctly |
| ⚠️ | Known edge case — handled, watch for recurrence |
| ❌ | Bug found, fix applied |
| 📝 | Decision / design choice recorded |

---

## Season: 2026 (Edition 19)

**Status:** ✅ Complete  
**Matches in DB:** 74  
**Script run:** `python etl_matches.py --season 2026`

### Results

| Table | Rows |
|-------|------|
| innings | 149 |
| player_innings_stats | 1134 |
| player_bowling_stats | 856 |

### Findings

- ✅ Zero warnings — all FKs resolved cleanly
- 📝 149 innings for 74 matches → exactly **1 super over** in 2026 season
- ✅ Phase bucketing working (overs 0-5 / 6-14 / 15-19)
- ✅ Bowler wickets: run-outs excluded correctly
- ✅ Opener detection: first batter + non_striker on over 0, delivery 0

---

## Global Decisions

### Phase boundaries
- Powerplay : overs 1–6  → JSON 0-indexed: over 0–5
- Middle     : overs 7–15 → JSON 0-indexed: over 6–14
- Death      : overs 16–20 → JSON 0-indexed: over 15–19

Source: Google "In T20 cricket, the innings is divided into three distinct phases: the Powerplay (overs 1 to 6), the middle overs (overs 7 to 15), and the death overs (overs 16 to 20)."

### Wicket attribution
- Run-outs, retired hurt, obstructing the field → NOT credited to bowler
- All other kinds → credited to bowler (wicket + phase wicket)

### Legal ball
- Delivery is legal if `extras.wides` and `extras.noballs` are both absent
- Batter faces all deliveries (legal + wides, but NOT no-balls per scorecard convention)
  - Actually: batter balls_faced = count of deliveries where NOT a wide

### Super over
- Included in ETL as `innings_num = 3` (or 4 for second super over if needed)
- `is_super_over = TRUE` column on innings table

### Opener detection
- `is_opener = TRUE` for the batter AND non_striker on the very first delivery of innings 1 (over 0, delivery index 0)

### Season string coercion
- Some seasons stored as integer in JSONB (e.g. `2024`), others as string (`"2007/08"`)
- Always coerce with `str(season)` before lookup

---

## Season Backlog

| Season | Status | Notes |
|--------|--------|-------|
| 2026   | ✅ Done | 74 matches, 149 innings, 0 warnings |
| 2025   | ✅ Done | |
| 2024   | ✅ Done | |
| 2023   | ✅ Done | |
| 2022   | ✅ Done | |
| 2021   | ✅ Done | |
| 2020/21 | ✅ Done | Split season — BCCI bubble in UAE |
| 2019   | ✅ Done | |
| 2018   | ✅ Done | |
| 2017   | ✅ Done | |
| 2016   | ✅ Done | |
| 2015   | ✅ Done | |
| 2014   | ✅ Done | |
| 2013   | ✅ Done | |
| 2012   | ✅ Done | |
| 2011   | ✅ Done | |
| 2009/10 | ✅ Done | Split season |
| 2009   | ✅ Done | |
| 2007/08 | ✅ Done | Season 1 — split season |
