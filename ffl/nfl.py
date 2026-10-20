#!/usr/bin/env python3
"""NFL data from nflverse / ffverse, scored with THIS league's rules.

Shared by build_stats (the published pages and the analytics grade) and
backtest_grade (which tests that grade on past seasons), so the thing that is
tested is the thing that ships.

Sources (all public, no auth):
  ffverse/ffopportunity  ep_weekly_<yr>.csv   expected + actual stat components per
                                              player-week (the xFP model)
  nflverse-data          stats_player_week_<yr>.csv.gz   usage / efficiency
  nflverse-data          snap_counts_<yr>.csv.gz         offensive snap share
  nflverse-data          players.csv.gz                  gsis <-> espn <-> pfr ids
  nflverse-data          play_by_play_<yr>.csv.gz        per-play EPA/CPOE (ffl.qb_charts)

Raw files land in data/nfl/ (gitignored; re-fetchable).

    python3 -m ffl.nfl 2026            # fetch one season
    python3 -m ffl.nfl 2019 2021 2022  # several
"""

from __future__ import annotations

import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
NFL = REPO / "data" / "nfl"

NV = "https://github.com/nflverse/nflverse-data/releases/download"
FF = "https://github.com/ffverse/ffopportunity/releases/download/latest-data"

# League scoring (ESPN statIds in core.json scoringItems): full PPR, 4-pt pass TD,
# -2 INT, -2 fumble lost, 0.04/pass yd, 0.1/rush+rec yd, 6 rush/rec TD, 2 per 2-pt.
# check_scoring() asserts these still match core.json when it is present.
W = {
    "pass_yards_gained": 0.04, "pass_touchdown": 4, "pass_two_point_conv": 2,
    "pass_interception": -2,
    "rush_yards_gained": 0.1, "rush_touchdown": 6, "rush_two_point_conv": 2,
    "receptions": 1, "rec_yards_gained": 0.1, "rec_touchdown": 6,
    "rec_two_point_conv": 2,
}
FUMBLES = ("rec_fumble_lost", "rush_fumble_lost")      # actual only; no expectation
ESPN_STAT = {3: 0.04, 4: 4, 19: 2, 20: -2, 24: 0.1, 25: 6, 26: 2, 42: 0.1, 43: 6,
             44: 2, 53: 1, 72: -2}

OFFENSE_SLOTS = {0, 2, 4, 6, 23}                       # QB RB WR TE FLEX
POSITIONS = ("QB", "RB", "WR", "TE")


def check_scoring(season: int) -> None:
    p = REPO / "data" / "raw" / str(season) / "core.json"
    if not p.exists():
        return
    import json
    items = json.loads(p.read_text())["settings"]["scoringSettings"]["scoringItems"]
    got = {i["statId"]: i["points"] for i in items if i["statId"] in ESPN_STAT}
    bad = {k: (v, got.get(k)) for k, v in ESPN_STAT.items() if got.get(k) != v}
    if bad:
        raise SystemExit(f"!! league scoring changed vs ffl/nfl.py W: {bad}")


# ---------------------------------------------------------------- fetch

