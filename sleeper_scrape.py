"""
Sleeper Fantasy Football Scraper -- 2026 Season
League : https://sleeper.com/leagues/1347804040074919936/players (dummy league for testing)
DB     : sleeper_2026.db

What it fetches:
  ADP         -- adp_ppr / adp_std / adp_half_ppr / adp_2qb / adp_dynasty
                 from https://api.sleeper.app/projections/nfl/2026?season_type=regular
  Projections -- season stat projections (rush_yd, rec_yd, pass_yd, TDs, etc.)
                 same endpoint
  Fantasy pts -- pts_ppr / pts_half_ppr / pts_std pre-calc'd by Sleeper
                 + proj_pts_league re-calculated with THIS league's exact scoring rules

TOKEN SETUP (one-time, ~30 seconds):
  1. Go to https://sleeper.com in Chrome and make sure you're logged in
  2. Press F12 -> Console tab
  3. Paste and run:
       (function(){
         for(const[k,v]of Object.entries(localStorage)){
           if(k.startsWith('firebase:authUser:')){
             const t=JSON.parse(v)?.stsTokenManager?.accessToken;
             if(t){console.log('TOKEN: '+t);return;}
           }
         }
         ['token','auth_token','access_token'].forEach(k=>{
           if(localStorage[k]) console.log('TOKEN: '+localStorage[k]);
         });
       })();
  4. Copy the long string after "TOKEN: "
  5. Save it to  sleeper_token.txt  in this directory
"""

import os, sys, sqlite3
import requests

# Config

LEAGUE_ID    = "1347804040074919936" #REPLACE WITH YOUR LEAGUE ID
SEASON       = "2026"
SEASON_TYPE  = "regular"
DB_PATH      = "sleeper_2026.db"

BASE_V1      = "https://api.sleeper.app/v1"
BASE_NOVERS  = "https://api.sleeper.app"      # no /v1/ -- needed for proj endpoint

POSITIONS    = {"QB", "RB", "WR", "TE", "K", "DEF"}

# Token

def load_token() -> str:
    p = os.path.join(os.path.dirname(__file__), "sleeper_token.txt")
    if os.path.exists(p):
        t = open(p).read().strip()
        if t:
            return t
    t = os.environ.get("SLEEPER_TOKEN", "").strip()
    if t:
        return t
    print("No token found. See the module docstring for instructions.")
    t = input("Paste Sleeper token: ").strip()
    if not t:
        sys.exit("No token provided.")
    if input("Save to sleeper_token.txt? [y/N] ").strip().lower() == "y":
        open(p, "w").write(t)
    return t

# HTTP

sess = requests.Session()
sess.headers.update({"User-Agent": "sleeper-scraper/3.0", "Content-Type": "application/json"})

def set_auth(token: str):
    sess.headers["Authorization"] = token   # raw JWT, no Bearer prefix

def api_get(url: str, params: dict = None):
    r = sess.get(url, params=params, timeout=30)
    if r.status_code == 401:
        sys.exit("401 Unauthorized -- token expired. Re-run the DevTools snippet.")
    if r.status_code == 404:
        return {}
    r.raise_for_status()
    return r.json()

# API calls

def fetch_league() -> dict:
    return api_get(f"{BASE_V1}/league/{LEAGUE_ID}")

def fetch_players() -> dict:
    print("  Fetching all NFL players (~5 MB)...")
    return api_get(f"{BASE_V1}/players/nfl")

def fetch_season_projections(season: str) -> list:
    """
    Season-level projections endpoint (no /v1/ prefix).
    Returns a list of projection objects each containing:
      player_id, player {first_name, last_name, position, team}, team,
      stats {adp_ppr, adp_std, adp_half_ppr, adp_2qb, adp_dynasty,
             pts_ppr, pts_std, pts_half_ppr,
             pass_yd, rush_yd, rec_yd, pass_td, rush_td, rec_td, ...}
    """
    data = api_get(f"{BASE_NOVERS}/projections/nfl/{season}",
                   params={"season_type": SEASON_TYPE})
    return data if isinstance(data, list) else []

# Database

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS players (
    player_id   TEXT PRIMARY KEY,
    full_name   TEXT,
    position    TEXT,
    team        TEXT,
    age         INTEGER,
    years_exp   INTEGER,
    status      TEXT
);

CREATE TABLE IF NOT EXISTS league_scoring (
    stat_key    TEXT PRIMARY KEY,
    points      REAL
);

