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

from .build_site import optimal_lineup

REPO = Path(__file__).resolve().parent.parent
DER = REPO / "data" / "derived"
OUT = REPO / "site" / "season.json"

BENCH = {20, 21, 24}


def load(n):
    return json.loads((DER / f"{n}.json").read_text())


def norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))



BENCH_SLOTS = {20, 21, 24}
SLOT_NAMES = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 23: "FLEX", 16: "D/ST", 17: "K",
              3: "RB/WR", 5: "WR/TE", 7: "OP"}
SLOT_ORDER = {0: 0, 2: 1, 4: 2, 6: 3, 23: 4, 3: 4, 5: 4, 7: 4, 16: 5, 17: 6}
PR_K = 4.0          # weeks at which results and outlook would weigh equally
PR_FLOOR = 0.60     # results never count for less than this
PR_HALFLIFE = 4.0   # recency half-life, in weeks


def _z(vals):
    """Z-scores. Zero spread means nobody is distinguishable - return zeros
    rather than dividing by it."""
    n = len(vals)
    mu = sum(vals) / n
    sd = (sum((v - mu) ** 2 for v in vals) / n) ** 0.5
    return [0.0] * n if sd < 1e-9 else [(v - mu) / sd for v in vals]


def power_rankings(season, weeks, pw, outlook, slot_counts, owner, tname):
    """Rank teams by what their roster can do, how much of it they capture, and
    what it projects to do from here.

    Points scored is deliberately NOT a separate term: scored = strength x
    efficiency, so adding it would count the same thing twice. Likewise the
    penalty for benching points is measured against the best LEGAL lineup, not
    against total bench points - you cannot start everyone, and depth is not a
    mistake.

    Returns one row per completed week, so the movement column is recomputed
    from data rather than depending on stored state.
    """
    start_slots = {k: v for k, v in slot_counts.items() if k not in BENCH_SLOTS}
    if not start_slots:
        return []
    teams = sorted({r["teamId"] for r in pw if r["season"] == season})
    if len(teams) < 2:
        return []

    # Week 0 is the season-start ranking: the roster the manager drafted, with no
    # results component because nothing has been played. It also gives week 1
    # something to move against.
    has_zero = any(r["season"] == season and r["week"] == 0 for r in outlook)
    stops = ([0] if has_zero else []) + list(weeks)

    out = []
    for upto in stops:
        wks = [w for w in weeks if 1 <= w <= upto]
        # recency: a week 10 roster should not be judged on week 1
        decay = {w: 0.5 ** ((upto - w) / PR_HALFLIFE) for w in wks}

        strength, efficiency, scored = {}, {}, {}
        for t in (teams if wks else []):
            num = den = sc = 0.0
            for w in wks:
                ents = [r for r in pw if r["season"] == season and r["week"] == w
                        and r["teamId"] == t and r["actual"] is not None]
                if not ents:
                    continue
                opt, _ = optimal_lineup(ents, start_slots)
                act = sum(r["actual"] for r in ents if r["started"])
                if opt <= 0:
                    continue
                d = decay[w]
                num += act * d
                den += opt * d
                sc += opt * d
            if den <= 0:
                continue
            strength[t] = sc / sum(decay[w] for w in wks)
            efficiency[t] = num / den
            scored[t] = num / sum(decay[w] for w in wks)

        snap = [r for r in outlook if r["season"] == season and r["week"] == upto]
        look = {}
        for t in teams:
            ents = [{"eligible": r["eligible"], "actual": r["ros"],
                     "playerId": r["playerId"], "slotId": r["slotId"]}
                    for r in snap if r["teamId"] == t and r["slotId"] != 21]
            if ents:
                best, _ = optimal_lineup(ents, start_slots)
                look[t] = best

        have = ([t for t in teams if t in strength] if wks
                else [t for t in teams if t in look])
        if not have:
            continue
        zs = (dict(zip(have, _z([strength[t] for t in have]))) if wks
              else {t: 0.0 for t in have})
        ze = (dict(zip(have, _z([efficiency[t] for t in have]))) if wks
              else {t: 0.0 for t in have})
        zf = (dict(zip(have, _z([look[t] for t in have])))
              if all(t in look for t in have) else {t: 0.0 for t in have})

        n = len(wks)
        wp = 0.0 if n == 0 else max(n / (n + PR_K), PR_FLOOR)
        wo = 1.0 - wp
        rows = []
        for t in have:
            val = wp * (0.60 * zs[t] + 0.40 * ze[t]) + wo * zf[t]
            rows.append({
                "week": upto, "tid": t, "p": owner.get((season, t)),
                "tm": tname.get((season, t)),
                "rating": round(50 + val * 15, 1),
                "zStrength": round(zs[t], 2), "zEff": round(ze[t], 2),
                "zLook": round(zf[t], 2),
                "strength": round(strength[t], 1) if t in strength else None,
                "eff": round(efficiency[t] * 100, 1) if t in efficiency else None,
                "look": round(look[t], 1) if t in look else None,
                "scored": round(scored[t], 1) if t in scored else None,
                "wResults": round(wp, 3),
            })
        rows.sort(key=lambda r: -r["rating"])
        for i, r in enumerate(rows, 1):
            r["rank"] = i
        out.append(rows)

    # movement against the previous week's ranking
    for i, rows in enumerate(out):
        prev = {r["tid"]: r["rank"] for r in out[i - 1]} if i else {}
        for r in rows:
            r["prev"] = prev.get(r["tid"])
            r["move"] = (prev[r["tid"]] - r["rank"]) if r["tid"] in prev else None
    return out



