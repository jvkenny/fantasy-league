#!/usr/bin/env python3
"""NFL position stat pages + the analytics roster grade.

  site/stats_{qb,rb,wr,te}.json   one payload per position page
  site/analysis.json              upserts the `team_grade` chart and the three
                                  QB air-yards charts (ffl.qb_charts)

Every player's fantasy points are scored with THIS league's rules (ffl.nfl.W),
so FP/G here matches ESPN's actuals for 96-97% of player-weeks; the rest are
plays the stat feed does not model (return TDs and the like).

The grade is the best legal offensive lineup the current roster can field,
each player valued at expected fantasy points per game (xFP: what his
opportunities are usually worth, from ffopportunity). It is published NEXT TO
the power rankings, not inside them: backtest_grade found it modestly better
than valuing players by actual points (5 of 6 seasons) but not by a margin
that clears its own noise. reports/grade_backtest.json carries the numbers;
the page itself states only what the grade is (method talk stays off the site).

    python3 -m ffl.nfl 2026 && python3 -m ffl.build_stats && python3 -m ffl.render
"""

from __future__ import annotations

import json
import statistics as st
import sys
from datetime import datetime, timezone
from pathlib import Path

from ffl import nfl, qb_charts

REPO = Path(__file__).resolve().parent.parent
SITE = REPO / "site"
DER = REPO / "data" / "derived"
BASE = "https://jvkenny.github.io/fantasy-league/"

# Listing gate (whole season so far) and how many make the scatter.
GATE = {"QB": ("attempts", 20), "RB": ("opps", 10), "WR": ("targets", 8), "TE": ("targets", 6)}
SCATTER_TOP = {"QB": 32, "RB": 48, "WR": 64, "TE": 32}

COMMON_HEAD = [
    {"k": "g", "l": "G", "f": "i", "d": "Games with at least one opportunity"},
    {"k": "snap", "l": "Snap%", "f": "pct", "d": "Average share of offensive snaps"},
]
COMMON_TAIL = [
    {"k": "fpg", "l": "FP/G", "f": "1", "d": "Fantasy points per game, league scoring"},
    {"k": "xfpg", "l": "xFP/G", "f": "1", "d": "Expected fantasy points per game: what this player's opportunities are usually worth (ffopportunity model)"},
    {"k": "fpoe", "l": "+/- exp", "f": "pm1", "d": "FP/G minus xFP/G. Positive = scoring above what the opportunities usually produce"},
]
COLS = {
    "QB": [
        {"k": "att", "l": "Att", "f": "i", "d": "Pass attempts"},
        {"k": "cmp", "l": "Cmp%", "f": "pct", "d": "Completion rate"},
        {"k": "cpoe", "l": "CPOE", "f": "pm1", "d": "Completion % over expected (nflfastR), attempt-weighted"},
        {"k": "epa_db", "l": "EPA/db", "f": "2", "d": "Passing EPA per dropback (attempts + sacks)"},
        {"k": "adot", "l": "aDOT", "f": "1", "d": "Average depth of target: air yards per attempt"},
        {"k": "pyds", "l": "Pass yds", "f": "i", "d": "Passing yards"},
        {"k": "ptd", "l": "TD", "f": "i", "d": "Passing touchdowns"},
        {"k": "int", "l": "Int", "f": "i", "d": "Interceptions"},
        {"k": "sk", "l": "Sk", "f": "i", "d": "Sacks taken"},
        {"k": "ryds", "l": "Rush yds", "f": "i", "d": "Rushing yards"},
        {"k": "rtd", "l": "Rush TD", "f": "i", "d": "Rushing touchdowns"},
    ],
    "RB": [
        {"k": "car", "l": "Car", "f": "i", "d": "Carries"},
        {"k": "ryds", "l": "Rush yds", "f": "i", "d": "Rushing yards"},
        {"k": "ypc", "l": "YPC", "f": "1", "d": "Yards per carry"},
        {"k": "repa", "l": "EPA/car", "f": "2", "d": "Rushing EPA per carry"},
        {"k": "rtd", "l": "Rush TD", "f": "i", "d": "Rushing touchdowns"},
        {"k": "tgt", "l": "Tgt", "f": "i", "d": "Targets"},
        {"k": "tshare", "l": "Tgt share", "f": "pct", "d": "Share of team targets, game average"},
        {"k": "rec", "l": "Rec", "f": "i", "d": "Receptions"},
        {"k": "recyds", "l": "Rec yds", "f": "i", "d": "Receiving yards"},
        {"k": "oppg", "l": "Opp/G", "f": "1", "d": "Carries + targets per game"},
    ],
    "WR": [
        {"k": "tgt", "l": "Tgt", "f": "i", "d": "Targets"},
        {"k": "tshare", "l": "Tgt share", "f": "pct", "d": "Share of team targets, game average"},
        {"k": "ayshare", "l": "AY share", "f": "pct", "d": "Share of team air yards, game average"},
        {"k": "wopr", "l": "WOPR", "f": "2", "d": "Weighted opportunity: 1.5 x target share + 0.7 x air-yards share"},
        {"k": "adot", "l": "aDOT", "f": "1", "d": "Air yards per target"},
        {"k": "rec", "l": "Rec", "f": "i", "d": "Receptions"},
        {"k": "catch", "l": "Catch%", "f": "pct", "d": "Receptions per target"},
        {"k": "recyds", "l": "Yds", "f": "i", "d": "Receiving yards"},
        {"k": "yac", "l": "YAC/rec", "f": "1", "d": "Yards after catch per reception"},
        {"k": "racr", "l": "RACR", "f": "2", "d": "Receiving yards per air yard"},
        {"k": "epa_t", "l": "EPA/tgt", "f": "2", "d": "Receiving EPA per target"},
        {"k": "rectd", "l": "TD", "f": "i", "d": "Receiving touchdowns"},
    ],
}
COLS["TE"] = COLS["WR"]


