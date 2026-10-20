#!/usr/bin/env python3
"""QB air-yards charts for the Analysis page (kind `qb_scatter`).

EPA per play vs CPOE per passer, one chart per air-yards band. The bands tile
the axis, so every pass attempt with an air_yards value lands in exactly one:

  short   under 5 air yards (incl. behind the line)   min 40 att
  medium  5 to 14                                     min 40 att
  deep    15 and up                                   min 10 att

Ported from ~/dev/data/analyses/nfl-qb-scatter/analysis.py (pandas) to the
standard library so the weekly refresh rebuilds these with everything else;
the port reproduces that script's numbers exactly (verified on weeks 1-6).

Source: nflverse play_by_play_<yr>.csv.gz (fetched by ffl.nfl). Called by
ffl.build_stats; upserts into site/analysis.json by key, so a chart keeps its
place on the page as weeks are added.
"""

from __future__ import annotations

import csv
import gzip
import math
from collections import Counter
from datetime import datetime, timezone

from ffl import nfl

PRESETS = [
    # (preset, title, scope, test(air_yards), min attempts)
    ("deep_ball", "QB deep ball performance", "Pass attempts of 15+ air yards",
     lambda a: a >= 15, 10),
    ("medium", "QB intermediate performance", "Pass attempts of 5–14 air yards",
     lambda a: 5 <= a <= 14.99, 40),
    ("short", "QB short game performance", "Pass attempts under 5 air yards (incl. behind the line)",
     lambda a: a < 5, 40),
]


def _num(v):
    if v in ("", "NA", None):
        return None
    try:
        return float(v)
    except ValueError:
        return None


def load_passes(season: int, max_week: int | None = None) -> list[dict]:
    path = nfl.NFL / str(season) / f"play_by_play_{season}.csv.gz"
    out = []
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["season_type"] != "REG" or r["pass_attempt"] != "1" or r["sack"] != "0":
                continue
            wk = int(float(r["week"]))
            if max_week and wk > max_week:
                continue
            ay = _num(r["air_yards"])
            if ay is None or not r["passer_player_id"] or r["passer_player_id"] == "NA":
                continue
            out.append({"wk": wk, "play": _num(r["play_id"]) or 0, "ay": ay,
                        "epa": _num(r["epa"]), "cpoe": _num(r["cpoe"]),
                        "id": r["passer_player_id"], "name": r["passer_player_name"],
                        "team": r["posteam"]})
    return out


def _mean(v):
    v = [x for x in v if x is not None]
    return sum(v) / len(v) if v else None


def _sd(v):
    v = [x for x in v if x is not None]
    if len(v) < 2:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def build(season: int, max_week: int | None = None) -> list[dict]:
    passes = load_passes(season, max_week)
    weeks = sorted({p["wk"] for p in passes})
    stamp = datetime.fromtimestamp(
        (nfl.NFL / str(season) / f"play_by_play_{season}.csv.gz").stat().st_mtime,
        timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    charts = []
    for preset, title, scope, test, minn in PRESETS:
        plays = [p for p in passes if test(p["ay"])]
        by = {}
        for p in plays:
            by.setdefault(p["id"], []).append(p)
        ref_x = _mean([p["epa"] for p in plays])
        ref_y = _mean([p["cpoe"] for p in plays])
        qbs = []
        for pid, ps in by.items():
            n = len(ps)
            if n < minn:
                continue
            names = Counter(p["name"] for p in ps)
            top = max(names.values())
            name = min(k for k, c in names.items() if c == top)      # pandas mode tie-break
            team = max(ps, key=lambda p: (p["wk"], p["play"]))["team"]
            x, y, sd = _mean([p["epa"] for p in ps]), _mean([p["cpoe"] for p in ps]), _sd([p["epa"] for p in ps])
            se = sd / math.sqrt(n) if sd is not None else 0.0
            qbs.append({"id": pid, "name": name, "team": team, "n": n,
                        "x": round(x, 3), "y": round(y, 2), "x_se": round(se, 3),
                        "_sig": abs(x - ref_x) > 1.96 * se, "_se": se})
        qbs.sort(key=lambda q: -q["x"])
        ses = sorted(q.pop("_se") for q in qbs)
        med = (ses[len(ses) // 2] if len(ses) % 2 else (ses[len(ses) // 2 - 1] + ses[len(ses) // 2]) / 2) if ses else 0
        charts.append({
            "preset": preset, "title": title, "scope": scope, "season": season,
            "weeks": [weeks[0], weeks[-1]], "min": minn, "unit": "att",
            "x_label": "EPA / play", "y_label": "CPOE",
            "league": {"x": round(ref_x, 3), "y": round(ref_y, 2), "plays": len(plays),
                       "qbs_qualified": len(qbs),
                       "qbs_x_distinguishable": sum(q.pop("_sig") for q in qbs),
                       "median_x_se": round(med, 3)},
            "retrieved": stamp, "qbs": qbs,
            "key": f"{preset}_{season}", "kind": "qb_scatter",
        })
    return charts


def upsert(analysis: dict, charts: list[dict]) -> None:
    for ch in charts:
        i = next((i for i, c in enumerate(analysis["charts"]) if c.get("key") == ch["key"]), None)
        if i is None:
            analysis["charts"].append(ch)
        else:
            analysis["charts"][i] = ch