def decisions(ents, start_slots):
    """What one team-week's lineup decisions cost, measured against LEGAL swaps only.

    Bench points on their own are not a mistake - you cannot start everyone.
    A mistake is a benched player who could have filled a starter's slot and
    outscored him. Three lineups are compared on ACTUAL points:

      started  what the manager ran out
      best     the best lineup hindsight allows (optimal_lineup on actuals)
      chalk    the lineup ESPN's projections said to start, scored on actuals

    regret = best - started            what hindsight says was left on the table
    chalk  = chalk - started           what simply following the projection would
                                       have gained (+) or cost (-)

    The chalk gap is the inexcusable part: you had the information and went the
    other way. Also names the single worst swap so the Facepalm can say who.
    """
    pool = [e for e in ents if e.get("actual") is not None]
    if not pool:
        return None
    started = sum(e["actual"] for e in pool if e["started"])
    best, _ = optimal_lineup(pool, start_slots)
    chalk = None
    if all(e.get("projected") is not None for e in pool):
        by_proj = [dict(e, actual=e["projected"], _act=e["actual"]) for e in pool]
        _, lineup = optimal_lineup(by_proj, start_slots)
        picked = {pid for _, pid, _ in lineup}
        chalk = sum(e["actual"] for e in pool if e["playerId"] in picked)
    # worst single legal swap: benched b for starter s, same slot eligibility
    swap = None
    for s_ in (e for e in pool if e["started"]):
        for b in (e for e in pool if not e["started"] and e["slotId"] == 20):
            if s_["slotId"] in (b.get("eligible") or []) and b["actual"] > s_["actual"]:
                d = b["actual"] - s_["actual"]
                if swap is None or d > swap["d"]:
                    proj_said = (b.get("projected") is not None and s_.get("projected") is not None
                                 and b["projected"] > s_["projected"])
                    swap = {"d": round(d, 1), "slot": s_["slot"],
                            "in": b["playerId"], "inPts": b["actual"],
                            "out": s_["playerId"], "outPts": s_["actual"],
                            "chalk": proj_said}
    return {"started": round(started, 1), "best": best,
            "regret": round(max(0.0, best - started), 1),
            "chalk": round(chalk - started, 1) if chalk is not None else None,
            "swap": swap}