def _f(v):
    return nfl._f(v)


def aggregate(season: int, reg_weeks: int, ids: dict) -> dict:
    ep = nfl.ep_weekly(season, reg_weeks)
    sw = [r for r in nfl.stats_week(season) if int(_f(r["week"])) <= reg_weeks]
    sn = nfl.snaps(season, ids)
    P = {}
    for r in ep:
        p = P.setdefault(r["gsis"], {"gsis": r["gsis"], "name": r["name"], "pos": r["pos"],
                                     "fp": 0.0, "xfp": 0.0, "weeks": set(), "team": r["team"],
                                     "lastwk": 0, "s": {}, "snaps": [], "wkly": {}})
        p["fp"] += r["fp"]; p["xfp"] += r["xfp"]; p["weeks"].add(r["week"])
        w = p["wkly"].setdefault(r["week"], [0.0, 0.0])
        w[0] += r["fp"]; w[1] += r["xfp"]
        if r["week"] >= p["lastwk"]:
            p["lastwk"], p["team"] = r["week"], r["team"]
    for r in sw:
        p = P.get(r["player_id"])
        if not p:
            continue
        s = p["s"]
        for k in ("attempts", "completions", "passing_yards", "passing_tds",
                  "passing_interceptions", "sacks_suffered", "passing_air_yards",
                  "passing_epa", "carries", "rushing_yards", "rushing_tds", "rushing_epa",
                  "targets", "receptions", "receiving_yards", "receiving_tds",
                  "receiving_air_yards", "receiving_yards_after_catch", "receiving_epa"):
            s[k] = s.get(k, 0.0) + _f(r.get(k))
        a = _f(r.get("attempts"))
        if r.get("passing_cpoe") not in ("", "NA", None) and a:
            s["cpoe_w"] = s.get("cpoe_w", 0.0) + _f(r["passing_cpoe"]) * a
            s["cpoe_n"] = s.get("cpoe_n", 0.0) + a
        for k in ("target_share", "air_yards_share", "wopr"):
            if r.get(k) not in ("", "NA", None):
                s.setdefault(k, []).append(_f(r[k]))
    for (g, wk), v in sn.items():
        if g in P and wk <= reg_weeks:
            P[g]["snaps"].append(v)
    return P, ep


