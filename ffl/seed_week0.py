#!/usr/bin/env python3
"""Seed the season-start (week 0) outlook snapshot from the draft.

The drafted roster IS each team's season-start roster, and the preseason player
pool carries each player's full-season projection, so week 0 is a real ranking:
the best legal lineup a manager walked out of the draft with. It has no results
component by definition, which is exactly right - nothing has been played.

Written once per season to data/raw/<yr>/outlook-wk00.json. Idempotent.

    python3 -m ffl.seed_week0 --season 2026
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"


def seed(year: int, force: bool = False) -> int:
    d = RAW / str(year)
    out = d / "outlook-wk00.json"
    if out.exists() and not force:
        print(f"  {out.name} already present - leaving it alone (use --force)")
        return 0
    core = json.loads((d / "core.json").read_text())
    picks = ((core.get("draftDetail") or {}).get("picks")) or []
    if not picks:
        print(f"!! {year}: no draft picks in core.json")
        return 1

    pool = {}
    for it in json.loads((d / "players.json").read_text()).get("players") or []:
        p = it.get("player") or it
        if p.get("id") is not None:
            pool[p["id"]] = p

    rows, missing = [], 0
    for pk in picks:
        p = pool.get(pk.get("playerId"))
        if not p:
            missing += 1
            continue
        proj = next((s.get("appliedTotal") for s in (p.get("stats") or [])
                     if s.get("statSourceId") == 1 and s.get("statSplitTypeId") == 0
                     and s.get("seasonId") == year), None)
        if proj is None:
            missing += 1
            continue
        rows.append([pk["teamId"], p["id"], round(proj, 1),
                     20, "|".join(str(x) for x in (p.get("eligibleSlots") or []))])

    out.write_text(json.dumps({"season": year, "week": 0, "source": "draft",
                               "players": rows}, separators=(",", ":")))
    teams = len({r[0] for r in rows})
    print(f"  {out.name}: {len(rows)} drafted players across {teams} teams"
          + (f"  ({missing} without a preseason projection)" if missing else ""))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--season", type=int, required=True)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    print("seeding week 0:")
    return seed(args.season, args.force)


if __name__ == "__main__":
    sys.exit(main())