def rows_for_kits(teams, season):
    return [t for t in teams if t["season"] == season]


def head_to_head(matchups, owner):
    """All-time record for every pair of managers, playoffs included.

    Keyed "A|B" with A < B alphabetically; w/l are from A's side. Carries the
    last meeting and who holds the current run, for the rivalry line on each
    scoreboard tile.
    """
    out = {}
    for m in sorted(matchups, key=lambda m: (m["season"], m["week"])):
        if not m["winner"] or m["winner"] in ("UNDECIDED", "TIE"):
            continue
        h = owner.get((m["season"], m["homeTeamId"]))
        a = owner.get((m["season"], m["awayTeamId"]))
        if not h or not a or h == a:
            continue
        hw = m["winner"] == "HOME"
        A, B = sorted([h, a])
        r = out.setdefault(f"{A}|{B}", {"w": 0, "l": 0, "po": 0, "run": None, "runN": 0, "last": None})
        winner = h if hw else a
        if winner == A:
            r["w"] += 1
        else:
            r["l"] += 1
        if m["isPlayoff"]:
            r["po"] += 1
        r["runN"] = r["runN"] + 1 if r["run"] == winner else 1
        r["run"] = winner
        ws = m["homeScore"] if hw else m["awayScore"]
        ls = m["awayScore"] if hw else m["homeScore"]
        r["last"] = {"s": m["season"], "wk": m["week"], "winner": winner,
                     "ws": round(ws, 1), "ls": round(ls, 1), "po": bool(m["isPlayoff"])}
    return out


