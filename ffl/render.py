#!/usr/bin/env python3
"""Assemble site/index.html from the template and the computed payload.

Emits pure ASCII. Typographic characters (times, minus, sigma, en dash, star)
are written as \\uXXXX escapes inside the app script, so the page renders
identically no matter what charset the host declares. This is not cosmetic:
python's http.server sends `text/html` with no charset, browsers fall back to
windows-1252, and every one of those characters turns to mojibake.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TPL = REPO / "site" / "_template.html"
DATA = REPO / "site" / "data.json"
OUT = REPO / "site" / "index.html"
PAGES = REPO / "docs" / "index.html"

MARK = "<script>\nconst D = JSON.parse"


def escape_non_ascii(text: str) -> str:
    return "".join(ch if ord(ch) < 128 else f"\\u{ord(ch):04x}" for ch in text)


def build(tpl_path: Path, data_path: Path, frag_out: Path, pages_out: Path) -> int:
    tpl = tpl_path.read_text(encoding="utf-8")
    data = data_path.read_text(encoding="utf-8")
    if not data.isascii():
        print(f"!! {data_path.name} is not ASCII - the build must use ensure_ascii=True")
        return 1

    i = tpl.index(MARK)
    head, script = tpl[:i], tpl[i:]
    if not head.isascii():
        print(f"!! non-ASCII outside the app script in {tpl_path.name}")
        return 1

    charts = (REPO / "site" / "_charts.js").read_text(encoding="utf-8")
    out = (head + escape_non_ascii(script)) \
        .replace("__CHARTS__", escape_non_ascii(charts)) \
        .replace("__DATA__", data)
    if not out.isascii():
        print("!! output still contains non-ASCII")
        return 1
    frag_out.write_text(out, encoding="ascii")

    j = out.index("</style>") + len("</style>")
    doc = (
        '<!doctype html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        '<meta name="color-scheme" content="light dark">\n'
        + out[:j] + "\n</head>\n<body>" + out[j:] + "\n</body>\n</html>\n"
    )
    pages_out.parent.mkdir(parents=True, exist_ok=True)
    pages_out.write_text(doc, encoding="ascii")
    (pages_out.parent / ".nojekyll").write_text("")
    print(f"  {pages_out.name:<12} {len(doc)/1024:>5.0f} KB  (fragment: {frag_out.name})")
    return 0


def main() -> int:
    print("rendering:")
    rc = build(REPO / "site" / "_template.html", REPO / "site" / "data.json",
               REPO / "site" / "index.html", REPO / "docs" / "index.html")
    stpl = REPO / "site" / "_season.html"
    sdat = REPO / "site" / "season.json"
    if stpl.exists() and sdat.exists():
        rc |= build(stpl, sdat, REPO / "site" / "season.html",
                    REPO / "docs" / "season.html")
    return rc


if __name__ == "__main__":
    sys.exit(main())