CREATE TABLE IF NOT EXISTS adp (
    player_id            TEXT PRIMARY KEY,
    adp_ppr              REAL,
    adp_half_ppr         REAL,
    adp_std              REAL,
    adp_2qb              REAL,
    adp_dynasty          REAL,
    adp_dynasty_ppr      REAL,
    adp_dynasty_half_ppr REAL,
    adp_dynasty_std      REAL,
    adp_rookie           REAL,
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);

CREATE TABLE IF NOT EXISTS season_projections (
    player_id       TEXT PRIMARY KEY,
    gp              REAL,
    pass_yd         REAL,   pass_att    REAL,   pass_cmp    REAL,
    pass_td         REAL,   pass_int    REAL,   pass_2pt    REAL,
    pass_sack       REAL,
    rush_yd         REAL,   rush_att    REAL,
    rush_td         REAL,   rush_2pt    REAL,   rush_fd     REAL,
    rec             REAL,   rec_yd      REAL,   rec_tgt     REAL,
    rec_td          REAL,   rec_2pt     REAL,   rec_fd      REAL,
    fum_lost        REAL,
    fgm             REAL,   fgm_0_19    REAL,   fgm_20_29   REAL,
    fgm_30_39       REAL,   fgm_40_49   REAL,   fgm_50p     REAL,
    xpm             REAL,
    def_sack        REAL,   def_int     REAL,   def_fum_rec REAL,
    def_td          REAL,   def_st_td   REAL,   pts_allow   REAL,
    proj_pts_league REAL,
    pts_ppr         REAL,
    pts_half_ppr    REAL,
    pts_std         REAL,
    vorp_ppr        REAL,
    FOREIGN KEY (player_id) REFERENCES players(player_id)
);
"""

def init_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    # Drop and recreate tables if the schema has changed
    expected = {"adp": 10, "season_projections": 39, "players": 7, "league_scoring": 2}
    needs_reset = False
    for tbl, expected_cols in expected.items():
        cols = conn.execute(f"PRAGMA table_info({tbl})").fetchall()
        if cols and len(cols) != expected_cols:
            needs_reset = True
            break
    if needs_reset:
        print("  Detected outdated schema -- rebuilding tables...")
        for tbl in ["season_projections", "adp", "players", "league_scoring"]:
            conn.execute(f"DROP TABLE IF EXISTS {tbl}")
        conn.commit()
    conn.executescript(SCHEMA)
    conn.commit()
    return conn

# Fantasy points

def calc_pts(stats: dict, scoring: dict) -> float:
    return round(sum((stats.get(k) or 0) * v for k, v in scoring.items()), 4)

# VORP

# How many starters at each position before hitting replacement level.
# Configurable here -- no hardcoded values in the calculation itself.
# Number picks are explained in blog
REPLACEMENT_RANK = {
    "QB": 26,
    "TE": 26,
    "RB": 61,
    "WR": 61,
}

def calc_vorp(conn) -> dict:
    """
    Calculate Value Over Replacement Player for every row in season_projections.

    Replacement baseline = pts_ppr of the Nth-ranked player at that position,
    where N comes from REPLACEMENT_RANK (dynamically queried from the DB).

    Returns a dict of player_id -> vorp_ppr.
    """
    vorp = {}
    for pos, rank in REPLACEMENT_RANK.items():
        # Find the replacement player's pts_ppr: Nth highest among this position
        row = conn.execute("""
            SELECT pts_ppr
            FROM season_projections sp
            JOIN players p USING (player_id)
            WHERE p.position = ?
              AND sp.pts_ppr IS NOT NULL
            ORDER BY sp.pts_ppr DESC
            LIMIT 1 OFFSET ?
        """, (pos, rank - 1)).fetchone()   # OFFSET is 0-based

        if row is None:
            # Fewer players projected than the replacement rank -- use 0 as baseline
            baseline = 0.0
        else:
            baseline = row[0]

        # VORP for every player at this position
        players = conn.execute("""
            SELECT sp.player_id, sp.pts_ppr
            FROM season_projections sp
            JOIN players p USING (player_id)
            WHERE p.position = ?
              AND sp.pts_ppr IS NOT NULL
        """, (pos,)).fetchall()

        for pid, pts_ppr in players:
            vorp[pid] = round(pts_ppr - baseline, 4)

    return vorp


def store_vorp(conn, vorp: dict):
    """Write vorp_ppr values back into season_projections."""
    conn.executemany(
        "UPDATE season_projections SET vorp_ppr = ? WHERE player_id = ?",
        [(v, pid) for pid, v in vorp.items()]
    )
    conn.commit()

# Store helpers

def store_players(conn, players: dict) -> int:
    rows = []
    for pid, p in players.items():
        fp  = p.get("fantasy_positions") or []
        pos = p.get("position") or (fp[0] if fp else None)
        if pos not in POSITIONS:
            continue
        name = (p.get("full_name") or
                f"{p.get('first_name','').strip()} {p.get('last_name','').strip()}".strip())
        rows.append((pid, name, pos, p.get("team"), p.get("age"),
                     p.get("years_exp"), p.get("status")))
    conn.executemany("INSERT OR REPLACE INTO players VALUES (?,?,?,?,?,?,?)", rows)
    conn.commit()
    return len(rows)

def store_scoring(conn, scoring: dict):
    conn.executemany("INSERT OR REPLACE INTO league_scoring VALUES (?,?)", scoring.items())
    conn.commit()

def store_projections(conn, proj_list: list, scoring: dict) -> dict:
    adp_rows, proj_rows = [], []
    counts = {"adp": 0, "proj": 0, "skipped": 0}

    for item in proj_list:
        pid  = item.get("player_id")
        s    = item.get("stats") or {}
        p    = item.get("player") or {}
        pos  = p.get("position")

        if pos not in POSITIONS or not s:
            continue

        # ADP -- only store when a real draft slot exists (< 900)
        adp_ppr = s.get("adp_ppr", 999)
        if adp_ppr < 900:
            adp_rows.append((
                pid,
                adp_ppr,
                s.get("adp_half_ppr"),
                s.get("adp_std"),
                s.get("adp_2qb"),
                s.get("adp_dynasty"),
                s.get("adp_dynasty_ppr"),
                s.get("adp_dynasty_half_ppr"),
                s.get("adp_dynasty_std"),
                s.get("adp_rookie"),
            ))
            counts["adp"] += 1

        # Projections -- keep any player with at least one meaningful stat
        has_proj = any(s.get(k) for k in
                       ("pts_ppr", "pts_std", "rush_yd", "pass_yd", "rec_yd",
                        "fgm", "def_sack", "def_int"))
        if not has_proj:
            counts["skipped"] += 1
            continue

        pts_league = calc_pts(s, scoring)
        proj_rows.append((
            pid,
            s.get("gp"),
            s.get("pass_yd"),    s.get("pass_att"),  s.get("pass_cmp"),
            s.get("pass_td"),    s.get("pass_int"),  s.get("pass_2pt"),
            s.get("pass_sack"),
            s.get("rush_yd"),    s.get("rush_att"),
            s.get("rush_td"),    s.get("rush_2pt"),  s.get("rush_fd"),
            s.get("rec"),        s.get("rec_yd"),    s.get("rec_tgt"),
            s.get("rec_td"),     s.get("rec_2pt"),   s.get("rec_fd"),
            s.get("fum_lost"),
            s.get("fgm"),
            s.get("fgm_0_19"),   s.get("fgm_20_29"), s.get("fgm_30_39"),
            s.get("fgm_40_49"),  s.get("fgm_50p"),
            s.get("xpm"),
            s.get("def_sack"),   s.get("def_int"),   s.get("def_fum_rec"),
            s.get("def_td"),     s.get("def_st_td"), s.get("pts_allow"),
            pts_league,
            s.get("pts_ppr"),    s.get("pts_half_ppr"), s.get("pts_std"),
            None,   # vorp_ppr -- populated later by calc_vorp / store_vorp
        ))
        counts["proj"] += 1

    if adp_rows:
        conn.executemany(
            "INSERT OR REPLACE INTO adp VALUES (?,?,?,?,?,?,?,?,?,?)", adp_rows)
    if proj_rows:
        conn.executemany("""
            INSERT OR REPLACE INTO season_projections VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, proj_rows)
    conn.commit()
    return counts

