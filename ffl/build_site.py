#!/usr/bin/env python3
"""Compute the deep metrics and emit one compact payload for the site.

The interesting numbers are not in ESPN's responses — they have to be derived:

  optimal lineup   the most points the roster could legally have scored that
                   week, given each player's eligibleSlots and the season's
                   starting-slot counts. Actual/optimal = lineup efficiency,
                   which separates "drafted badly" from "started the wrong guys".
  all-play record  a team's record had it played EVERY other team each week.
                   Immune to schedule luck, so actual-wins minus all-play-wins
                   is a clean luck measure.
  consistency      standard deviation of weekly scores.
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DER = REPO / "data" / "derived"
OUT = REPO / "site" / "data.json"

BENCH_SLOTS = {20, 21, 24}
SLOTNAME = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 23: "FLEX",
            3: "RB/WR", 5: "WR/TE", 7: "OP"}


def load(n):
    return json.loads((DER / f"{n}.json").read_text())


def optimal_lineup(entries, slot_counts):
    """Max points obtainable from this roster under the season's slot rules.

    Slots are filled from most restrictive to least (QB/K/D-ST/TE before RB/WR,
    FLEX last). Because every multi-position slot in this league is FLEX, and
    FLEX is filled from whatever the position slots leave behind, greedy in this
    order is exact rather than approximate.
    """
    pool = [e for e in entries if e.get("actual") is not None]
    starting = {int(s): n for s, n in slot_counts.items()
                if int(s) not in BENCH_SLOTS and n}
    # restrictiveness = how many players on this roster could fill the slot
    breadth = {s: sum(1 for e in pool if s in (e.get("eligible") or []))
               for s in starting}
    used, total, lineup = set(), 0.0, []
    for slot in sorted(starting, key=lambda s: (breadth.get(s, 0), s)):
        cands = sorted(
            (e for e in pool
             if id(e) not in used and slot in (e.get("eligible") or [])),
            key=lambda e: -e["actual"])
        for e in cands[:starting[slot]]:
            used.add(id(e)); total += e["actual"]
            lineup.append((slot, e["playerId"], e["actual"]))
    return round(total, 2), lineup



def compute_findings(D, careers, team_seasons, team_weeks, draft, player_seasons,
                     h2h, champs, detail):
    """Derive the written findings from the data, never by hand.

    Every claim below is computed, so a refresh rewrites them. The guard that
    matters: seasons without roster data (and the in-progress season) are
    excluded from anything roster-derived, or a missing-data zero reads as a
    historic bust.
    """
    F = []
    def add(cat, title, value, detail_):
        F.append({"cat": cat, "t": title, "v": value, "d": detail_})

    ts = [r for r in team_seasons if r["w"] is not None]
    played = [g for g in team_weeks]
    det = set(detail)
    # a season is "gradeable" for roster claims only if it has roster data AND is finished
    finished = {r["s"] for r in ts if r["rk"]}
    grade = det & finished

    # ---- bragging -------------------------------------------------------
    from collections import Counter
    cc = Counter(v for v in champs.values() if v)
    multi = [p for p, n in cc.items() if n > 1]
    yrs = sorted(champs)
    b2b = [y for a, y in zip(yrs, yrs[1:]) if champs.get(a) == champs.get(y)]
    add("Bragging rights", "Only repeat champion",
        multi[0] if multi else "—",
        f"{len(cc)} different managers have won the {sum(cc.values())} completed seasons. "
        f"{multi[0] if multi else 'Nobody'} is the only one with two, and "
        f"{'nobody has ever gone back-to-back' if not b2b else 'back-to-back happened'}.")

    best = max(ts, key=lambda r: (r["w"] or 0) - (r["l"] or 0))
    add("Bragging rights", "Best regular season",
        f"{best['w']}\u2013{best['l']}",
        f"{best['p']}, {best['s']}. {best['pf']:.0f} points for, an all-play record of "
        f"{best['apw']}\u2013{best['apl']}, and the title to go with it.")

    hi = max(played, key=lambda g: g["pts"])
    add("Bragging rights", "Highest week ever", f"{hi['pts']:.2f}",
        f"{hi['p']}, {hi['s']} week {hi['wk']}, against {hi['o']}, who managed "
        f"{hi['opp']:.2f}.")

    combo = max(played, key=lambda g: g["pts"] + g["opp"])
    add("Bragging rights", "Highest-scoring matchup",
        f"{combo['pts'] + combo['opp']:.2f}",
        f"{combo['s']} week {combo['wk']}: {combo['p']} {combo['pts']:.2f}, "
        f"{combo['o']} {combo['opp']:.2f}. Both totals would have beaten most weeks in "
        f"league history; one of them still lost.")

    # streaks
    from collections import defaultdict as dd
    seq = dd(list)
    for g in sorted(played, key=lambda g: (g["s"], g["wk"])):
        if not g["po"]:
            seq[g["p"]].append((g["s"], g["wk"], g["win"]))
    def run(rows, want):
        bestn = cur = 0; span = start = None
        for s_, w_, win in rows:
            if win == want:
                if cur == 0: start = (s_, w_)
                cur += 1
                if cur > bestn: bestn, span = cur, (start, (s_, w_))
            else:
                cur = 0
        return bestn, span
    ws = max(((run(v, 1), k) for k, v in seq.items()), key=lambda x: x[0][0])
    add("Bragging rights", "Longest winning streak", f"{ws[0][0]} straight",
        f"{ws[1]}, from {ws[0][1][0][0]} week {ws[0][1][0][1]} through "
        f"{ws[0][1][1][0]} week {ws[0][1][1][1]}.")

    dom = [(k.split('|'), v) for k, v in h2h.items()]
    dom = [(a, b, v) for (a, b), v in dom if v[0] + v[1] >= 6 and v[0] > v[1]]
    if dom:
        d0 = max(dom, key=lambda r: (r[2][0] / (r[2][0] + r[2][1]), r[2][0]))
        add("Bragging rights", "Most one-sided rivalry",
            f"{d0[2][0]}\u2013{d0[2][1]}",
            f"{d0[0]} owns {d0[1]}, outscoring them {d0[2][2]:.1f} to {d0[2][3]:.1f} "
            f"per meeting across {d0[2][0] + d0[2][1]} games.")

    perfect = [g for g in played if g["opt"] and abs(g["pts"] - g["opt"]) < 0.01]
    if perfect:
        add("Bragging rights", "Perfect lineups",
            f"{len(perfect)}",
            "Weeks where a manager started the single best lineup their roster allowed: "
            + ", ".join(f"{g['p']} ({g['s']} wk{g['wk']})" for g in perfect[:4])
            + ("." if len(perfect) <= 4 else f", and {len(perfect) - 4} more."))

    # ---- facepalm --------------------------------------------------------
    lost_with = [g for g in played if not g["win"] and g["bn"]
                 and g["bn"] > (g["opp"] - g["pts"])]
    if lost_with:
        w0 = max(lost_with, key=lambda g: g["bn"] - (g["opp"] - g["pts"]))
        add("Facepalm", "The win was on the bench", f"{w0['bn']:.1f} pts benched",
            f"{w0['p']}, {w0['s']} week {w0['wk']}, lost to {w0['o']} by "
            f"{w0['opp'] - w0['pts']:.2f} while leaving {w0['bn']:.1f} points in bench slots "
            f"\u2014 {w0['bn'] - (w0['opp'] - w0['pts']):.1f} more than they needed.")
        # a drawn game is not a loss; without this guard the tie below surfaces
        # here as "lost by 0.00"
        real = [g for g in lost_with if g["opp"] - g["pts"] > 0.005]
        if real:
            tight = min(real, key=lambda g: g["opp"] - g["pts"])
            add("Facepalm", "Cruellest margin",
                f"lost by {tight['opp'] - tight['pts']:.2f}",
                f"{tight['p']}, {tight['s']} week {tight['wk']}, left {tight['bn']:.1f} points "
                f"on the bench and lost to {tight['o']} by "
                f"{tight['opp'] - tight['pts']:.2f}.")

    effw = [g for g in played if g["opt"] and g["opt"] > 40]
    if effw:
        worst = min(effw, key=lambda g: g["pts"] / g["opt"])
        add("Facepalm", "Worst lineup call", f"{worst['pts'] / worst['opt'] * 100:.1f}%",
            f"{worst['p']}, {worst['s']} week {worst['wk']}: started {worst['pts']:.1f} of a "
            f"possible {worst['opt']:.1f}. Less than half the points that were on the roster.")
        w5 = sorted(effw, key=lambda g: g["pts"] / g["opt"])[:5]
        who = Counter(g["p"] for g in w5).most_common(1)[0]
        if who[1] >= 2:
            add("Facepalm", "Rookie year", f"{who[1]} of the 5 worst",
                f"{who[0]} owns {who[1]} of the five worst lineup weeks ever recorded "
                f"({', '.join(str(g['s']) + ' wk' + str(g['wk']) for g in w5 if g['p'] == who[0])}).")

    gone = [d for d in draft if d["s"] in grade and d["lg"] - d["pts"] > 100]
    if gone:
        g0 = max(gone, key=lambda d: d["lg"] - d["pts"])
        add("Facepalm", "The one that got away", f"{g0['lg'] - g0['pts']:.1f} pts lost",
            f"{g0['p']} drafted {g0['pl']} in round {g0['rd']}, {g0['s']}, started him for "
            f"{g0['pts']:.1f}, then watched him produce {g0['lg'] - g0['pts']:.1f} more for "
            f"somebody else.")
        serial = Counter(d["p"] for d in sorted(gone, key=lambda d: -(d["lg"] - d["pts"]))[:8])
        top = serial.most_common(1)[0]
        if top[1] >= 3:
            add("Facepalm", "Serial offender", f"{top[1]} of the top 8",
                f"{top[0]} accounts for {top[1]} of the eight biggest give-aways in league "
                f"history. Drafts them, then hands them over.")

    rost = dd(set)
    for r in player_seasons:
        rost[(r["s"], r["pl"])].add(r["p"])
    hot = max(rost.items(), key=lambda kv: len(kv[1]))
    if len(hot[1]) >= 4:
        add("Oddity", "League hot potato", f"{len(hot[1])} rosters",
            f"{hot[0][1]} was rostered by {len(hot[1])} different managers during {hot[0][0]} "
            f"alone. Nobody wanted to be holding him.")

    # ---- robbed ----------------------------------------------------------
    lk = [r for r in ts if r["luck"] is not None]
    unl = min(lk, key=lambda r: r["luck"])
    add("Robbed", "Unluckiest season on record", f"{unl['luck']:+.1f} wins",
        f"{unl['p']}, {unl['s']}: {unl['w']}\u2013{unl['l']} despite an all-play record of "
        f"{unl['apw']}\u2013{unl['apl']}. The scores were worth about {unl['expW']:.1f} wins. "
        f"They got {unl['w']}.")

    luk = max(lk, key=lambda r: r["luck"])
    add("Robbed", "Luckiest season on record", f"{luk['luck']:+.1f} wins",
        f"{luk['p']}, {luk['s']}: {luk['w']}\u2013{luk['l']} on an all-play record of "
        f"{luk['apw']}\u2013{luk['apl']}, worth about {luk['expW']:.1f} wins.")

    beat = max((g for g in played if not g["win"]), key=lambda g: g["pts"])
    add("Robbed", "Highest score that still lost", f"{beat['pts']:.2f}",
        f"{beat['p']}, {beat['s']} week {beat['wk']}. {beat['o']} answered with "
        f"{beat['opp']:.2f}. That total would have won any other matchup that week.")

    nochip = [r for r in ts if r["rk"] != 1 and r["apw"] + r["apl"] > 0]
    if nochip:
        rb = max(nochip, key=lambda r: r["apw"] / (r["apw"] + r["apl"]))
        add("Robbed", "Best season without a title",
            f"{rb['apw'] / (rb['apw'] + rb['apl']):.3f} all-play",
            f"{rb['p']}, {rb['s']}: beat {rb['apw']} of {rb['apw'] + rb['apl']} opponent-weeks "
            f"and finished {rb['rk']}.")

    missed = [r for r in ts if r["rk"] and r["rk"] > 5]
    if missed:
        m0 = max(missed, key=lambda r: r["pf"] or 0)
        add("Robbed", "Most points, no playoffs", f"{m0['pf']:.0f} pts",
            f"{m0['p']}, {m0['s']}, went {m0['w']}\u2013{m0['l']} and finished {m0['rk']}.")

    careerluck = sorted(careers, key=lambda c: c["luck"])
    add("Robbed", "Career luck, worst to best",
        f"{careerluck[0]['luck']:+.1f} to {careerluck[-1]['luck']:+.1f}",
        f"{careerluck[0]['p']} is down {abs(careerluck[0]['luck']):.1f} wins across their career; "
        f"{careerluck[-1]['p']} is up {careerluck[-1]['luck']:.1f}. That is a swing of "
        f"{careerluck[-1]['luck'] - careerluck[0]['luck']:.1f} wins that nobody earned or deserved.")

    # ---- oddity -----------------------------------------------------------
    close = min((g for g in played if g["win"] and g["pts"] - g["opp"] > 0),
                key=lambda g: g["pts"] - g["opp"])
    add("Oddity", "Closest game ever", f"{close['pts'] - close['opp']:.2f} pts",
        f"{close['p']} {close['pts']:.2f}, {close['o']} {close['opp']:.2f}, "
        f"{close['s']} week {close['wk']}.")

    drawn = [g for g in played if abs(g["pts"] - g["opp"]) < 0.005]
    if drawn:
        d0 = drawn[0]
        add("Oddity", "The only tie", f"{d0['pts']:.2f} apiece",
            f"{d0['p']} and {d0['o']} finished {d0['s']} week {d0['wk']} on exactly the same "
            f"score. In {len(played):,} team-weeks of league history it has happened once.")

    ls = max(((run(v, 0), k) for k, v in seq.items()), key=lambda x: x[0][0])
    add("Oddity", "Longest losing streak", f"{ls[0][0]} straight",
        f"{ls[1]}, {ls[0][1][0][0]} week {ls[0][1][0][1]} to {ls[0][1][1][0]} week "
        f"{ls[0][1][1][1]}.")

    champ_rows = [r for r in ts if r["rk"] == 1]
    weakest = min(champ_rows, key=lambda r: (r["w"] or 0) / max(1, (r["w"] or 0) + (r["l"] or 0)))
    add("Oddity", "Weakest champion",
        f"{weakest['w']}\u2013{weakest['l']}",
        f"{weakest['p']} won {weakest['s']} from the {weakest['seed']} seed with a "
        f"{weakest['luck']:+.1f}-win luck rating. Regular seasons are advisory.")

    return F

def main():
    seasons = load("seasons"); teams = load("teams"); matchups = load("matchups")
    pw = load("player_weeks"); picks = load("draft_picks")
    txns = load("transactions"); players = load("players")

    # Published as first names only: the page is world-readable and there is no
    # reason for nineteen people's full legal names to be search-indexable
    # alongside their worst weeks. Applied here so full names never reach the
    # payload at all, rather than being hidden in the front end.
    full = sorted({t["owner"] for t in teams if t["owner"]})
    short = {n: n.split()[0] for n in full}
    clash = {v for v in short.values() if list(short.values()).count(v) > 1}
    if clash:  # fall back to "First L." only for the names that actually collide
        for n in full:
            if short[n] in clash:
                parts = n.split()
                short[n] = f"{parts[0]} {parts[-1][0]}." if len(parts) > 1 else n
    still = {v for v in short.values() if list(short.values()).count(v) > 1}
    if still:
        raise SystemExit(f"ambiguous display names: {still} — fix data/identities.json")

    owner = {(t["season"], t["teamId"]): short.get(t["owner"]) for t in teams}
    tname = {(t["season"], t["teamId"]): (t["name"] or t["abbrev"]) for t in teams}
    pname = {p["playerId"]: p["name"] for p in players}
    ppos = {p["playerId"]: p["position"] for p in players}
    slotc = {s["season"]: (s.get("slotCounts") or {}) for s in seasons}
    detail = {s["season"] for s in seasons if s["hasPlayerDetail"]}

    # ---- roster -> optimal lineup, bench points, per team-week ----------
    roster = defaultdict(list)
    for r in pw:
        roster[(r["season"], r["week"], r["teamId"])].append(r)

    optimal, benched, startpts, beat = {}, defaultdict(float), {}, defaultdict(lambda: [0, 0])
    for key, ents in roster.items():
        yr = key[0]
        opt, _ = optimal_lineup(ents, slotc.get(yr, {}))
        optimal[key] = opt
        s = sum(e["actual"] for e in ents if e["started"] and e["actual"] is not None)
        startpts[key] = round(s, 2)
        for e in ents:
            if e["actual"] is None:
                continue
            if not e["started"] and e["slotId"] == 20:
                benched[key] += e["actual"]
            if e["started"] and e["projected"] is not None:
                o = owner.get((yr, key[2]))
                beat[o][1] += 1
                if e["actual"] > e["projected"]:
                    beat[o][0] += 1

    # ---- team-weeks (drives every chart) --------------------------------
    team_weeks = []
    weekly = defaultdict(list)   # (season, week) -> [(person, score)]
    for m in matchups:
        if not m["winner"] or m["winner"] == "UNDECIDED":
            continue
        yr, wk = m["season"], m["week"]
        for side, opp in (("home", "away"), ("away", "home")):
            tid = m[f"{side}TeamId"]
            p = owner.get((yr, tid))
            if not p:
                continue
            k = (yr, wk, tid)
            pts = m[f"{side}Score"]
            team_weeks.append({
                "s": yr, "wk": wk, "p": p, "o": owner.get((yr, m[f"{opp}TeamId"])),
                "pts": pts, "opp": m[f"{opp}Score"],
                "win": 1 if m["winner"] == side.upper() else 0,
                "po": 1 if m["isPlayoff"] else 0,
                "opt": optimal.get(k), "bn": round(benched.get(k, 0), 1) or None,
            })
            weekly[(yr, wk)].append((p, pts))

    # ---- all-play (schedule-independent) --------------------------------
    allplay = defaultdict(lambda: [0, 0])
    for (yr, wk), rows in weekly.items():
        for p, pts in rows:
            for q, qpts in rows:
                if p == q:
                    continue
                allplay[(yr, p)][0 if pts > qpts else 1] += 1

    # ---- team-season rows ------------------------------------------------
    team_seasons = []
    for t in teams:
        # via the owner map, not t["owner"] — the map carries the display
        # name, and mixing the two silently breaks every join below
        yr, tid = t["season"], t["teamId"]
        p = owner.get((yr, tid))
        if not p:
            continue
        mine = [g for g in team_weeks if g["s"] == yr and g["p"] == p and not g["po"]]
        scores = [g["pts"] for g in mine]
        opts = [g["opt"] for g in mine if g["opt"]]
        ap = allplay.get((yr, p), [0, 0])
        apg = ap[0] + ap[1]
        exp_w = round(ap[0] / apg * len(mine), 1) if apg and mine else None
        team_seasons.append({
            "p": p, "s": yr, "tm": t["name"], "w": t["wins"], "l": t["losses"],
            "pf": t["pointsFor"], "pa": t["pointsAgainst"],
            "rk": t["finalRank"], "seed": t["playoffSeed"],
            "apw": ap[0], "apl": ap[1],
            "expW": exp_w,
            "luck": round((t["wins"] or 0) - exp_w, 1) if exp_w is not None else None,
            "opt": round(sum(opts), 1) if opts else None,
            "eff": round(sum(scores) / sum(opts) * 100, 1) if opts and sum(opts) else None,
            "bn": round(sum(g["bn"] or 0 for g in mine), 1) or None,
            "hi": max(scores) if scores else None,
            "lo": min(scores) if scores else None,
            "sd": round(statistics.pstdev(scores), 1) if len(scores) > 1 else None,
        })

    # ---- career rollup ---------------------------------------------------
    career = {}
    for r in team_seasons:
        c = career.setdefault(r["p"], dict(p=r["p"], seasons=0, w=0, l=0, pf=0.0, pa=0.0,
                                           apw=0, apl=0, titles=0, finals=0, po=0,
                                           luck=0.0, optsum=0.0, ptssum=0.0, best=None))
        c["seasons"] += 1; c["w"] += r["w"] or 0; c["l"] += r["l"] or 0
        c["pf"] += r["pf"] or 0; c["pa"] += r["pa"] or 0
        c["apw"] += r["apw"]; c["apl"] += r["apl"]
        if r["luck"] is not None: c["luck"] += r["luck"]
        if r["opt"]: c["optsum"] += r["opt"]; c["ptssum"] += (r["pf"] or 0)
        if r["seed"]: c["po"] += 1
        if r["rk"] == 1: c["titles"] += 1
        if r["rk"] in (1, 2): c["finals"] += 1
        if r["rk"] and (c["best"] is None or r["rk"] < c["best"]): c["best"] = r["rk"]
    careers = []
    for c in career.values():
        g = c["w"] + c["l"]
        apg = c["apw"] + c["apl"]
        careers.append({
            "p": c["p"], "seasons": c["seasons"], "w": c["w"], "l": c["l"],
            "pct": round(c["w"] / g, 3) if g else 0,
            "apPct": round(c["apw"] / apg, 3) if apg else None,
            "ppg": round(c["pf"] / g, 1) if g else 0,
            "papg": round(c["pa"] / g, 1) if g else 0,
            "luck": round(c["luck"], 1),
            "eff": round(c["ptssum"] / c["optsum"] * 100, 1) if c["optsum"] else None,
            "beatProj": round(beat[c["p"]][0] / beat[c["p"]][1], 3) if beat[c["p"]][1] else None,
            "titles": c["titles"], "finals": c["finals"], "po": c["po"], "best": c["best"],
        })
    careers.sort(key=lambda r: -r["pct"])

    # ---- head to head ----------------------------------------------------
    h2h = defaultdict(lambda: [0, 0, 0.0, 0.0])
    for g in team_weeks:
        if not g["o"] or g["o"] == g["p"]:
            continue
        e = h2h[f'{g["p"]}|{g["o"]}']
        e[0 if g["win"] else 1] += 1
        e[2] += g["pts"]; e[3] += g["opp"]
    h2h = {k: [v[0], v[1], round(v[2] / max(1, v[0] + v[1]), 1),
               round(v[3] / max(1, v[0] + v[1]), 1)] for k, v in h2h.items()}

    # ---- player seasons ---------------------------------------------------
    agg = defaultdict(lambda: dict(st=0.0, bn=0.0, gs=0, best=0.0))
    for r in pw:
        p = owner.get((r["season"], r["teamId"]))
        if not p or r["actual"] is None:
            continue
        a = agg[(r["season"], p, r["playerId"])]
        if r["started"]:
            a["st"] += r["actual"]; a["gs"] += 1; a["best"] = max(a["best"], r["actual"])
        elif r["slotId"] == 20:
            a["bn"] += r["actual"]
    player_seasons = [{
        "s": s, "p": p, "pl": pname.get(pid), "pos": ppos.get(pid),
        "st": round(v["st"], 1), "bn": round(v["bn"], 1), "gs": v["gs"],
        "best": round(v["best"], 1),
    } for (s, p, pid), v in agg.items() if v["st"] or v["bn"]]
    player_seasons.sort(key=lambda r: -r["st"])

    # ---- draft ------------------------------------------------------------
    # Two different questions, so two columns. "What did this pick return to
    # the manager who spent it" is NOT the same as "how good was this player
    # that year" — a player dropped in week 2 scores plenty for someone else.
    for_drafter = {(s, p, pid): v["st"] for (s, p, pid), v in agg.items()}
    league_total = defaultdict(float)
    for (s, p, pid), v in agg.items():
        league_total[(s, pid)] += v["st"]
    draft = []
    for p_ in picks:
        yr, pid = p_["season"], p_["playerId"]
        who = owner.get((yr, p_["teamId"]))
        draft.append({
            "s": yr, "ov": p_["overall"], "rd": p_["round"], "p": who,
            "pl": pname.get(pid), "pos": ppos.get(pid),
            "pts": round(for_drafter.get((yr, who, pid), 0), 1),
            "lg": round(league_total.get((yr, pid), 0), 1),
            "k": 1 if p_["keeper"] else 0,
        })

    # ---- transactions ------------------------------------------------------
    tx = defaultdict(lambda: defaultdict(int))
    for t in txns:
        p = owner.get((t["season"], t["teamId"]))
        if not p:
            continue
        a = t.get("action")
        if a in ("ADD", "DROP"):
            tx[(t["season"], p)][a.lower()] += 1
        if t.get("type") == "TRADE_ACCEPT":
            tx[(t["season"], p)]["trade"] += 1
    txrows = [{"s": s, "p": p, "add": v["add"], "drop": v["drop"], "trade": v["trade"]}
              for (s, p), v in tx.items()]

    payload = {
        "meta": {
            "id": 582222, "name": seasons[-1]["name"],
            "seasons": [s["season"] for s in seasons],
            "detail": sorted(detail),
            "champs": {s["season"]: owner.get((s["season"], s["championTeamId"]))
                       for s in seasons if s["championTeamId"]},
            "teamNames": {f'{k[0]}|{k[1]}': v for k, v in tname.items()},
            "me": short.get("John Kenny"),
        },
        "people": sorted({r["p"] for r in team_seasons}),
        "n": {
            "managers": len(careers),
            "managersWithDetail": sum(1 for c in careers if c["eff"] is not None),
            "teamSeasons": len(team_seasons),
            "teamWeeks": len(team_weeks),
            "playerSeasons": len(player_seasons),
            "picks": len(picks),
            "detailSeasons": len(detail),
            "allSeasons": len(seasons),
            "gapSeasons": sorted({s["season"] for s in seasons} - detail),
            "slots": {SLOTNAME.get(int(k), k): v
                      for k, v in sorted((slotc.get(seasons[-1]["season"]) or {}).items(),
                                         key=lambda kv: int(kv[0]))
                      if int(k) not in BENCH_SLOTS},
        },
        "careers": careers,
        "teamSeasons": team_seasons,
        "teamWeeks": team_weeks,
        "h2h": h2h,
        "playerSeasons": player_seasons,
        "draft": draft,
        "tx": txrows,
        "findings": compute_findings(None, careers, team_seasons, team_weeks, draft,
                                     player_seasons, h2h,
                                     {s_["season"]: owner.get((s_["season"], s_["championTeamId"]))
                                      for s_ in seasons if s_["championTeamId"]},
                                     detail),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {OUT}  ({OUT.stat().st_size/1024:.0f} KB)")
    return payload


if __name__ == "__main__":
    d = main()
    print(f"\n rows: careers={len(d['careers'])} teamSeasons={len(d['teamSeasons'])} "
          f"teamWeeks={len(d['teamWeeks'])} playerSeasons={len(d['playerSeasons'])} "
          f"draft={len(d['draft'])} tx={len(d['tx'])}")
    print("\n=== LINEUP EFFICIENCY / LUCK (career) ===")
    for r in sorted([c for c in d["careers"] if c["eff"]], key=lambda r: -r["eff"]):
        print(f"  {r['p']:<22} eff {r['eff']:>5.1f}%   luck {r['luck']:>+5.1f} W   "
              f"actual {r['pct']:.3f} vs all-play {r['apPct']}")