def row(p: dict) -> dict:
    s, g = p["s"], len(p["weeks"])
    div = lambda a, b: (a / b) if b else None
    mean = lambda v: (sum(v) / len(v)) if v else None
    out = {"n": p["name"], "gsis": p["gsis"], "team": p["team"], "g": g,
           "snap": mean(p["snaps"]),
           "fpg": p["fp"] / g, "xfpg": p["xfp"] / g, "fpoe": (p["fp"] - p["xfp"]) / g}
    # per-week [week, FP, xFP] for the week-by-week panels
    wk = [[w, round(v[0], 1), round(v[1], 1)] for w, v in sorted(p["wkly"].items())]
    pos = p["pos"]
    if pos == "QB":
        db = s.get("attempts", 0) + s.get("sacks_suffered", 0)
        out.update(att=s.get("attempts", 0), cmp=div(s.get("completions", 0), s.get("attempts", 0)),
                   cpoe=div(s.get("cpoe_w", 0), s.get("cpoe_n", 0)),
                   epa_db=div(s.get("passing_epa", 0), db),
                   adot=div(s.get("passing_air_yards", 0), s.get("attempts", 0)),
                   pyds=s.get("passing_yards", 0), ptd=s.get("passing_tds", 0),
                   int=s.get("passing_interceptions", 0), sk=s.get("sacks_suffered", 0),
                   ryds=s.get("rushing_yards", 0), rtd=s.get("rushing_tds", 0))
    elif pos == "RB":
        out.update(car=s.get("carries", 0), ryds=s.get("rushing_yards", 0),
                   ypc=div(s.get("rushing_yards", 0), s.get("carries", 0)),
                   repa=div(s.get("rushing_epa", 0), s.get("carries", 0)),
                   rtd=s.get("rushing_tds", 0), tgt=s.get("targets", 0),
                   tshare=mean(s.get("target_share", [])), rec=s.get("receptions", 0),
                   recyds=s.get("receiving_yards", 0),
                   oppg=(s.get("carries", 0) + s.get("targets", 0)) / g)
    else:
        out.update(tgt=s.get("targets", 0), tshare=mean(s.get("target_share", [])),
                   ayshare=mean(s.get("air_yards_share", [])), wopr=mean(s.get("wopr", [])),
                   adot=div(s.get("receiving_air_yards", 0), s.get("targets", 0)),
                   rec=s.get("receptions", 0),
                   catch=div(s.get("receptions", 0), s.get("targets", 0)),
                   recyds=s.get("receiving_yards", 0),
                   yac=div(s.get("receiving_yards_after_catch", 0), s.get("receptions", 0)),
                   racr=div(s.get("receiving_yards", 0), s.get("receiving_air_yards", 0)),
                   epa_t=div(s.get("receiving_epa", 0), s.get("targets", 0)),
                   rectd=s.get("receiving_tds", 0))
    out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out.items()}
    out["wk"] = wk
    return out


def gate_value(p: dict) -> float:
    s = p["s"]
    k, _ = GATE[p["pos"]]
    if k == "opps":
        return s.get("carries", 0) + s.get("targets", 0)
    return s.get(k, 0)


def team_grade(season, reg, ep, ids, owners, slots, power_rank):
    pw = [e for e in json.loads((DER / "player_weeks.json").read_text())
          if e["season"] == season]
    rwk = max(e["week"] for e in pw)
    nflwk = max(r["week"] for r in ep)
    rx, _ = nfl.rates(ep, nflwk + 1, "xfp")
    rf, _ = nfl.rates(ep, nflwk + 1, "fp")
    name = {g: v["name"] for g, v in ids["gsis"].items()}
    rows = []
    for tid, who in owners.items():
        ent = [e for e in pw if e["week"] == rwk and e["teamId"] == tid and nfl.entry_pos(e)]
        val = lambda rt: (lambda e: rt.get(ids["espn"].get(str(e["playerId"])), 0.0))
        vx, lx = nfl.roster_value(ent, val(rx), slots)
        vf, _ = nfl.roster_value(ent, val(rf), slots)
        pos_of = {e["playerId"]: nfl.entry_pos(e) for e in ent}
        lineup = []
        for slot, pid, v in lx:
            g = ids["espn"].get(str(pid))
            lineup.append({"slot": {0: "QB", 2: "RB", 4: "WR", 6: "TE", 23: "FLEX"}.get(slot, str(slot)),
                           "n": name.get(g, str(pid)), "pos": pos_of.get(pid),
                           "xfp": round(v, 1), "fp": round(rf.get(g, 0.0), 1)})
        order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "FLEX": 4}
        lineup.sort(key=lambda r: (order.get(r["slot"], 9), -r["xfp"]))
        rows.append({"tid": tid, "p": who["p"], "tm": who["tm"], "xfp": round(vx, 1),
                     "fp": round(vf, 1), "luck": round(vf - vx, 1), "lineup": lineup,
                     "power": power_rank.get(tid)})
    mu = st.mean(r["xfp"] for r in rows)
    sd = st.pstdev(r["xfp"] for r in rows) or 1.0
    for r in sorted(rows, key=lambda r: -r["xfp"]):
        z = (r["xfp"] - mu) / sd
        r["z"] = round(z, 2)
        r["grade"] = ("A" if z >= 1 else "B" if z >= 1 / 3 else "C" if z >= -1 / 3
                      else "D" if z >= -1 else "F")
    rows.sort(key=lambda r: -r["xfp"])
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    return rows, rwk, nflwk


