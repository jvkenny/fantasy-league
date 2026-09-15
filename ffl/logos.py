#!/usr/bin/env python3
"""Fetch each team's ESPN logo into the site and derive a kit colour from it.

The logo URL comes with the mTeam view already captured in core.json. Three
kinds show up: ESPN-hosted SVGs (stable), ESPN custom uploads (need the
session cookie - a 401 here is expected; drop the file in by hand), and plain
hotlinks to whatever site the manager copied from (Redbubble, a newspaper),
which can vanish or block. So the bytes are copied into docs/logos/<season>/
once and served from there; nothing on the page hotlinks.

The kit colour is the most common saturated colour in the logo, pulled into a
lightness band that carries white text and shows on the cream ground. Nobody
decides anyone's colours - the logo they chose does.

Existing files are skipped, so re-running only fetches what is new. Run once a
season, or when someone changes their logo:

    python3 -m ffl.logos            # latest season
    python3 -m ffl.logos --season 2025
"""

from __future__ import annotations

import argparse
import colorsys
import json
import re
import subprocess
import tempfile
import urllib.request
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RAW = REPO / "data" / "raw"
DOCS = REPO / "docs"
OUT = REPO / "data" / "derived" / "logos.json"
UA = "Mozilla/5.0 (Macintosh) fantasy-league-site/1.0"
EXT = {"image/svg+xml": "svg", "image/jpeg": "jpg", "image/png": "png",
       "image/gif": "gif", "image/webp": "webp"}


def fetch(url: str) -> tuple[int, str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, r.headers.get_content_type(), r.read()
    except urllib.error.HTTPError as e:
        return e.code, "", b""
    except Exception as e:  # noqa: BLE001 - report and move on
        print(f"   !! {e}")
        return 0, "", b""


def _hex(rgb):
    return "#%02X%02X%02X" % tuple(int(round(c)) for c in rgb)


def dominant(path: Path) -> str | None:
    """Most common saturated colour, nudged into a usable lightness band."""
    from PIL import Image
    src = path
    if path.suffix == ".svg":
        # macOS Quick Look rasterises SVG; nothing else on the box does
        tmp = Path(tempfile.mkdtemp())
        subprocess.run(["qlmanage", "-t", "-s", "128", "-o", str(tmp), str(path)],
                       capture_output=True)
        pngs = list(tmp.glob("*.png"))
        if not pngs:
            return _svg_fill(path)
        src = pngs[0]
    im = Image.open(src).convert("RGBA")
    im.thumbnail((96, 96))
    buckets: Counter = Counter()
    for r, g, b, a in im.get_flattened_data() if hasattr(im, "get_flattened_data") else im.getdata():
        if a < 128:
            continue
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s < 0.35 or l < 0.12 or l > 0.88:
            continue                      # greys, black, white: not identity
        buckets[(round(h * 24) % 24, round(s * 4), round(l * 6))] += 1
    if not buckets:
        return "#3D3D3D"                  # a black-and-white logo: charcoal, not nothing
    (hb, sb, lb), _ = buckets.most_common(1)[0]
    h, s, l = hb / 24, min(1.0, max(0.45, sb / 4)), lb / 6
    l = min(0.50, max(0.30, l))           # readable with white text, visible on cream
    return _hex(tuple(c * 255 for c in colorsys.hls_to_rgb(h, l, s)))


def _svg_fill(path: Path) -> str | None:
    txt = path.read_text(errors="ignore")
    cols = Counter(c.upper() for c in re.findall(r'(?:fill|stroke)[=:]\s*["\']?(#[0-9a-fA-F]{6})', txt))
    for c, _ in cols.most_common():
        r, g, b = int(c[1:3], 16), int(c[3:5], 16), int(c[5:7], 16)
        h, l, s = colorsys.rgb_to_hls(r / 255, g / 255, b / 255)
        if s >= 0.35 and 0.12 < l < 0.88:
            return _hex(tuple(x * 255 for x in colorsys.hls_to_rgb(h, min(0.5, max(0.3, l)), s)))
    return "#3D3D3D"


def main(season: int | None) -> int:
    yrs = sorted(int(p.name) for p in RAW.iterdir() if p.name.isdigit())
    season = season or yrs[-1]
    core = json.loads((RAW / str(season) / "core.json").read_text())
    core = core[0] if isinstance(core, list) else core
    dest = DOCS / "logos" / str(season)
    dest.mkdir(parents=True, exist_ok=True)
    book = json.loads(OUT.read_text()) if OUT.exists() else {}
    entry = book.setdefault(str(season), {})
    print(f"logos for {season} -> {dest.relative_to(REPO)}")
    for t in core["teams"]:
        tid, url = t["id"], t.get("logo")
        have = next((p for p in dest.glob(f"{tid}.*")), None)
        if not have and url:
            code, ctype, data = fetch(url)
            ext = EXT.get(ctype)
            if code == 200 and ext and data:
                have = dest / f"{tid}.{ext}"
                have.write_bytes(data)
                print(f"   {tid:>2} {t.get('name','')[:28]:<28} fetched {len(data):>6}B {ext}")
            else:
                print(f"   {tid:>2} {t.get('name','')[:28]:<28} !! {code or 'error'} "
                      f"{'(needs the ESPN session - save it by hand as ' + str(tid) + '.<ext>)' if code == 401 else ''}")
        if have:
            rec = entry.setdefault(str(tid), {})
            rec["file"] = str(have.relative_to(DOCS))
            rec["name"] = t.get("name")
            if not rec.get("color"):
                rec["color"] = dominant(have)
            print(f"   {tid:>2} {'':<28} {rec['file']:<22} {rec['color']}")
    OUT.write_text(json.dumps(book, indent=1))
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int)
    raise SystemExit(main(ap.parse_args().season))