# Summary printout

def print_summary(conn):
    print("\n" + "-"*80)
    print("TOP 15 -- Projected season points (this league's scoring)  [2026]")
    print("-"*80)
    print(f"  {'Name':<26} {'Pos':<5} {'Team':<5} {'Lg Pts':>8}  {'PPR Pts':>8}  {'VORP':>8}  {'ADP':>6}")
    print("  " + "-"*78)
    rows = conn.execute("""
        SELECT p.full_name, p.position, p.team,
               sp.proj_pts_league, sp.pts_ppr, sp.vorp_ppr, a.adp_ppr
        FROM season_projections sp
        JOIN players p USING (player_id)
        LEFT JOIN adp a USING (player_id)
        ORDER BY sp.proj_pts_league DESC
        LIMIT 15
    """).fetchall()
    for name, pos, team, lg_pts, ppr_pts, vorp, adp in rows:
        vorp_s = f"{vorp:>8.1f}" if vorp is not None else "     n/a"
        print(f"  {name:<26} {pos:<5} {team or '??':<5} "
              f"{lg_pts:>8.1f}  {ppr_pts or 0:>8.1f}  {vorp_s}  {adp or 0:>6.1f}")

    print("\n" + "-"*72)
    print("TOP 25 ADP (PPR) -- earliest drafted first  [2026]")
    print("-"*72)
    print(f"  {'ADP':>6}  {'Name':<26} {'Pos':<5} {'Team':<5} {'Lg Pts':>8}  {'PPR Pts':>8}  {'STD ADP':>8}")
    print("  " + "-"*70)
    rows = conn.execute("""
        SELECT p.full_name, p.position, p.team,
               a.adp_ppr, a.adp_std, a.adp_half_ppr,
               COALESCE(sp.proj_pts_league, 0), COALESCE(sp.pts_ppr, 0)
        FROM adp a
        JOIN players p USING (player_id)
        LEFT JOIN season_projections sp USING (player_id)
        ORDER BY a.adp_ppr ASC
        LIMIT 25
    """).fetchall()
    for name, pos, team, adp_ppr, adp_std, adp_hppr, lg_pts, ppr_pts in rows:
        print(f"  {adp_ppr:>6.1f}  {name:<26} {pos:<5} {team or '??':<5} "
              f"{lg_pts:>8.1f}  {ppr_pts:>8.1f}  {adp_std or 0:>8.1f}")

