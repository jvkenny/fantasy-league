#!/usr/bin/env python3
"""Does an opportunity-based roster grade predict the rest of a season better
than what the league already looks at?

At each week N of each past season with roster detail, value every team's
CURRENT roster five ways and ask which ordering best matches the teams' actual
average score over weeks N..end of regular season:

  xfp     best legal offensive lineup, players valued by expected fantasy
          points per game so far (ffopportunity, league scoring)
  fp      same lineup machinery, valued by actual fantasy points per game so far
          (= the power ranking's "strength" idea, on a per-player basis)
  blend   0.5 xfp + 0.5 fp
  xfp_av  xfp, but a player ESPN projects for 0 in week N (injured, out, bye)
          counts 0 that week - ESPN used ONLY as an availability flag
  fp_av   fp with the same availability flag (so the comparison stays fair)
  espn    best legal offensive lineup by ESPN's projection for week N
          (ESPN's rest-of-season projections were not archived before 2026)
  record  the team's own average score so far (what the standings see)

Metric: Spearman rank correlation across the league's teams, per league-week,
averaged within each season, then across seasons. Seasons are the unit of
uncertainty (league-weeks inside a season share rosters, so they are not
independent).

    python3 -m ffl.backtest_grade      # -> reports/grade_backtest.json
"""

from __future__ import annotations

import json
import statistics as st
import sys
from pathlib import Path

from ffl import nfl

REPO = Path(__file__).resolve().parent.parent
DER = REPO / "data" / "derived"
OUT = REPO / "reports" / "grade_backtest.json"
SEASONS = (2019, 2021, 2022, 2023, 2024, 2025)
FIRST_WEEK = 4      # need 3 weeks of history
TAIL = 3            # and 3 weeks left to predict


def spearman(a, b):
    def rank(v):
        o = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(o):
            j = i
            while j + 1 < len(o) and v[o[j + 1]] == v[o[i]]:
                j += 1
            for k in range(i, j + 1):
                r[o[k]] = (i + j) / 2
            i = j + 1
        return r
    ra, rb = rank(a), rank(b)
    ma, mb = st.mean(ra), st.mean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** .5
    return num / den if den else 0.0


def main() -> int:
    ids = nfl.players()
    pw = json.loads((DER / "player_weeks.json").read_text())
    seasons = {s["season"]: s for s in json.loads((DER / "seasons.json").read_text())}
    games = json.loads((DER / "matchups.json").read_text())
    preds = ("xfp", "fp", "blend", "xfp_av", "fp_av", "espn", "record")
    per_season, rows = {}, []

    for yr in SEASONS:
        S = seasons[yr]
        reg = S["regularSeasonWeeks"]
        slots = {int(k): v for k, v in S["slotCounts"].items()}
        ep = nfl.ep_weekly(yr, reg)
        score = {}
        for g in games:
            if g["season"] == yr and not g["isPlayoff"]:
                score[(g["homeTeamId"], g["week"])] = g["homeScore"]
                if g.get("awayTeamId"):
                    score[(g["awayTeamId"], g["week"])] = g["awayScore"]
        teams = sorted({t for t, _ in score})
        out = {p: [] for p in preds}
        for N in range(FIRST_WEEK, reg - TAIL + 2):
            rx, _ = nfl.rates(ep, N, "xfp")
            rf, _ = nfl.rates(ep, N, "fp")
            vals = {p: [] for p in preds}
            target = []
            for t in teams:
                ent = [e for e in pw if e["season"] == yr and e["week"] == N
                       and e["teamId"] == t and nfl.entry_pos(e)]
                if not ent:
                    break

                def from_rate(rt, avail=False):
                    def f(e):
                        if avail and not (e.get("projected") or 0) > 0:
                            return 0.0
                        g = ids["espn"].get(str(e["playerId"]))
                        return rt.get(g, 0.0) if g else 0.0
                    return f
                vx = nfl.roster_value(ent, from_rate(rx), slots)[0]
                vf = nfl.roster_value(ent, from_rate(rf), slots)[0]
                ve = nfl.roster_value(ent, lambda e: e.get("projected") or 0.0, slots)[0]
                past = [score[(t, w)] for w in range(1, N) if (t, w) in score]
                fut = [score[(t, w)] for w in range(N, reg + 1) if (t, w) in score]
                vals["xfp"].append(vx); vals["fp"].append(vf)
                vals["blend"].append(.5 * vx + .5 * vf); vals["espn"].append(ve)
                vals["xfp_av"].append(nfl.roster_value(ent, from_rate(rx, True), slots)[0])
                vals["fp_av"].append(nfl.roster_value(ent, from_rate(rf, True), slots)[0])
                vals["record"].append(st.mean(past)); target.append(st.mean(fut))
            else:
                for p in preds:
                    rho = spearman(vals[p], target)
                    out[p].append(rho)
                    rows.append({"season": yr, "week": N, "pred": p, "rho": round(rho, 4)})
        per_season[yr] = {p: round(st.mean(v), 4) for p, v in out.items()}
        print(yr, "weeks", len(out["xfp"]), per_season[yr])

    summary = {}
    for p in preds:
        v = [per_season[y][p] for y in SEASONS]
        summary[p] = {"mean_rho": round(st.mean(v), 3),
                      "se": round(st.stdev(v) / len(v) ** .5, 3)}
    # paired: does xfp beat each rival season by season?
    paired = {}
    for p in preds:
        if p == "xfp":
            continue
        d = [per_season[y]["xfp"] - per_season[y][p] for y in SEASONS]
        paired[p] = {"mean_diff": round(st.mean(d), 3),
                     "se": round(st.stdev(d) / len(d) ** .5, 3),
                     "seasons_xfp_wins": sum(x > 0 for x in d)}
    res = {"seasons": list(SEASONS), "first_week": FIRST_WEEK, "tail": TAIL,
           "shrink_games": nfl.SHRINK_GAMES, "summary": summary,
           "per_season": per_season, "paired_vs_xfp": paired, "rows": rows}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1))
    print(json.dumps({"summary": summary, "paired_vs_xfp": paired}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
