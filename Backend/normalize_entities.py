"""
Normalises teams and venues tables after seed_entities.py has run.

Teams:
  Each row keeps its exact JSON name (needed for FK lookups during ETL).
  city  — the city the franchise represents (geographic slot).
  franchise_name — groups ONLY same-ownership renames (e.g. Delhi Daredevils → Delhi Capitals).
                   Deccan Chargers and SRH are different franchises, both city=Hyderabad.
  active — False for franchises no longer in IPL.

Venues:
  Same physical stadium appears under multiple name variants in the JSON.
  We pick one canonical row and DELETE the duplicates.
  The ETL scripts use VENUE_ALIASES to map raw JSON names → canonical venue_id.
"""

import psycopg2
from psycopg2.extras import RealDictCursor
from config import DATABASE_URL

# ── Team metadata ─────────────────────────────────────────────────────────────
# (json_name, city, franchise_name, active)
# franchise_name only links same-ownership renames — NOT city-slot succession.

TEAM_META = [
    # Active franchises
    ("Chennai Super Kings",          "Chennai",    "Chennai Super Kings",          True),
    ("Mumbai Indians",               "Mumbai",     "Mumbai Indians",               True),
    ("Kolkata Knight Riders",        "Kolkata",    "Kolkata Knight Riders",        True),
    ("Rajasthan Royals",             "Jaipur",     "Rajasthan Royals",             True),
    ("Sunrisers Hyderabad",          "Hyderabad",  "Sunrisers Hyderabad",          True),
    ("Gujarat Titans",               "Ahmedabad",  "Gujarat Titans",               True),
    ("Lucknow Super Giants",         "Lucknow",    "Lucknow Super Giants",         True),

    # Renamed — same ownership
    ("Delhi Daredevils",             "Delhi",      "Delhi Capitals",               False),
    ("Delhi Capitals",               "Delhi",      "Delhi Capitals",               True),
    ("Kings XI Punjab",              "Mohali",     "Punjab Kings",                 False),
    ("Punjab Kings",                 "Mohali",     "Punjab Kings",                 True),
    ("Royal Challengers Bangalore",  "Bengaluru",  "Royal Challengers Bengaluru",  False),
    ("Royal Challengers Bengaluru",  "Bengaluru",  "Royal Challengers Bengaluru",  True),
    ("Rising Pune Supergiant",       "Pune",       "Rising Pune Supergiants",      False),
    ("Rising Pune Supergiants",      "Pune",       "Rising Pune Supergiants",      False),

    # Defunct — different franchise, same city slot as a current team
    ("Deccan Chargers",              "Hyderabad",  "Deccan Chargers",              False),  # Hyderabad slot before SRH
    ("Gujarat Lions",                "Rajkot",     "Gujarat Lions",                False),
    ("Kochi Tuskers Kerala",         "Kochi",      "Kochi Tuskers Kerala",         False),
    ("Pune Warriors",                "Pune",       "Pune Warriors",                False),
]

# ── Venue deduplication ───────────────────────────────────────────────────────
# Maps every variant name → canonical name to keep.
# Canonical row stays; all aliases are deleted.
# The ETL will use this dict when looking up venue_id from raw JSON.

VENUE_ALIASES = {
    # Arun Jaitley (was Feroz Shah Kotla)
    "Arun Jaitley Stadium, Delhi":   "Arun Jaitley Stadium",
    "Feroz Shah Kotla":              "Arun Jaitley Stadium",

    # Brabourne
    "Brabourne Stadium, Mumbai":     "Brabourne Stadium",

    # DY Patil
    "Dr DY Patil Sports Academy, Mumbai": "Dr DY Patil Sports Academy",

    # ACA-VDCA Vizag
    "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium, Visakhapatnam":
        "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium",

    # Eden Gardens
    "Eden Gardens, Kolkata":         "Eden Gardens",

    # HPCA Dharamsala
    "Himachal Pradesh Cricket Association Stadium, Dharamsala":
        "Himachal Pradesh Cricket Association Stadium",

    # Chinnaswamy
    "M Chinnaswamy Stadium, Bengaluru": "M Chinnaswamy Stadium",
    "M.Chinnaswamy Stadium":            "M Chinnaswamy Stadium",

    # Chepauk
    "MA Chidambaram Stadium, Chepauk":          "MA Chidambaram Stadium",
    "MA Chidambaram Stadium, Chepauk, Chennai": "MA Chidambaram Stadium",

    # Mullanpur / New Chandigarh — same stadium
    "Maharaja Yadavindra Singh International Cricket Stadium, New Chandigarh":
        "Maharaja Yadavindra Singh International Cricket Stadium, Mullanpur",

    # MCA Pune
    "Maharashtra Cricket Association Stadium, Pune":
        "Maharashtra Cricket Association Stadium",

    # Narendra Modi (was Sardar Patel / Motera)
    "Sardar Patel Stadium, Motera":  "Narendra Modi Stadium, Ahmedabad",

    # PCA Mohali — three name variants
    "Punjab Cricket Association IS Bindra Stadium, Mohali":
        "Punjab Cricket Association IS Bindra Stadium",
    "Punjab Cricket Association IS Bindra Stadium, Mohali, Chandigarh":
        "Punjab Cricket Association IS Bindra Stadium",
    "Punjab Cricket Association Stadium, Mohali":
        "Punjab Cricket Association IS Bindra Stadium",

    # RGIS Hyderabad
    "Rajiv Gandhi International Stadium, Uppal":
        "Rajiv Gandhi International Stadium",
    "Rajiv Gandhi International Stadium, Uppal, Hyderabad":
        "Rajiv Gandhi International Stadium",

    # Sawai Mansingh
    "Sawai Mansingh Stadium, Jaipur": "Sawai Mansingh Stadium",

    # Shaheed Veer Narayan Singh Raipur
    "Shaheed Veer Narayan Singh International Stadium, Raipur":
        "Shaheed Veer Narayan Singh International Stadium",

    # Wankhede
    "Wankhede Stadium, Mumbai":      "Wankhede Stadium",
}

