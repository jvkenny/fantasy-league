# The Fantasy League Est. 2018 — data pull

Full historical export of ESPN league **582222** (2018–present), plus the
visualisation site built on top of it.

- **League:** The Fantasy League Est. 2018 (named *Park Ridge Fantasy* through 2021)
- **John's team:** Chicago Double-Doink (teamId 8)
- **Format:** H2H points, 10 teams (8 in 2020–21), 14-week regular season, 5 playoff teams

## Auth

The league is private, so every call needs `espn_s2` + `SWID`. Per the repo secrets
standard these live in 1Password and never touch a working tree.

Create item **`fantasy-football`** in the **`Dev`** vault with two fields named
exactly `ESPN_S2` and `SWID`. To read the values: log in at fantasy.espn.com in
Chrome → DevTools → Application → Cookies → `https://fantasy.espn.com` → copy
`espn_s2` and `SWID` (keep SWID's surrounding braces).

Then everything runs through `op run`:

    op run --env-file .env -- python3 -m ffl.export

`espn_s2` is a long-lived session cookie (roughly a year). When the pull starts
returning 401, re-copy it — that is the only maintenance this needs.

Note: ESPN does **not** mark these cookies HttpOnly, so any script running on an
ESPN page can read them. Treat them as live credentials.

## What gets pulled

Per season, raw ESPN responses written verbatim to `data/raw/<year>/`:

| File | Contents |
|---|---|
| `core.json` | settings, members, teams, rosters, standings, **every draft pick**, full schedule with final scores |
| `week-NN-boxscore.json` | every rostered player that week — lineup slot (started vs benched), **projected and actual** points, and the ~38 raw stat fields behind them |
| `week-NN-transactions.json` | every add, drop, waiver claim and trade for that week |
| `players.json` | the season's entire player pool, rostered or not — for "best player left on waivers" analysis |

Nothing is discarded at capture time; normalisation happens downstream, so an
ESPN schema change can never cost us history.

## Three ESPN quirks this handles

Documented because each one silently returns *wrong or empty* data rather than an error:

1. **The route is per-season, not per-era.** The common advice — "2018+ uses the
   season path, earlier uses `leagueHistory`" — is false here. 2019 and 2021–2026
   answer on the season path; **2018 and 2020 return 401 there** and only answer on
   `leagueHistory`. The client tries both and caches the winner.
2. **Transactions are per scoring period.** `view=mTransactions2` alone returns an
   empty response — not an error. Add `scoringPeriodId=N` and week 5 of 2025 returns
   37 transactions. A full season means looping the weeks.
3. **`leagueHistory` wraps the league in a one-element list.**

## Refresh

The export is resumable — existing files are skipped, so a re-run only fetches
what is new. The exception is the last two weeks of a live season, always
re-fetched because ESPN revises recent stats.

    op run --env-file .env -- python3 -m ffl.export              # incremental
    op run --env-file .env -- python3 -m ffl.export --force      # rebuild all
    op run --env-file .env -- python3 -m ffl.export --seasons 2026
