#!/usr/bin/env python3
"""Full historical export of an ESPN fantasy football league.

Pulls, for every season the league has ever played:

  core.json                 settings, members, teams, rosters, standings,
                            every draft pick, and the complete schedule
  week-NN-boxscore.json     every rostered player that week, the slot they were
                            in (started vs benched), projected AND actual points,
                            plus the ~38 underlying raw stat fields
  week-NN-transactions.json every add, drop, waiver claim and trade that week
  players.json              the full season player pool, rostered or not

Raw ESPN responses are written verbatim — normalisation happens downstream, so
nothing is lost at capture time and a schema change never costs us history.

The export is resumable: files already on disk are skipped, except the tail of
the in-progress season, which is always re-fetched because ESPN revises recent
stats. That makes the weekly refresh cheap.

Usage:
    ESPN_S2=... SWID=... python3 -m ffl.export --league 582222
    op run --env-file .env -- python3 -m ffl.export --league 582222
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

from .espn import AuthError, EspnError, League

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "data" / "raw"


def log(msg: str) -> None:
    print(msg, flush=True)


def write(path: Path, obj) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    data = json.dumps(obj, separators=(",", ":"))
    tmp.write_text(data)
    tmp.replace(path)  # atomic: a killed run never leaves a half-written file
    return len(data)


def discover_seasons(league_id: int, first: int, last: int) -> list[int]:
    """Which seasons this league actually played. Cheap probe, one call each."""
    found = []
    for year in range(first, last + 1):
        try:
            data = League(league_id, year).get(["mSettings"])
            found.append(year)
            log(f"  {year}  ✓  {data.get('settings', {}).get('name', '?')}")
        except AuthError:
            raise
        except EspnError:
            log(f"  {year}  –  no season")
    return found


def season_bounds(core: dict) -> tuple[int, int]:
    """(last week with data, final week of the season)."""
    status = core.get("status") or {}
    final = status.get("finalScoringPeriod") or 17
    latest = status.get("latestScoringPeriod") or core.get("scoringPeriodId") or final
    return min(latest, final), final


def export_season(league_id: int, year: int, out: Path, *,
                  force: bool, refresh_tail: int) -> dict:
    lg = League(league_id, year)
    sdir = out / str(year)
    stats = {"season": year, "files": 0, "bytes": 0, "skipped": 0,
             "weeks": 0, "transactions": 0, "picks": 0}

    core_path = sdir / "core.json"
    if core_path.exists() and not force:
        core = json.loads(core_path.read_text())
        stats["skipped"] += 1
    else:
        core = lg.core()
        stats["bytes"] += write(core_path, core)
        stats["files"] += 1

    stats["picks"] = len(((core.get("draftDetail") or {}).get("picks")) or [])
    stats["teams"] = len(core.get("teams") or [])
    stats["name"] = (core.get("settings") or {}).get("name")
    latest, final = season_bounds(core)
    in_progress = latest < final

    # Always re-fetch the tail of a live season: ESPN revises recent stats.
    tail_from = latest - refresh_tail + 1 if in_progress else final + 1

    for wk in range(1, latest + 1):
        for kind, fn in (("boxscore", lg.week), ("transactions", lg.transactions)):
            p = sdir / f"week-{wk:02d}-{kind}.json"
            if p.exists() and not force and wk < tail_from:
                stats["skipped"] += 1
                continue
            try:
                data = fn(wk)
            except EspnError as e:
                log(f"    ! {year} wk{wk} {kind}: {e}")
                continue
            stats["bytes"] += write(p, data)
            stats["files"] += 1
            if kind == "transactions":
                stats["transactions"] += len(data.get("transactions") or [])
        stats["weeks"] += 1

    ppath = sdir / "players.json"
    if ppath.exists() and not force and not in_progress:
        stats["skipped"] += 1
    else:
        try:
            stats["bytes"] += write(ppath, lg.players(scoring_period=latest))
            stats["files"] += 1
        except EspnError as e:
            log(f"    ! {year} players: {e}")

    log(f"  {year}  {stats['name']}  ·  {stats['teams']} teams  ·  wk1-{latest}"
        f"{' (live)' if in_progress else ''}  ·  {stats['picks']} picks  ·  "
        f"{stats['transactions']} txns  ·  {stats['files']} new / {stats['skipped']} cached"
        f"  ·  {stats['bytes']/1e6:.1f} MB")
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--league", type=int, default=int(os.environ.get("LEAGUE_ID", 582222)))
    ap.add_argument("--first", type=int, default=2018)
    ap.add_argument("--last", type=int, default=None, help="default: current year")
    ap.add_argument("--seasons", type=str, default=None, help="explicit list, e.g. 2024,2025")
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--force", action="store_true", help="re-download everything")
    ap.add_argument("--refresh-tail", type=int, default=2,
                    help="weeks of a live season to always re-fetch (default 2)")
    args = ap.parse_args(argv)

    s2 = os.environ.get("ESPN_S2") or ""
    swid = os.environ.get("SWID") or ""
    if not (s2 and swid):
        log("!! ESPN_S2 / SWID are not set — this league is private and the pull "
            "will 401.\n   See README.md § Auth.")
        return 2

    # Shape checks first: a truncated paste is by far the most common failure,
    # and it is much clearer to say so than to let ESPN answer 401.
    problems = []
    if len(s2) < 100:
        problems.append(f"ESPN_S2 is only {len(s2)} chars — expected ~300. "
                        "The paste was probably truncated.")
    if not (swid.startswith("{") and swid.endswith("}")):
        problems.append(f"SWID should be brace-wrapped like {{XXXXXXXX-...}}, got "
                        f"{len(swid)} chars starting {swid[:1]!r}.")
    if problems:
        for pr in problems:
            log(f"!! {pr}")
        return 2

    log(f"Credentials look well-formed (espn_s2 {len(s2)} chars, SWID {len(swid)} chars). "
        "Checking them against ESPN…")
    try:
        League(args.league, 2025).get(["mSettings"])
        log("Auth OK.\n")
    except AuthError:
        log("!! ESPN rejected these cookies.\n"
            "   espn_s2 expires, and it is invalidated when you log out of ESPN.\n"
            "   Grab a fresh pair while logged in and try again.")
        return 2
    except EspnError as e:
        log(f"!! Could not reach ESPN: {e}")
        return 1

    last = args.last or time.gmtime().tm_year
    t0 = time.time()

    if args.seasons:
        seasons = [int(s) for s in args.seasons.split(",") if s.strip()]
    else:
        log(f"Discovering seasons for league {args.league} ({args.first}-{last})…")
        try:
            seasons = discover_seasons(args.league, args.first, last)
        except AuthError as e:
            log(f"!! {e}")
            return 2

    if not seasons:
        log("!! No seasons found.")
        return 1

    log(f"\nExporting {len(seasons)} seasons → {args.out}")
    all_stats = []
    for year in seasons:
        try:
            all_stats.append(export_season(args.league, year, args.out,
                                           force=args.force,
                                           refresh_tail=args.refresh_tail))
        except AuthError as e:
            log(f"!! {e}")
            return 2
        except EspnError as e:
            log(f"!! {year}: {e}")

    manifest = {
        "league_id": args.league,
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "seasons": all_stats,
        "totals": {
            "seasons": len(all_stats),
            "files": sum(s["files"] for s in all_stats),
            "megabytes": round(sum(s["bytes"] for s in all_stats) / 1e6, 1),
            "draft_picks": sum(s["picks"] for s in all_stats),
            "transactions": sum(s["transactions"] for s in all_stats),
        },
    }
    write(args.out / "manifest.json", manifest)
    t = manifest["totals"]
    log(f"\nDone in {time.time()-t0:.0f}s — {t['seasons']} seasons, {t['files']} files, "
        f"{t['megabytes']} MB, {t['draft_picks']} draft picks, {t['transactions']} transactions.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
