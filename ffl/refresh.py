#!/usr/bin/env python3
"""The one weekly refresh: every page on the site, the Analysis page included.

    python3 -m ffl.refresh --pull data/raw/2026/_pull_wk07.json   # after a browser pull
    python3 -m ffl.refresh                                        # rebuild from what is on disk

Steps, in order, stopping at the first failure:
  1. ingest the browser pull (if given) into data/raw/<yr>/: week-NN-boxscore.json,
     week-NN-transactions.json, outlook-wkNN.json, and core.json patched with the
     week's scores, winners and records. Refuses a week ESPN has not finalized.
  2. ffl.normalize -> ffl.build_site -> ffl.build_season   (record book, season tracker)
  3. ffl.nfl <season> (forced re-fetch: nflverse revises recent weeks)
  4. ffl.build_stats   (position pages, roster grade, QB air-yards charts)
  5. ffl.render        (every page)

The pull itself happens in John's Chrome with ffl/pull_week.js (see README,
"Weekly refresh via the browser"); this script never touches ESPN.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BENCH = {20, 21}


def ingest(pull_path: Path) -> int:
    P = json.loads(pull_path.read_text())
    W = P["W"]
    if P["cur"] <= W or any(m["w"] == "UNDECIDED" for m in P["ms"]):
        raise SystemExit(f"!! week {W} is not final in ESPN yet (currentMatchupPeriod "
                         f"{P['cur']}, winners {[m['w'] for m in P['ms']]}); pull again later")
    season = json.loads((REPO / "site" / "season.json").read_text())["meta"]["season"]
    R = REPO / "data" / "raw" / str(season)

    sched = []
    for m in P["ms"]:
        sides = {}
        for k, (tid, tot, _live, ents) in zip(("home", "away"), m["s"]):
            entries, starters = [], 0.0
            for slot, pid, name, dpos, pro, elig, act, proj in ents:
                stats = []
                if act is not None:
                    stats.append({"scoringPeriodId": W, "statSourceId": 0, "statSplitTypeId": 1, "appliedTotal": act})
                if proj is not None:
                    stats.append({"scoringPeriodId": W, "statSourceId": 1, "statSplitTypeId": 1, "appliedTotal": proj})
                entries.append({"lineupSlotId": slot, "playerPoolEntry": {"player": {
                    "id": pid, "fullName": name, "defaultPositionId": dpos, "proTeamId": pro,
                    "eligibleSlots": [int(x) for x in elig.split("|")], "stats": stats}}})
                if slot not in BENCH and act is not None:
                    starters += act
            if abs(starters - tot) > 0.02:
                raise SystemExit(f"!! team {tid}: starters sum {starters:.2f} != ESPN total {tot}")
            sides[k] = {"teamId": tid, "totalPoints": tot,
                        "rosterForCurrentScoringPeriod": {"entries": entries}}
        sched.append({"matchupPeriodId": W, "winner": m["w"], **sides})
    (R / f"week-{W:02d}-boxscore.json").write_text(json.dumps(
        {"scoringPeriodId": W, "seasonId": season, "id": 582222, "schedule": sched}))

    c = json.loads((R / "core.json").read_text())
    by = {(m["s"][0][0], m["s"][1][0]): m for m in P["ms"]}
    for m in c["schedule"]:
        if m["matchupPeriodId"] == W:
            src = by[(m["home"]["teamId"], m["away"]["teamId"])]
            m["home"]["totalPoints"], m["away"]["totalPoints"] = src["s"][0][1], src["s"][1][1]
            m["winner"] = src["w"]
    rec = {r[0]: r for r in P["rec"]}
    for t in c["teams"]:
        _, w, l, ti, pf, pa = rec[t["id"]]
        t["record"]["overall"].update(wins=w, losses=l, ties=ti, pointsFor=pf, pointsAgainst=pa,
                                      percentage=round(w / max(1, w + l + ti), 4))
    c["status"]["currentMatchupPeriod"], c["scoringPeriodId"] = P["cur"], P["latest"]
    (R / "core.json").write_text(json.dumps(c))

    out = R / f"outlook-wk{W:02d}.json"
    if out.exists():
        print(f"  {out.name} exists; kept (outlook snapshots are never overwritten)")
    else:
        out.write_text(json.dumps({"season": season, "week": W, "pulledForPeriod": W + 1,
                                   "players": P["look"]}))
    tx = [{"type": t, "status": s, "bidAmount": b, "teamId": tid,
           "items": [{"type": a, "playerId": p, "toTeamId": to or None, "fromTeamId": fr or None}
                     for a, p, to, fr in its]} for t, s, b, tid, its in P["tx"]]
    (R / f"week-{W:02d}-transactions.json").write_text(json.dumps({"transactions": tx}))
    print(f"  ingested week {W}: scores verified against starters, {len(P['look'])} outlook rows, {len(tx)} transactions")
    return season


def run(mod: str, *args: str) -> None:
    print(f"== {mod} {' '.join(args)}".rstrip())
    subprocess.run([sys.executable, "-m", mod, *args], cwd=REPO, check=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pull", type=Path, help="browser pull JSON to ingest first")
    a = ap.parse_args()
    if a.pull:
        print(f"== ingest {a.pull}")
        ingest(a.pull if a.pull.is_absolute() else REPO / a.pull)
    season = json.loads((REPO / "site" / "season.json").read_text())["meta"]["season"]
    for mod in ("ffl.normalize", "ffl.build_site", "ffl.build_season"):
        run(mod)
    run("ffl.nfl", str(season))
    run("ffl.build_stats")
    run("ffl.render")
    print("done: commit site/ and docs/, then push to publish")
    return 0


if __name__ == "__main__":
    sys.exit(main())
