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


def main() -> int:
    tpl = TPL.read_text(encoding="utf-8")
    data = DATA.read_text(encoding="utf-8")

    if not data.isascii():
        print("!! data.json is not ASCII — build_site must use ensure_ascii=True")
        return 1

    i = tpl.index(MARK)
    head, script = tpl[:i], tpl[i:]
    if not head.isascii():
        print("!! non-ASCII outside the app script; add a numeric entity for it")
        return 1

    out = head + escape_non_ascii(script)
    out = out.replace("__DATA__", data)

    if not out.isascii():
        print("!! output still contains non-ASCII")
        return 1

    OUT.write_text(out, encoding="ascii")
    print(f"wrote {OUT}  ({len(out)/1024:.0f} KB, pure ASCII)  [artifact fragment]")

    # GitHub Pages serves a real document; the Artifact host supplies its own
    # <head>, so that build must stay a fragment. Emit both from one source
    # rather than keeping two divergent copies of the page.
    i = out.index("</style>") + len("</style>")
    head, body = out[:i], out[i:]
    doc = (
        "<!doctype html>\n<html lang=\"en\">\n<head>\n"
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n'
        '<meta name="robots" content="noindex,nofollow">\n'
        '<meta name="color-scheme" content="light dark">\n'
        f"{head}\n</head>\n<body>{body}\n</body>\n</html>\n"
    )
    PAGES.parent.mkdir(parents=True, exist_ok=True)
    PAGES.write_text(doc, encoding="ascii")
    (PAGES.parent / ".nojekyll").write_text("")
    print(f"wrote {PAGES}  ({len(doc)/1024:.0f} KB)  [standalone, GitHub Pages]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