def main() -> int:
    sj = json.loads((SITE / "season.json").read_text())
    season = sj["meta"]["season"]
    nfl.check_scoring(season)
    seasons = {s["season"]: s for s in json.loads((DER / "seasons.json").read_text())}
    S = seasons[season]
    reg = S["regularSeasonWeeks"]
    slots = {int(k): v for k, v in S["slotCounts"].items()}
    ids = nfl.players()
    P, ep = aggregate(season, reg, ids)
    nflwk = max(r["week"] for r in ep)

    # ownership from the committed payload (first names only)
    owners, owned = {}, {}
    for t in sj["rosters"]:
        owners[t["tid"]] = {"p": t["p"], "tm": t["tm"]}
        for pl in t["players"]:
            g = ids["espn"].get(str(pl["pid"]))
            if g:
                owned[g] = t["p"]
    rwk = max(t["wk"] for t in sj["rosters"])
    managers = sorted(owners[t]["p"] for t in owners)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    common_meta = {"season": season, "nflWeeks": [1, nflwk], "rosterWeek": rwk,
                   "managers": managers, "generated": stamp,
                   "recordBook": BASE, "seasonPage": BASE + "season.html",
                   "analysisPage": BASE + "analysis.html",
                   "pages": {p: BASE + p.lower() + ".html" for p in nfl.POSITIONS}}

    for pos in nfl.POSITIONS:
        k, floor = GATE[pos]
        players = []
        for g, p in P.items():
            if p["pos"] != pos:
                continue
            if gate_value(p) < floor and g not in owned:
                continue
            r = row(p)
            r["own"] = owned.get(g)
            players.append(r)
        players.sort(key=lambda r: -r["xfpg"])
        for i, r in enumerate(players):
            r["sc"] = 1 if (i < SCATTER_TOP[pos] and r["g"] >= 2) else 0
        out = {"meta": dict(common_meta, pos=pos, gate=f"min {floor} {k if k != 'opps' else 'carries + targets'} (rostered players always shown)"),
               "cols": COMMON_HEAD + COLS[pos] + COMMON_TAIL,
               "players": players}
        (SITE / f"stats_{pos.lower()}.json").write_text(
            json.dumps(out, ensure_ascii=True, separators=(",", ":")))
        print(f"  stats_{pos.lower()}.json  {len(players)} players")

    # analytics grade -> analysis.json chart
    pr = [r for wk in sj["power"] for r in (wk if isinstance(wk, list) else [wk])]
    latest = max(r["week"] for r in pr)
    prank = {r["tid"]: r["rank"] for r in pr if r["week"] == latest}
    rows, rwk2, _ = team_grade(season, reg, ep, ids, owners, slots, prank)
    chart = {"key": f"team_grade_{season}", "kind": "team_grade", "season": season,
             "title": "Roster grade: opportunity, not results",
             "nflWeeks": [1, nflwk], "rosterWeek": rwk2, "powerWeek": latest,
             "teams": rows, "retrieved": stamp}
    ap = SITE / "analysis.json"
    d = json.loads(ap.read_text())
    i = next((i for i, c in enumerate(d["charts"]) if c.get("key") == chart["key"]), None)
    if i is None:
        d["charts"].append(chart)
    else:
        d["charts"][i] = chart
    # QB air-yards charts (same page) rebuild from play-by-play every run
    qb_charts.upsert(d, qb_charts.build(season))
    d["meta"]["pages"] = common_meta["pages"]
    ap.write_text(json.dumps(d, ensure_ascii=True, separators=(",", ":")))
    print(f"  analysis.json  team_grade: rosters wk{rwk2}, NFL wks 1-{nflwk}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