# Main

def main():
    print(f"=== Sleeper {SEASON} Scraper  (league {LEAGUE_ID}) ===\n")

    token = load_token()
    set_auth(token)
    print("  Auth token loaded.\n")

    # 1. League info + scoring
    print("[1/4] Fetching league + scoring rules...")
    league  = fetch_league()
    scoring = league.get("scoring_settings", {})
    print(f"  League  : {league.get('name')}")
    print(f"  Season  : {league.get('season')}  |  {len(scoring)} scoring rules")

    conn = init_db()
    store_scoring(conn, scoring)

    # 2. All players
    print("\n[2/4] Fetching player roster...")
    players   = fetch_players()
    n_players = store_players(conn, players)
    print(f"  {n_players:,} players stored")

    # 3. 2026 season projections + ADP
    print(f"\n[3/4] Fetching {SEASON} season projections + ADP...")
    proj_list = fetch_season_projections(SEASON)
    print(f"  {len(proj_list):,} records from Sleeper")

    if not proj_list:
        print("  ERROR: Endpoint returned no data.")
        conn.close()
        sys.exit(1)

    counts = store_projections(conn, proj_list, scoring)
    print(f"  Players with real ADP stored    : {counts['adp']:,}")
    print(f"  Players with projections stored : {counts['proj']:,}")
    print(f"  Skipped (no meaningful stats)   : {counts['skipped']:,}")

    # 4. VORP
    print("\n[4/5] Calculating VORP (Value Over Replacement Player)...")
    for pos, rank in REPLACEMENT_RANK.items():
        row = conn.execute("""
            SELECT sp.pts_ppr, p.full_name
            FROM season_projections sp JOIN players p USING (player_id)
            WHERE p.position = ? AND sp.pts_ppr IS NOT NULL
            ORDER BY sp.pts_ppr DESC LIMIT 1 OFFSET ?
        """, (pos, rank - 1)).fetchone()
        baseline_str = f"{row[0]:.1f} pts ({row[1]})" if row else "n/a"
        print(f"  {pos:<3} replacement baseline (rank {rank}): {baseline_str}")
    vorp = calc_vorp(conn)
    store_vorp(conn, vorp)
    print(f"  vorp_ppr written for {len(vorp):,} players")

    # 5. Summary
    print("\n[5/5] Summary")
    print_summary(conn)
    conn.close()

    print(f"\nDatabase saved: {DB_PATH}")


if __name__ == "__main__":
    main()
