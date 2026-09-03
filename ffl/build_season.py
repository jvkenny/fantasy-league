#!/usr/bin/env python3
"""Payload for the single-season tracker page.

Defaults to the most recent season in the data, so the same code carries into
2027 without edits. Everything here is scoped to that season except the
calibration constants and the career-form table, which need history.

Win probability is modelled, not scraped. ESPN publishes a projected total per
player; summing the starters gives a projected team score, and the historical
spread of (actual - projected) gives the uncertainty. P(A beats B) is then the
normal CDF of the projected margin over the combined spread. The same history
says ESPN's projection picks the winner barely better than a coin flip, so the
page reports that hit rate next to the probabilities rather than hiding it.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DER = REPO / "data" / "derived"
OUT = REPO / "site" / "season.json"

BENCH = {20, 21, 24}


def load(n):
    return json.loads((DER / f"{n}.json").read_text())


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))



def weekly_awards(season, week, pw, matchups, owner, tname, pname, ppos):
    """Post-game awards for one completed week.

    Returns [] when the week has no result yet, so the page can render an
    honest empty state rather than inventing a winner.
    """
    games = [m for m in matchups if m["season"] == season and m["week"] == week
             and m["winner"] and m["winner"] != "UNDECIDED"]
    if not games:
        return []
    rows = [r for r in pw if r["season"] == season and r["week"] == week]

    score, won, opp = {}, {}, {}
    for m in games:
        h, a = m["homeTeamId"], m["awayTeamId"]
        score[h], score[a] = m["homeScore"], m["awayScore"]
        won[h], won[a] = m["winner"] == "HOME", m["winner"] == "AWAY"
        opp[h], opp[a] = a, h
    who = lambda t: owner.get((season, t))
    # how many of the other nine this score would have beaten - the all-play view
    beat = {t: sum(1 for u, v in score.items() if u != t and score[t] > v) for t in score}
    n_others = len(score) - 1

    A = []
    def add(key, label, value, detail):
        A.append({"k": key, "l": label, "v": value, "d": detail})

    started = [r for r in rows if r["started"] and r["actual"] is not None]
    benched = [r for r in rows if not r["started"] and r["slotId"] == 20
               and r["actual"] is not None]

    if started:
        m0 = max(started, key=lambda r: r["actual"])
        add("mvp", "MVP", f"{m0['actual']:.1f}",
            f"{pname.get(m0['playerId'])} ({ppos.get(m0['playerId'])}) was the "
            f"highest-scoring started player of the week, for {who(m0['teamId'])}.")

    # facepalm: losing with the win on the bench beats merely benching a lot
    bench_by = {}
    for r in benched:
        bench_by[r["teamId"]] = bench_by.get(r["teamId"], 0) + r["actual"]
    cand = []
    for t, v in bench_by.items():
        if t not in score:
            continue
        margin = score[opp[t]] - score[t]
        cand.append((not won.get(t) and v > margin, v - max(margin, 0), t, v, margin))
    if cand:
        cand.sort(key=lambda c: (-c[0], -c[1]))
        fatal, surplus, t, v, margin = cand[0]
        if fatal:
            add("facepalm", "Facepalm of the week", f"{v:.1f} benched",
                f"{who(t)} lost to {who(opp[t])} by {margin:.2f} with {v:.1f} points sitting "
                f"on the bench - {surplus:.1f} more than the game needed.")
        else:
            add("facepalm", "Facepalm of the week", f"{v:.1f} benched",
                f"{who(t)} left {v:.1f} points in bench slots"
                + (f" and got away with it." if won.get(t)
                   else f" and lost by {margin:.2f} - though the bench would not have saved it."))

    winners = [t for t in score if won.get(t)]
    losers = [t for t in score if not won.get(t)]
    if winners:
        lucky = min(winners, key=lambda t: beat[t])
        add("lucky", "The Lucky One", f"{score[lucky]:.1f}",
            f"{who(lucky)} won while outscoring only {beat[lucky]} of the other {n_others} "
            f"teams. Drew {who(opp[lucky])}, who managed {score[opp[lucky]]:.1f}.")
    if losers:
        robbed = max(losers, key=lambda t: beat[t])
        add("robbed", "Robbed", f"{score[robbed]:.1f}",
            f"{who(robbed)} lost despite a score that would have beaten "
            f"{beat[robbed]} of the other {n_others} teams. {who(opp[robbed])} happened to "
            f"put up {score[opp[robbed]]:.1f}.")

    proj = [r for r in started if r["projected"] is not None]
    if proj:
        b = min(proj, key=lambda r: r["actual"] - r["projected"])
        add("bust", "Bust of the week", f"{b['actual'] - b['projected']:+.1f}",
            f"{pname.get(b['playerId'])} scored {b['actual']:.1f} against a "
            f"{b['projected']:.1f} projection, in {who(b['teamId'])}'s starting lineup.")
        g = max(proj, key=lambda r: r["actual"] - r["projected"])
        add("sleeper", "Overachiever", f"{g['actual'] - g['projected']:+.1f}",
            f"{pname.get(g['playerId'])} put up {g['actual']:.1f} on a "
            f"{g['projected']:.1f} projection for {who(g['teamId'])}.")

    if benched:
        bh = max(benched, key=lambda r: r["actual"])
        add("benchhero", "Best player nobody started", f"{bh['actual']:.1f}",
            f"{pname.get(bh['playerId'])} ({ppos.get(bh['playerId'])}) scored "
            f"{bh['actual']:.1f} on {who(bh['teamId'])}'s bench.")

    hi = max(score, key=score.get); lo = min(score, key=score.get)
    add("high", "Highest score", f"{score[hi]:.1f}", f"{who(hi)}.")
    add("low", "Lowest score", f"{score[lo]:.1f}", f"{who(lo)}.")

    blow = max(games, key=lambda m: abs(m["homeScore"] - m["awayScore"]))
    bw = blow["homeTeamId"] if blow["winner"] == "HOME" else blow["awayTeamId"]
    add("blowout", "Biggest beating", f"{abs(blow['homeScore'] - blow['awayScore']):.1f}",
        f"{who(bw)} over {who(opp[bw])}, "
        f"{max(blow['homeScore'], blow['awayScore']):.1f} to "
        f"{min(blow['homeScore'], blow['awayScore']):.1f}.")
    nail = min(games, key=lambda m: abs(m["homeScore"] - m["awayScore"]))
    nw = nail["homeTeamId"] if nail["winner"] == "HOME" else nail["awayTeamId"]
    add("nail", "Closest game", f"{abs(nail['homeScore'] - nail['awayScore']):.2f}",
        f"{who(nw)} edged {who(opp[nw])}.")

    avg = sum(score.values()) / len(score)
    add("avg", "League average", f"{avg:.1f}",
        f"Across {len(score)} teams, from {score[lo]:.1f} to {score[hi]:.1f}.")
    return A


def main(season: int | None = None):
    seasons = load("seasons"); teams = load("teams"); matchups = load("matchups")
    pw = load("player_weeks"); picks = load("draft_picks"); players = load("players")
    ident = json.loads((REPO / "data" / "identities.json").read_text())["people"] \
        if (REPO / "data" / "identities.json").exists() else {}

    # display names must match the record book exactly
    full = sorted({t["owner"] for t in teams if t["owner"]})
    short = {n: n.split()[0] for n in full}
    clash = {v for v in short.values() if list(short.values()).count(v) > 1}
    for n in full:
        if short[n] in clash:
            p_ = n.split()
            short[n] = f"{p_[0]} {p_[-1][0]}." if len(p_) > 1 else n

    SEASON = season or max(s["season"] for s in seasons)
    meta_s = next(s for s in seasons if s["season"] == SEASON)
    owner = {(t["season"], t["teamId"]): short.get(t["owner"]) for t in teams}
    tname = {(t["season"], t["teamId"]): (t["name"] or t["abbrev"]) for t in teams}
    pname = {p["playerId"]: p["name"] for p in players}
    ppos = {p["playerId"]: p["position"] for p in players}

    # ---- calibration from every completed season -------------------------
    proj_tot = defaultdict(float)
    for r in pw:
        if r["started"] and r["projected"] is not None:
            proj_tot[(r["season"], r["week"], r["teamId"])] += r["projected"]
    actual = {}
    for m in matchups:
        if not m["winner"] or m["winner"] == "UNDECIDED":
            continue
        actual[(m["season"], m["week"], m["homeTeamId"])] = m["homeScore"]
        actual[(m["season"], m["week"], m["awayTeamId"])] = m["awayScore"]
    resid = [actual[k] - proj_tot[k] for k in proj_tot
             if k in actual and proj_tot[k] > 20]
    sd = statistics.pstdev(resid) if len(resid) > 2 else 24.0
    hit = tot = 0
    for m in matchups:
        if not m["winner"] or m["winner"] in ("UNDECIDED", "TIE"):
            continue
        a = proj_tot.get((m["season"], m["week"], m["homeTeamId"]), 0)
        b = proj_tot.get((m["season"], m["week"], m["awayTeamId"]), 0)
        if a > 20 and b > 20:
            tot += 1
            hit += (("HOME" if a > b else "AWAY") == m["winner"])

    # ---- this season -----------------------------------------------------
    rows = [t for t in teams if t["season"] == SEASON]
    played = [m for m in matchups if m["season"] == SEASON
              and m["winner"] and m["winner"] != "UNDECIDED"]
    weeks_done = sorted({m["week"] for m in played})
    cur = (max(weeks_done) + 1) if weeks_done else 1
    started = bool(played)

    # all-play + streak from completed weeks
    weekly = defaultdict(list)
    for m in played:
        for side, opp in (("home", "away"), ("away", "home")):
            p = owner.get((SEASON, m[f"{side}TeamId"]))
            if p:
                weekly[m["week"]].append((p, m[f"{side}Score"]))
    ap = defaultdict(lambda: [0, 0])
    for wk, lst in weekly.items():
        for p, v in lst:
            for q, qv in lst:
                if p != q:
                    ap[p][0 if v > qv else 1] += 1
    seq = defaultdict(list)
    for m in sorted(played, key=lambda m: m["week"]):
        for side in ("home", "away"):
            p = owner.get((SEASON, m[f"{side}TeamId"]))
            if p:
                seq[p].append(m["winner"] == side.upper())

    def streak(v):
        if not v:
            return None
        last = v[-1]; n = 0
        for x in reversed(v):
            if x == last:
                n += 1
            else:
                break
        return f"{'W' if last else 'L'}{n}"

    # per-team roster-derived numbers for this season
    ros = defaultdict(list)
    for r in pw:
        if r["season"] == SEASON:
            ros[(r["week"], r["teamId"])].append(r)
    bench_pts = defaultdict(float); start_pts = defaultdict(float)
    for (wk, tid), ents in ros.items():
        for e in ents:
            if e["actual"] is None:
                continue
            if e["started"]:
                start_pts[tid] += e["actual"]
            elif e["slotId"] == 20:
                bench_pts[tid] += e["actual"]

    standings = []
    for t in rows:
        p = owner.get((SEASON, t["teamId"]))
        if not p:
            continue
        a = ap.get(p, [0, 0])
        standings.append({
            "p": p, "tm": t["name"], "tid": t["teamId"],
            "w": t["wins"] or 0, "l": t["losses"] or 0,
            "pf": round(t["pointsFor"] or 0, 2), "pa": round(t["pointsAgainst"] or 0, 2),
            "apw": a[0], "apl": a[1], "streak": streak(seq.get(p)),
            "bn": round(bench_pts.get(t["teamId"], 0), 1) or None,
            "seed": t["playoffSeed"], "rk": t["finalRank"],
        })

    # ---- schedule, with projections + win probability where available -----
    sched = []
    for m in sorted((m for m in matchups if m["season"] == SEASON),
                    key=lambda m: (m["week"], m["homeTeamId"])):
        hp = owner.get((SEASON, m["homeTeamId"])); apn = owner.get((SEASON, m["awayTeamId"]))
        if not hp or not apn:
            continue
        ph = proj_tot.get((SEASON, m["week"], m["homeTeamId"]))
        pa = proj_tot.get((SEASON, m["week"], m["awayTeamId"]))
        sched.append({
            "wk": m["week"], "hp": hp, "ap": apn,
            "htm": tname.get((SEASON, m["homeTeamId"])),
            "atm": tname.get((SEASON, m["awayTeamId"])),
            "hs": m["homeScore"] or None, "as": m["awayScore"] or None,
            "won": m["winner"] if m["winner"] != "UNDECIDED" else None,
            "po": m["isPlayoff"],
        })

    # ---- rosters (latest week we have) ------------------------------------
    latest = max((wk for (wk, _) in ros), default=None)
    rosters = []
    if latest is not None:
        for (wk, tid), ents in sorted(ros.items()):
            if wk != latest:
                continue
            rosters.append({
                "tid": tid, "p": owner.get((SEASON, tid)), "tm": tname.get((SEASON, tid)),
                "wk": wk,
                "players": sorted(({
                    "n": pname.get(e["playerId"]), "pos": ppos.get(e["playerId"]),
                    "slot": e["slot"], "st": e["started"],
                    "proj": round(e["projected"], 1) if e["projected"] is not None else None,
                    "act": e["actual"],
                } for e in ents), key=lambda x: (not x["st"], -(x["proj"] or 0))),
            })

    draft = [{"ov": p["overall"], "rd": p["round"],
              "p": owner.get((SEASON, p["teamId"])),
              "pl": pname.get(p["playerId"]), "pos": ppos.get(p["playerId"]),
              "k": 1 if p["keeper"] else 0}
             for p in picks if p["season"] == SEASON]

    # ---- career form, for preseason context --------------------------------
    prior = [t for t in teams if t["season"] < SEASON and t["owner"]]
    agg = defaultdict(lambda: dict(w=0, l=0, titles=0, seasons=0, last=None))
    for t in sorted(prior, key=lambda t: t["season"]):
        p = owner.get((t["season"], t["teamId"]))
        if not p:
            continue
        a = agg[p]
        a["w"] += t["wins"] or 0; a["l"] += t["losses"] or 0; a["seasons"] += 1
        if t["finalRank"] == 1:
            a["titles"] += 1
        a["last"] = {"s": t["season"], "rk": t["finalRank"],
                     "w": t["wins"], "l": t["losses"]}
    form = [{"p": p, "w": a["w"], "l": a["l"], "seasons": a["seasons"],
             "pct": round(a["w"] / (a["w"] + a["l"]), 3) if (a["w"] + a["l"]) else None,
             "titles": a["titles"], "last": a["last"]}
            for p, a in agg.items() if p in {s["p"] for s in standings}]
    form.sort(key=lambda r: -(r["pct"] or 0))

    done_weeks = sorted({m["week"] for m in matchups if m["season"] == SEASON
                         and m["winner"] and m["winner"] != "UNDECIDED"})
    awards = {}
    for wk in done_weeks:
        a = weekly_awards(SEASON, wk, pw, matchups, owner, tname, pname, ppos)
        if a:
            awards[f"{SEASON}|{wk}"] = a
    sample = None
    if not awards:
        # Season has not started. Rather than ship a dead page, show the most
        # recent week the league ever played, clearly labelled as such.
        reg = {s_["season"]: (s_.get("regularSeasonWeeks") or 99) for s_ in seasons}
        prev = [(m["season"], m["week"]) for m in matchups
                if m["winner"] and m["winner"] != "UNDECIDED"
                and m["week"] <= reg.get(m["season"], 99)]
        if prev:
            ps, pwk = max(prev)
            own_prev = {(t["season"], t["teamId"]): short.get(t["owner"]) for t in teams}
            a = weekly_awards(ps, pwk, pw, matchups, own_prev, tname, pname, ppos)
            if a:
                sample = {"season": ps, "week": pwk}
                awards[f"{ps}|{pwk}"] = a

    payload = {
        "meta": {
            "season": SEASON, "league": meta_s.get("name"),
            "currentWeek": cur, "weeksDone": weeks_done,
            "regularWeeks": meta_s.get("regularSeasonWeeks"),
            "playoffTeams": meta_s.get("playoffTeams"),
            "teams": len(rows), "started": started,
            "generated": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "recordBook": "https://jvkenny.github.io/fantasy-league/",
        },
        "calib": {"sd": round(sd, 2), "hit": round(hit / tot, 3) if tot else None,
                  "n": tot, "residN": len(resid)},
        "awards": awards, "sample": sample,
        "standings": standings, "schedule": sched, "rosters": rosters,
        "draft": draft, "form": form,
    }
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB) "
          f"season={SEASON} started={started} week={cur} "
          f"sched={len(sched)} rosters={len(rosters)} draft={len(draft)}")
    return payload


if __name__ == "__main__":
    d = main()
    m = d["meta"]
    print(f"  season {m['season']} started={m['started']} week={m['currentWeek']}")
    if d["sample"]:
        print(f"  no results yet - showing {d['sample']['season']} week "
              f"{d['sample']['week']} as the sample")
    for k, a in d["awards"].items():
        print(f"  awards for {k}: {len(a)}")