def weekly_awards(season, week, pw, matchups, owner, tname, pname, ppos,
                  ppro=None, start_slots=None):
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
    ppro = ppro or {}
    def add(key, label, value, detail, pid=None, mgr=None):
        a = {"k": key, "l": label, "v": value, "d": detail}
        if pid is not None:
            a["pid"] = pid; a["pro"] = ppro.get(pid); a["pn"] = pname.get(pid)
            a["pos"] = ppos.get(pid)
        if mgr:
            a["mgr"] = mgr
        A.append(a)

    started = [r for r in rows if r["started"] and r["actual"] is not None]
    benched = [r for r in rows if not r["started"] and r["slotId"] == 20
               and r["actual"] is not None]

    if started:
        m0 = max(started, key=lambda r: r["actual"])
        add("mvp", "MVP", f"{m0['actual']:.1f}",
            f"{pname.get(m0['playerId'])} ({ppos.get(m0['playerId'])}) was the "
            f"highest-scoring started player of the week, for {who(m0['teamId'])}.",
            pid=m0["playerId"], mgr=who(m0["teamId"]))

    # facepalm: a lineup DECISION that cost a game, not a big bench. Ranked:
    # lost and the chalk lineup wins (ignored the projection, paid for it) >
    # lost and only hindsight wins > largest legal regret anywhere.
    if start_slots:
        cand = []
        for t in score:
            ents = [r for r in rows if r["teamId"] == t]
            d = decisions(ents, start_slots)
            if not d or not d["swap"]:
                continue
            margin = score[opp[t]] - score[t]
            lost = not won.get(t)
            chalk_wins = lost and d["chalk"] is not None and d["chalk"] > margin
            best_wins = lost and d["regret"] > margin
            cand.append(((2 if chalk_wins else 0) + (1 if best_wins else 0), d["regret"], t, d, margin))
        if cand:
            cand.sort(key=lambda c: (-c[0], -c[1]))
            tier, regret, t, d, margin = cand[0]
            sw = d["swap"]
            swap_txt = (f"{pname.get(sw['in'])} sat with {sw['inPts']:.1f} while "
                        f"{pname.get(sw['out'])} started at {sw['slot']} for {sw['outPts']:.1f}")
            if tier >= 2:
                add("facepalm", "Facepalm of the week", f"{regret:.1f} left",
                    f"{who(t)} lost to {who(opp[t])} by {margin:.2f}. {swap_txt}"
                    f"{' - and ESPN had him projected higher' if sw['chalk'] else ''}. "
                    f"Starting the projected lineup alone would have won it.",
                    pid=sw["in"], mgr=who(t))
            elif tier == 1:
                add("facepalm", "Facepalm of the week", f"{regret:.1f} left",
                    f"{who(t)} lost to {who(opp[t])} by {margin:.2f} with {regret:.1f} points "
                    f"available through legal swaps. {swap_txt}"
                    f"{' - ESPN had him higher, too' if sw['chalk'] else ', though nobody saw it coming'}.",
                    pid=sw["in"], mgr=who(t))
            else:
                add("facepalm", "Facepalm of the week", f"{regret:.1f} left",
                    f"{who(t)} left {regret:.1f} points in legal swaps: {swap_txt}"
                    + (" - and got away with it." if won.get(t)
                       else f" - though it would not have covered a {margin:.2f} loss."),
                    pid=sw["in"], mgr=who(t))

    winners = [t for t in score if won.get(t)]
    losers = [t for t in score if not won.get(t)]
    if winners:
        lucky = min(winners, key=lambda t: beat[t])
        add("lucky", "The Lucky One", f"{score[lucky]:.1f}",
            f"{who(lucky)} won while outscoring only {beat[lucky]} of the other {n_others} "
            # "Drew X" read as a first name on a page that is otherwise all
            # first names. Say what the sentence actually means instead.
            f"teams. The schedule handed them {who(opp[lucky])}, "
            f"who managed {score[opp[lucky]]:.1f}.", mgr=who(lucky))
    if losers:
        robbed = max(losers, key=lambda t: beat[t])
        add("robbed", "Robbed", f"{score[robbed]:.1f}",
            f"{who(robbed)} lost despite a score that would have beaten "
            f"{beat[robbed]} of the other {n_others} teams. {who(opp[robbed])} happened to "
            f"put up {score[opp[robbed]]:.1f}.", mgr=who(robbed))

    proj = [r for r in started if r["projected"] is not None]
    if proj:
        b = min(proj, key=lambda r: r["actual"] - r["projected"])
        add("bust", "Bust of the week", f"{b['actual'] - b['projected']:+.1f}",
            f"{pname.get(b['playerId'])} scored {b['actual']:.1f} against a "
            f"{b['projected']:.1f} projection, in {who(b['teamId'])}'s starting lineup.",
            pid=b["playerId"], mgr=who(b["teamId"]))
        g = max(proj, key=lambda r: r["actual"] - r["projected"])
        add("sleeper", "Overachiever", f"{g['actual'] - g['projected']:+.1f}",
            f"{pname.get(g['playerId'])} put up {g['actual']:.1f} on a "
            f"{g['projected']:.1f} projection for {who(g['teamId'])}.",
            pid=g["playerId"], mgr=who(g["teamId"]))

    if benched:
        bh = max(benched, key=lambda r: r["actual"])
        add("benchhero", "Best player nobody started", f"{bh['actual']:.1f}",
            f"{pname.get(bh['playerId'])} ({ppos.get(bh['playerId'])}) scored "
            f"{bh['actual']:.1f} on {who(bh['teamId'])}'s bench.",
            pid=bh["playerId"], mgr=who(bh["teamId"]))

    hi = max(score, key=score.get); lo = min(score, key=score.get)
    add("high", "Highest score", f"{score[hi]:.1f}", f"{who(hi)}.", mgr=who(hi))
    add("low", "Lowest score", f"{score[lo]:.1f}", f"{who(lo)}.", mgr=who(lo))

    blow = max(games, key=lambda m: abs(m["homeScore"] - m["awayScore"]))
    bw = blow["homeTeamId"] if blow["winner"] == "HOME" else blow["awayTeamId"]
    add("blowout", "Biggest beating", f"{abs(blow['homeScore'] - blow['awayScore']):.1f}",
        f"{who(bw)} over {who(opp[bw])}, "
        f"{max(blow['homeScore'], blow['awayScore']):.1f} to "
        f"{min(blow['homeScore'], blow['awayScore']):.1f}.", mgr=who(bw))
    nail = min(games, key=lambda m: abs(m["homeScore"] - m["awayScore"]))
    nw = nail["homeTeamId"] if nail["winner"] == "HOME" else nail["awayTeamId"]
    add("nail", "Closest game", f"{abs(nail['homeScore'] - nail['awayScore']):.2f}",
        f"{who(nw)} edged {who(opp[nw])}.", mgr=who(nw))

    avg = sum(score.values()) / len(score)
    add("avg", "League average", f"{avg:.1f}",
        f"Across {len(score)} teams, from {score[lo]:.1f} to {score[hi]:.1f}.")
    return A