def _get(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as r:
        dest.write_bytes(r.read())


def fetch(season: int, force: bool = False) -> None:
    d = NFL / str(season)
    files = {
        f"ep_weekly_{season}.csv": f"{FF}/ep_weekly_{season}.csv",
        f"stats_player_week_{season}.csv.gz": f"{NV}/stats_player/stats_player_week_{season}.csv.gz",
        f"snap_counts_{season}.csv.gz": f"{NV}/snap_counts/snap_counts_{season}.csv.gz",
        f"play_by_play_{season}.csv.gz": f"{NV}/pbp/play_by_play_{season}.csv.gz",
    }
    for name, url in files.items():
        if force or not (d / name).exists():
            print(f"  fetch {season} {name}")
            _get(url, d / name)
    if force or not (NFL / "players.csv.gz").exists():
        print("  fetch players.csv.gz")
        _get(f"{NV}/players/players.csv.gz", NFL / "players.csv.gz")


# ---------------------------------------------------------------- load

def _rows(path: Path):
    op = gzip.open if path.suffix == ".gz" else open
    with op(path, "rt", newline="", encoding="utf-8") as f:
        yield from csv.DictReader(f)


def _f(v) -> float:
    try:
        return float(v) if v not in ("", "NA", None) else 0.0
    except ValueError:
        return 0.0


def players() -> dict:
    """gsis_id -> {name, pos, espn, pfr}; plus reverse maps."""
    out, by_espn, by_pfr = {}, {}, {}
    for r in _rows(NFL / "players.csv.gz"):
        g = r.get("gsis_id")
        if not g:
            continue
        e = (r.get("espn_id") or "").split(".")[0]
        out[g] = {"name": r.get("display_name") or "", "pos": r.get("position") or "",
                  "espn": e, "pfr": r.get("pfr_id") or ""}
        if e:
            by_espn[e] = g
        if r.get("pfr_id"):
            by_pfr[r["pfr_id"]] = g
    return {"gsis": out, "espn": by_espn, "pfr": by_pfr}


def score(r: dict, expected: bool) -> float:
    sfx = "_exp" if expected else ""
    pts = sum(w * _f(r.get(k + sfx)) for k, w in W.items())
    if not expected:
        pts -= 2 * sum(_f(r.get(k)) for k in FUMBLES)
    return pts


def ep_weekly(season: int, reg_weeks: int | None = None) -> list[dict]:
    """Per player-week: gsis, week, team, pos, fp (actual), xfp (expected),
    league scoring. Regular season only when reg_weeks is given."""
    out = []
    for r in _rows(NFL / str(season) / f"ep_weekly_{season}.csv"):
        wk = int(_f(r["week"]))
        if reg_weeks and wk > reg_weeks:
            continue
        out.append({"gsis": r["player_id"], "week": wk, "team": r["posteam"],
                    "pos": r["position"], "name": r["full_name"],
                    "fp": score(r, False), "xfp": score(r, True),
                    "row": r})
    return out


def stats_week(season: int) -> list[dict]:
    return [r for r in _rows(NFL / str(season) / f"stats_player_week_{season}.csv.gz")
            if r.get("season_type") == "REG"]


def snaps(season: int, ids: dict) -> dict:
    """(gsis, week) -> offense snap share."""
    out = {}
    for r in _rows(NFL / str(season) / f"snap_counts_{season}.csv.gz"):
        if r.get("game_type") != "REG":
            continue
        g = ids["pfr"].get(r["pfr_player_id"])
        if g:
            out[(g, int(_f(r["week"])))] = _f(r["offense_pct"])
    return out


# ---------------------------------------------------------------- grade core

SHRINK_GAMES = 2   # pseudo-games of positional prior blended into every rate


def rates(ep: list[dict], before_week: int, key: str) -> tuple[dict, dict]:
    """Per-game rate of `key` ('xfp' or 'fp') from weeks < before_week, shrunk
    toward the positional median by SHRINK_GAMES pseudo-games so a one-game
    sample cannot dominate. Returns (rate by gsis, prior by position)."""
    tot, gp, pos = {}, {}, {}
    for r in ep:
        if r["week"] >= before_week:
            continue
        g = r["gsis"]
        tot[g] = tot.get(g, 0.0) + r[key]
        gp[g] = gp.get(g, 0) + 1
        pos[g] = r["pos"]
    prior = {}
    for p in POSITIONS:
        v = sorted(tot[g] / gp[g] for g in tot if pos[g] == p and gp[g] >= 2)
        prior[p] = v[len(v) // 2] if v else 0.0
    k = SHRINK_GAMES
    rate = {g: (tot[g] + k * prior.get(pos[g], 0.0)) / (gp[g] + k) for g in tot}
    return rate, prior


ESPN_POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE"}
SLOT_POS = {0: "QB", 2: "RB", 4: "WR", 6: "TE"}


def roster_value(entries: list[dict], value_of, slot_counts: dict) -> tuple[float, list]:
    """Best legal OFFENSIVE lineup (QB, RB, WR, TE, FLEX) where each player is
    worth value_of(entry). K and D/ST are left out: nflverse has no expected-
    points model for them, and every predictor is held to the same slots."""
    from ffl.build_site import optimal_lineup
    pool = []
    for e in entries:
        v = value_of(e)
        if v is None:
            continue
        pool.append({"playerId": e["playerId"], "eligible": e.get("eligible") or [],
                     "actual": v})
    sc = {s: n for s, n in slot_counts.items() if int(s) in OFFENSE_SLOTS}
    return optimal_lineup(pool, sc)


def entry_pos(e: dict) -> str | None:
    for s in e.get("eligible") or []:
        if s in SLOT_POS:
            return SLOT_POS[s]
    return None


def main(argv):
    yrs = [int(a) for a in argv] or [2026]
    for y in yrs:
        fetch(y, force=(y == max(yrs) and y >= 2026))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
