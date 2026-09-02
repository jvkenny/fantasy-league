#!/usr/bin/env python3
"""Turn the raw ESPN haul into tidy tables the site can query.

Reads data/raw/<year>/*.json, writes data/derived/*.json. Raw stays untouched —
this step is always safe to re-run and to rewrite.

Tables produced:
    seasons.json       one row per season: champion, runner-up, settings
    teams.json         one row per team-season: record, points for/against, finish
    matchups.json      every head-to-head: scores, winner, playoff flag
    player_weeks.json  every rostered player-week: slot, started, actual, projected
    draft_picks.json   every pick: round, overall, keeper, auction bid
    transactions.json  every add/drop/waiver/trade
    players.json       playerId -> name, position, pro team
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
OUT = REPO / "data" / "derived"

# ESPN's lineup slot ids. Bench and IR are the only non-starting ones.
SLOTS = {0: "QB", 1: "TQB", 2: "RB", 3: "RB/WR", 4: "WR", 5: "WR/TE", 6: "TE",
         7: "OP", 8: "DT", 9: "DE", 10: "LB", 11: "DL", 12: "CB", 13: "S",
         14: "DB", 15: "DP", 16: "D/ST", 17: "K", 18: "P", 19: "HC",
         20: "BE", 21: "IR", 23: "FLEX", 24: "ER"}
BENCH = {20, 21, 24}
POS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 16: "D/ST"}
PRO = {0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
       8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA",
       16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI", 22: "ARI",
       23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR",
       30: "JAX", 33: "BAL", 34: "HOU"}

_ident_file = REPO / "data" / "identities.json"
IDENTITIES: dict[str, str] = (
    json.loads(_ident_file.read_text()).get("people", {}) if _ident_file.exists() else {}
)

players: dict[int, dict] = {}


def note_player(p: dict) -> int | None:
    pid = p.get("id")
    if pid is None:
        return None
    if pid not in players:
        players[pid] = {
            "playerId": pid,
            "name": p.get("fullName"),
            "position": POS.get(p.get("defaultPositionId"), str(p.get("defaultPositionId"))),
            "proTeam": PRO.get(p.get("proTeamId"), str(p.get("proTeamId"))),
        }
    return pid


def stat(entry_player: dict, week: int, source: int) -> float | None:
    """Applied fantasy points for one week. source 0 = actual, 1 = projected."""
    for s in entry_player.get("stats") or []:
        if s.get("statSourceId") == source and s.get("scoringPeriodId") == week \
                and s.get("statSplitTypeId") == 1:
            return s.get("appliedTotal")
    return None


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    seasons, teams, matchups, player_weeks, picks, txns = [], [], [], [], [], []

    years = sorted(int(p.name) for p in RAW.iterdir() if p.is_dir() and p.name.isdigit())

    for yr in years:
        core = json.loads((RAW / yr_dir(yr)).read_text()) if False else \
            json.loads((RAW / str(yr) / "core.json").read_text())
        st = core.get("settings") or {}
        sched_settings = st.get("scheduleSettings") or {}
        reg_weeks = sched_settings.get("matchupPeriodCount")

        # One human can hold several ESPN accounts, and can rename a profile
        # mid-history. data/identities.json is the editable map that keeps
        # all-time leaderboards from splitting a person in two.
        owner_name = {}
        for m in core.get("members") or []:
            mid = (m.get("id") or "").upper()
            nm = " ".join(x for x in [m.get("firstName"), m.get("lastName")] if x).strip()
            owner_name[mid] = IDENTITIES.get(mid) or nm or m.get("displayName")

        # --- teams -------------------------------------------------------
        final_rank = {}
        for t in core.get("teams") or []:
            rec = ((t.get("record") or {}).get("overall")) or {}
            owners = [str(o).upper() for o in (t.get("owners") or [])]
            name = (t.get("name") or
                    " ".join(x for x in [t.get("location"), t.get("nickname")] if x).strip())
            final_rank[t.get("id")] = t.get("rankCalculatedFinal") or t.get("playoffSeed")
            teams.append({
                "season": yr, "teamId": t.get("id"), "name": name.strip(),
                "abbrev": t.get("abbrev"),
                "owner": ", ".join(filter(None, (owner_name.get(o) for o in owners))) or None,
                "wins": rec.get("wins"), "losses": rec.get("losses"), "ties": rec.get("ties"),
                "pointsFor": round(rec.get("pointsFor") or 0, 2),
                "pointsAgainst": round(rec.get("pointsAgainst") or 0, 2),
                "playoffSeed": t.get("playoffSeed"),
                "finalRank": t.get("rankCalculatedFinal"),
            })

        # --- matchups ----------------------------------------------------
        for m in core.get("schedule") or []:
            wk = m.get("matchupPeriodId")
            home, away = m.get("home") or {}, m.get("away") or {}
            if not home or not away:
                continue  # bye
            hs, as_ = home.get("totalPoints"), away.get("totalPoints")
            matchups.append({
                "season": yr, "week": wk,
                "homeTeamId": home.get("teamId"), "awayTeamId": away.get("teamId"),
                "homeScore": round(hs or 0, 2), "awayScore": round(as_ or 0, 2),
                "winner": m.get("winner"),
                "margin": round(abs((hs or 0) - (as_ or 0)), 2),
                "isPlayoff": bool(reg_weeks and wk > reg_weeks),
                "playoffTier": m.get("playoffTierType"),
            })

        # --- draft -------------------------------------------------------
        for p in ((core.get("draftDetail") or {}).get("picks")) or []:
            picks.append({
                "season": yr, "overall": p.get("overallPickNumber"),
                "round": p.get("roundId"), "pickInRound": p.get("roundPickNumber"),
                "teamId": p.get("teamId"), "playerId": p.get("playerId"),
                "keeper": bool(p.get("keeper")),
                "bidAmount": p.get("bidAmount") or 0,
            })

        # --- per-week player detail + transactions ------------------------
        for wkfile in sorted((RAW / str(yr)).glob("week-*-boxscore.json")):
            wk = int(wkfile.stem.split("-")[1])
            d = json.loads(wkfile.read_text())
            for m in d.get("schedule") or []:
                if m.get("matchupPeriodId") != wk:
                    continue
                for side in ("home", "away"):
                    s = m.get(side) or {}
                    roster = s.get("rosterForCurrentScoringPeriod") or {}
                    for e in roster.get("entries") or []:
                        ppe = e.get("playerPoolEntry") or {}
                        pl = ppe.get("player") or {}
                        pid = note_player(pl)
                        slot = e.get("lineupSlotId")
                        player_weeks.append({
                            "season": yr, "week": wk, "teamId": s.get("teamId"),
                            "playerId": pid, "slot": SLOTS.get(slot, str(slot)),
                            "slotId": slot,
                            # eligibleSlots is what makes optimal-lineup analysis
                            # possible: without it we cannot know which bench
                            # player could legally have filled a given slot.
                            "eligible": pl.get("eligibleSlots") or [],
                            "started": slot not in BENCH,
                            "actual": stat(pl, wk, 0), "projected": stat(pl, wk, 1),
                        })

        for txfile in sorted((RAW / str(yr)).glob("week-*-transactions.json")):
            wk = int(txfile.stem.split("-")[1])
            d = json.loads(txfile.read_text())
            for t in d.get("transactions") or []:
                for it in t.get("items") or []:
                    txns.append({
                        "season": yr, "week": wk,
                        "type": t.get("type"), "action": it.get("type"),
                        "teamId": it.get("toTeamId") or t.get("teamId"),
                        "fromTeamId": it.get("fromTeamId"),
                        "playerId": it.get("playerId"),
                        "bidAmount": t.get("bidAmount") or 0,
                        "status": t.get("status"),
                    })

        # player names for anyone only seen in transactions/draft
        pfile = RAW / str(yr) / "players.json"
        if pfile.exists():
            for pe in (json.loads(pfile.read_text()).get("players") or []):
                note_player(pe.get("player") or {})

        # --- season summary ----------------------------------------------
        champ = next((t["teamId"] for t in teams
                      if t["season"] == yr and t["finalRank"] == 1), None)
        runner = next((t["teamId"] for t in teams
                       if t["season"] == yr and t["finalRank"] == 2), None)
        slot_counts = {int(k): v for k, v in
                       ((st.get("rosterSettings") or {}).get("lineupSlotCounts") or {}).items()
                       if v}
        seasons.append({
            "season": yr, "name": st.get("name"),
            "slotCounts": slot_counts,
            "teams": len(core.get("teams") or []),
            "regularSeasonWeeks": reg_weeks,
            "playoffTeams": sched_settings.get("playoffTeamCount"),
            "championTeamId": champ, "runnerUpTeamId": runner,
            "hasPlayerDetail": any(pw["season"] == yr for pw in player_weeks),
        })

    tables = {"seasons": seasons, "teams": teams, "matchups": matchups,
              "player_weeks": player_weeks, "draft_picks": picks,
              "transactions": txns, "players": list(players.values())}
    for name, rows in tables.items():
        (OUT / f"{name}.json").write_text(json.dumps(rows, separators=(",", ":")))
        print(f"  {name:<14} {len(rows):>7,} rows")
    print(f"\nwrote {OUT}")
    return 0


def yr_dir(yr):  # pragma: no cover - kept for path clarity
    return f"{yr}/core.json"


if __name__ == "__main__":
    raise SystemExit(main())
