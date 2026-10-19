# AGENTS.md — Fantasy League Record Book

Guide for AI coding agents. `README.md` covers the ESPN API details, auth, and the weekly
refresh; this file is what you need to change the code safely.

## Identity

- **What:** a full historical export of one private ESPN fantasy football league (2018–present)
  and a two-page static site built from it: the all-time record book (`/`) and the current
  season tracker (`/season.html`), plus `/analysis.html` — charts John
  asks for on any topic. `site/analysis.json` holds `charts[]`, each with a `kind` that selects its
  renderer in `site/_analysis.html` (`qb_scatter` is written by
  `~/dev/data/analyses/nfl-qb-scatter/publish.py`; NFL logos self-hosted in `docs/logos/nfl/`).
  `/qb.html` is a redirect stub.
- **Repository:** `github.com/jvkenny/fantasy-league` (public). Default branch `main`.
  The maintainer's local checkout directory is named `fantasy-football`; that name is also the
  secret-store item name, so do not rename it.
- **Site:** https://jvkenny.github.io/fantasy-league/ — GitHub Pages from `main`, `/docs`.
- **Status:** active during the NFL season (one commit per completed week), dormant otherwise.

## Architecture

Pure Python standard library (3.9+), no dependencies. Pipeline:

```
ffl.export     ESPN API -> data/raw/<year>/*.json          (needs ESPN cookies; gitignored output)
ffl.normalize  data/raw -> data/derived/*.json             (gitignored; carries full names)
ffl.build_site      data/derived -> site/data.json         (all-time payload, committed)
ffl.build_season    data/derived -> site/season.json       (current-season payload, committed)
ffl.render     site/_template.html + _season.html + _theme.css + _charts.js + payloads
               -> site/index.html, site/season.html, site/analysis.html   (fragments for an artifact host)
               -> docs/index.html, docs/season.html, docs/analysis.html   (standalone pages for GitHub Pages)
ffl.seed_week0 week-0 power ranking from the draft (regenerable; never hand-place outlook files)
ffl.nfl        nflverse + ffverse public data -> data/nfl/<year>/ (gitignored; no auth)
ffl.build_stats   data/nfl + data/derived -> site/stats_{qb,rb,wr,te}.json + team_grade in
               site/analysis.json (position pages + analytics roster grade, league scoring)
ffl.backtest_grade  tests the grade on 2019, 2021-2025 -> reports/grade_backtest.json
```

Position pages (`/qb.html` … `/te.html`, template `site/_pos.html`) and `/analysis.html` wear the
**Haust** skin (`site/_haust.css`, John's Iceland palette, scoped by `data-skin=haust`); the record
book and season tracker keep `_theme.css`. The analytics grade sits NEXT TO the power rankings, not
inside them: the backtest found it only modestly better (within noise). Do not wire it into
`power_rankings()` without a new backtest and the maintainer's say-so.

Key code: `ffl/espn.py` (client, per-season route fallback), `ffl/build_season.py`
(`power_rankings()`, `decisions()`, `weekly_awards()`), `site/_charts.js` (SVG chart primitives).
`data/kits.json` holds per-manager colour/monogram identity kits.

## What a cloud agent can and cannot do

- **Can:** change templates, CSS, charts, and render logic, then run `python3 -m ffl.render`.
  The committed `site/data.json` and `site/season.json` are enough to rebuild both pages.
- **Cannot:** pull new ESPN data or rerun `normalize`/`build_*`. Those need the league's session
  cookies and the gitignored `data/raw/`, `data/derived/`, and `data/identities.json`, which exist
  only on the maintainer's machine. Never ask for or handle the cookies.

## Boundaries (publishing rules — keep them)

- Never commit `data/raw/`, `data/derived/`, or `data/identities.json` (full legal names).
- The site shows **first names only** and carries `noindex,nofollow`.
- No highlight of any single manager's row; the site is league-wide.
- No pregame win-probability previews (removed deliberately: ESPN's projected favourite won
  only ~56% of past matchups). Projections are used only post-game (Bust/Overachiever awards).
- No email subscribe form (removed deliberately).
- Power-ranking outlook term stays capped (`PR_LOOK_CAP`); points scored and W-L are not
  ranking terms. See the method notes in `build_season.py` before touching the formula.
- `render.py` must emit pure ASCII (`\uXXXX` escapes) so pages render under any charset.
- Charts pass a narrower viewBox on phones; grid columns use `minmax(min(Npx,100%),1fr)`.
- `.env` contains only secret-store references (`op://...`) plus the public league ID. Never put
  a resolved value in it or anywhere else.

## Development

```bash
python3 -m ffl.render                 # rebuild pages from committed payloads (no network)
python3 -m http.server 8000           # then open http://localhost:8000/docs/
```

No test suite. Verify by rendering, checking `git diff` touches only what you meant, and
viewing both pages at desktop and ~375px width.

Full weekly refresh (maintainer only, needs ESPN access):

```bash
python3 -m ffl.normalize && python3 -m ffl.build_site && \
  python3 -m ffl.build_season && python3 -m ffl.nfl 2026 && python3 -m ffl.build_stats && \
  python3 -m ffl.render
```

## Deployment

Pushing to `main` publishes via GitHub Pages (Pages caches ~10 minutes). Agents open a PR;
the maintainer merges.

## Handoff

- Rendering from committed payloads is reproducible (verified: no diff after `ffl.render`).
- Weekly data refresh is a manual, maintainer-run step during the season.