def main(season: int | None = None):
    seasons = load("seasons"); teams = load("teams"); matchups = load("matchups")
    pw = load("player_weeks"); picks = load("draft_picks"); players = load("players")
    try:
        outlook = load("outlook")
    except FileNotFoundError:
        outlook = []
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
    slotc = {int(k): v for k, v in (meta_s.get("slotCounts") or {}).items()}
    start_slots = {k: v for k, v in slotc.items() if k not in BENCH_SLOTS}
    owner = {(t["season"], t["teamId"]): short.get(t["owner"]) for t in teams}
    tname = {(t["season"], t["teamId"]): (t["name"] or t["abbrev"]) for t in teams}
    pname = {p["playerId"]: p["name"] for p in players}
    ppos = {p["playerId"]: p["position"] for p in players}
    ppro = {p["playerId"]: p.get("proTeam") for p in players}
    kits = json.loads((REPO / "data" / "kits.json").read_text())["kits"] \
        if (REPO / "data" / "kits.json").exists() else {}
    logos = json.loads((DER / "logos.json").read_text()) if (DER / "logos.json").exists() else {}

    # kit per manager for THIS season: their ESPN logo and a colour derived
    # from it; kits.json only fills in for managers without a logo, or pins
    for t in rows_for_kits(teams, SEASON):
        p = owner.get((SEASON, t["teamId"]))
        rec = logos.get(str(SEASON), {}).get(str(t["teamId"]))
        if not p or not rec:
            continue
        k = dict(kits.get(p) or {})
        k["logo"] = rec["file"]
        if rec.get("color") and not k.get("pin"):
            k["c1"] = rec["color"]
        k.setdefault("c2", "#F2EFE9")
        k.setdefault("mono", p[:2].upper())
        kits[p] = k

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
    # (predicted probability, did it happen) for every side of every matchup,
    # so the reliability diagram is built from outcomes rather than from the
    # model's own confidence
    pairs = []
    for m in matchups:
        if not m["winner"] or m["winner"] in ("UNDECIDED", "TIE"):
            continue
        a = proj_tot.get((m["season"], m["week"], m["homeTeamId"]), 0)
        b = proj_tot.get((m["season"], m["week"], m["awayTeamId"]), 0)
        if a > 20 and b > 20:
            tot += 1
            hit += (("HOME" if a > b else "AWAY") == m["winner"])
            pr = norm_cdf((a - b) / (sd * math.sqrt(2)))
            pairs.append((pr, m["winner"] == "HOME"))
            pairs.append((1 - pr, m["winner"] == "AWAY"))
    buckets = defaultdict(list)
    for pr, wonit in pairs:
        buckets[min(9, int(pr * 10))].append((pr, wonit))
    calib_bins = [{
        "bin": b / 10,
        "pred": round(sum(x for x, _ in v) / len(v), 4),
        "act": round(sum(1 for _, y in v if y) / len(v), 4),
        "n": len(v),
    } for b, v in sorted(buckets.items())]

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
    # lineup decisions per team-week (legal swaps only) and points by slot
    dec = []          # {wk, tid, p, started, best, regret, chalk, swap}
    slot_pts = []     # {wk, tid, slot, pts}
    regret_tot = defaultdict(float)
    for (wk, tid), ents in sorted(ros.items()):
        d = decisions(ents, start_slots) if start_slots else None
        if d:
            sw = d["swap"]
            dec.append({"wk": wk, "tid": tid, "p": owner.get((SEASON, tid)),
                        "started": d["started"], "best": d["best"],
                        "regret": d["regret"], "chalk": d["chalk"],
                        "swap": ({"d": sw["d"], "slot": sw["slot"], "chalk": sw["chalk"],
                                  "in": pname.get(sw["in"]), "out": pname.get(sw["out"]),
                                  "inPts": sw["inPts"], "outPts": sw["outPts"]}
                                 if sw else None)})
            regret_tot[tid] += d["regret"]
        by_slot = defaultdict(float)
        for e in ents:
            if e["started"] and e["actual"] is not None:
                by_slot[e["slot"]] += e["actual"]
        for sl, v in by_slot.items():
            slot_pts.append({"wk": wk, "tid": tid, "p": owner.get((SEASON, tid)),
                             "slot": sl, "pts": round(v, 1)})

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
            "rg": round(regret_tot[t["teamId"]], 1) if t["teamId"] in regret_tot else None,
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
            "hid": m["homeTeamId"], "aid": m["awayTeamId"],
            "htm": tname.get((SEASON, m["homeTeamId"])),
            "atm": tname.get((SEASON, m["awayTeamId"])),
            "hs": m["homeScore"] or None, "as": m["awayScore"] or None,
            "won": m["winner"] if m["winner"] != "UNDECIDED" else None,
            "po": m["isPlayoff"],
        })

    # top started player on each side of every played game, for the scoreboard
    game_top = {}
    for (wk, tid), ents in ros.items():
        st_ = [e for e in ents if e["started"] and e["actual"] is not None]
        if st_:
            m0 = max(st_, key=lambda e: e["actual"])
            game_top[f"{wk}|{tid}"] = {"pid": m0["playerId"], "n": pname.get(m0["playerId"]),
                                       "pos": ppos.get(m0["playerId"]),
                                       "pro": ppro.get(m0["playerId"]), "pts": m0["actual"]}

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
                    "pid": e["playerId"], "pro": ppro.get(e["playerId"]),
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
        a = weekly_awards(SEASON, wk, pw, matchups, owner, tname, pname, ppos,
                          ppro, start_slots)
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
            a = weekly_awards(ps, pwk, pw, matchups, own_prev, tname, pname, ppos,
                              ppro, start_slots)
            if a:
                sample = {"season": ps, "week": pwk}
                awards[f"{ps}|{pwk}"] = a

    power = power_rankings(SEASON, weeks_done, pw, outlook, slotc, owner, tname)

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
        # The season page charts the league, not the method, so the raw
        # distributions are no longer shipped - only the calibration figures
        # the footnotes quote. (build_site still keeps the full arrays for the
        # record book.)
        "calib": {"sd": round(sd, 2), "hit": round(hit / tot, 3) if tot else None,
                  "n": tot, "residN": len(resid)},
        "awards": awards, "sample": sample, "power": power,
        "standings": standings, "schedule": sched, "rosters": rosters,
        "draft": draft, "form": form,
        "kits": kits, "h2h": head_to_head(matchups, owner),
        "gameTop": game_top, "decisions": dec, "slotPts": slot_pts,
        "slots": [SLOT_NAMES.get(k, str(k)) for k in sorted(start_slots, key=SLOT_ORDER.get)],
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