# ── Venue city lookup for the canonical rows ──────────────────────────────────

VENUE_CITY = {
    "Arun Jaitley Stadium":                                    "Delhi",
    "Barabati Stadium":                                        "Cuttack",
    "Barsapara Cricket Stadium, Guwahati":                     "Guwahati",
    "Bharat Ratna Shri Atal Bihari Vajpayee Ekana Cricket Stadium, Lucknow": "Lucknow",
    "Brabourne Stadium":                                       "Mumbai",
    "Buffalo Park":                                            "East London",
    "De Beers Diamond Oval":                                   "Kimberley",
    "Dr DY Patil Sports Academy":                              "Mumbai",
    "Dr. Y.S. Rajasekhara Reddy ACA-VDCA Cricket Stadium":    "Visakhapatnam",
    "Dubai International Cricket Stadium":                     "Dubai",
    "Eden Gardens":                                            "Kolkata",
    "Green Park":                                              "Kanpur",
    "Himachal Pradesh Cricket Association Stadium":            "Dharamsala",
    "Holkar Cricket Stadium":                                  "Indore",
    "JSCA International Stadium Complex":                      "Ranchi",
    "Kingsmead":                                               "Durban",
    "M Chinnaswamy Stadium":                                   "Bengaluru",
    "MA Chidambaram Stadium":                                  "Chennai",
    "Maharaja Yadavindra Singh International Cricket Stadium, Mullanpur": "Mullanpur",
    "Maharashtra Cricket Association Stadium":                 "Pune",
    "Narendra Modi Stadium, Ahmedabad":                        "Ahmedabad",
    "Nehru Stadium":                                           "Kochi",
    "New Wanderers Stadium":                                   "Johannesburg",
    "Newlands":                                                "Cape Town",
    "OUTsurance Oval":                                         "Bloemfontein",
    "Punjab Cricket Association IS Bindra Stadium":            "Mohali",
    "Rajiv Gandhi International Stadium":                      "Hyderabad",
    "Saurashtra Cricket Association Stadium":                  "Rajkot",
    "Sawai Mansingh Stadium":                                  "Jaipur",
    "Shaheed Veer Narayan Singh International Stadium":        "Raipur",
    "Sharjah Cricket Stadium":                                 "Sharjah",
    "Sheikh Zayed Stadium":                                    "Abu Dhabi",
    "St George's Park":                                        "Port Elizabeth",
    "Subrata Roy Sahara Stadium":                              "Pune",
    "SuperSport Park":                                         "Centurion",
    "Vidarbha Cricket Association Stadium, Jamtha":            "Nagpur",
    "Wankhede Stadium":                                        "Mumbai",
    "Zayed Cricket Stadium, Abu Dhabi":                        "Abu Dhabi",
}


def normalize():
    conn = psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)
    cur = conn.cursor()

    # ── 1. Teams: add city, franchise_name, active columns ───────────────────
    for col, dtype in [("city", "TEXT"), ("franchise_name", "TEXT"), ("active", "BOOLEAN")]:
        cur.execute(f"ALTER TABLE teams ADD COLUMN IF NOT EXISTS {col} {dtype}")

    for team_name, city, franchise, active in TEAM_META:
        cur.execute(
            "UPDATE teams SET city = %s, franchise_name = %s, active = %s WHERE name = %s",
            (city, franchise, active, team_name)
        )

    conn.commit()
    cur.execute("SELECT name, city, franchise_name, active FROM teams ORDER BY city, name")
    rows = cur.fetchall()
    print(f"── teams ({len(rows)} rows) ──")
    print(f"  {'NAME':<35} {'CITY':<12} {'FRANCHISE':<35} ACTIVE")
    print(f"  {'─'*35} {'─'*12} {'─'*35} ──────")
    for r in rows:
        print(f"  {r['name']:<35} {r['city']:<12} {r['franchise_name']:<35} {'✓' if r['active'] else '✗'}")

    # ── 2. Venues: add city column, then delete duplicates ───────────────────
    cur.execute("""
        ALTER TABLE venues
        ADD COLUMN IF NOT EXISTS city TEXT
    """)

    # Set city on canonical rows
    for venue_name, city in VENUE_CITY.items():
        cur.execute(
            "UPDATE venues SET city = %s WHERE name = %s",
            (city, venue_name)
        )

    # Delete alias rows (canonical already exists)
    deleted = 0
    for alias, canonical in VENUE_ALIASES.items():
        cur.execute("DELETE FROM venues WHERE name = %s", (alias,))
        deleted += cur.rowcount
        if cur.rowcount:
            print(f"  DEL  '{alias}'  →  '{canonical}'")

    conn.commit()

    cur.execute("SELECT name, city FROM venues ORDER BY name")
    rows = cur.fetchall()
    print(f"\n── venues ({len(rows)} rows, {deleted} deleted) ──")
    for r in rows:
        print(f"  {r['name']:<60} {r['city'] or ''}")

    cur.close()
    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    normalize()
