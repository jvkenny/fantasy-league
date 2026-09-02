"""Dependency-free client for ESPN's v3 fantasy football API.

Verified against league 582222 ("The Fantasy League Est. 2018") on 2026-09-01.

Three things ESPN does that are not obvious, all handled here:

1. ROUTE IS PER-SEASON, NOT PER-ERA. The usual advice is "2018+ uses the season
   path, 2017 and earlier use leagueHistory". Not true for this league: 2019 and
   2021-2026 answer on the season path, but 2018 and 2020 return 401 there and
   only answer on leagueHistory. So we try the season path and fall back, then
   cache which route won.

2. TRANSACTIONS ARE PER SCORING PERIOD. `view=mTransactions2` on its own returns
   nothing at all. Add `scoringPeriodId=N` and it returns that week's activity.
   A whole season means looping the weeks.

3. leagueHistory RETURNS A ONE-ELEMENT LIST wrapping the league object.
"""

from __future__ import annotations

import gzip
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl"

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)



def _clean(value: str | None) -> str | None:
    """Strip whitespace/control characters a copy-paste may have introduced."""
    if not value:
        return None
    v = value.strip().strip('"').strip("'")
    v = "".join(ch for ch in v if ch not in "\r\n\t")
    return v or None


class EspnError(RuntimeError):
    pass


class AuthError(EspnError):
    """Cookies are missing, expired, or don't grant access to this league."""


class League:
    def __init__(self, league_id: int, season: int, espn_s2: str | None = None,
                 swid: str | None = None, *, throttle: float = 0.5):
        self.league_id = int(league_id)
        self.season = int(season)
        # Pasted cookies routinely carry a trailing \r, newline or space. An
        # HTTP header value containing \r is rejected outright by http.client,
        # so scrub before storing rather than crashing 300 chars deep in a
        # traceback that would print the credential.
        self.espn_s2 = _clean(espn_s2 or os.environ.get("ESPN_S2"))
        self.swid = _clean(swid or os.environ.get("SWID"))
        self.throttle = throttle
        self._last = 0.0
        self._route: str | None = None  # "season" | "history", decided on first call

    # -- plumbing ---------------------------------------------------------

    def _build(self, route: str, views, params, ) -> str:
        qs: list[tuple[str, str]] = []
        if route == "history":
            root = f"{BASE}/leagueHistory/{self.league_id}"
            qs.append(("seasonId", str(self.season)))
        else:
            root = f"{BASE}/seasons/{self.season}/segments/0/leagues/{self.league_id}"
        if isinstance(views, str):
            views = [views]
        for v in views or []:
            qs.append(("view", v))
        for k, v in (params or {}).items():
            if v is not None:
                qs.append((k, str(v)))
        return root + ("?" + urllib.parse.urlencode(qs) if qs else "")

    def _headers(self, extra: dict | None = None) -> dict:
        h = {"User-Agent": UA, "Accept": "application/json", "Accept-Encoding": "gzip"}
        if self.espn_s2 and self.swid:
            swid = self.swid if self.swid.startswith("{") else "{" + self.swid + "}"
            h["Cookie"] = f"espn_s2={self.espn_s2}; SWID={swid}"
        if extra:
            h.update(extra)
        return h

    def _fetch(self, url: str, extra: dict | None, retries: int):
        last: Exception | None = None
        for attempt in range(retries):
            gap = time.monotonic() - self._last
            if gap < self.throttle:
                time.sleep(self.throttle - gap)
            self._last = time.monotonic()
            req = urllib.request.Request(url, headers=self._headers(extra))
            try:
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    if r.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    data = json.loads(raw.decode("utf-8"))
                    return data[0] if isinstance(data, list) and len(data) == 1 else data
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    raise  # caller decides whether to try the other route
                last = e
                time.sleep(2 ** attempt)
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                last = e
                time.sleep(2 ** attempt)
        raise EspnError(f"season {self.season}: gave up after {retries} tries — {last}")

    def get(self, views=None, *, params: dict | None = None,
            fantasy_filter: dict | None = None, retries: int = 4):
        extra = {}
        if fantasy_filter is not None:
            extra["x-fantasy-filter"] = json.dumps(fantasy_filter, separators=(",", ":"))

        routes = [self._route] if self._route else ["season", "history"]
        err: Exception | None = None
        auth_err: Exception | None = None
        for route in routes:
            try:
                data = self._fetch(self._build(route, views, params), extra, retries)
                self._route = route
                return data
            except urllib.error.HTTPError as e:
                # A 401 anywhere outranks a later 404. Without this, the season
                # route's "not authorised" gets masked by the history route's
                # "no such season", and a credential problem is misreported as
                # a league that never existed.
                if e.code in (401, 403) and auth_err is None:
                    auth_err = e
                err = e
                continue
        if auth_err is not None:
            raise AuthError(
                f"HTTP {auth_err.code} on league {self.league_id} season "
                f"{self.season}: ESPN rejected the cookies."
            ) from auth_err
        code = getattr(err, "code", "?")
        raise EspnError(
            f"HTTP {code} on league {self.league_id} season {self.season}: "
            "no route served this season."
        ) from err

    # -- views ------------------------------------------------------------

    def core(self):
        """Settings, teams, members, rosters, standings, draft, full schedule.

        One call — ESPN happily combines these views and it keeps us well under
        the rate limit across nine seasons.
        """
        return self.get([
            "mSettings", "mTeam", "mRoster", "mStandings",
            "mDraftDetail", "mMatchupScore", "mSchedule",
        ])

    def week(self, scoring_period: int):
        """Per-player detail for one week: who started, projected vs actual."""
        return self.get(
            ["mBoxscore", "mMatchupScore", "mRoster"],
            params={"scoringPeriodId": scoring_period},
        )

    def transactions(self, scoring_period: int):
        """One week of adds/drops/trades. Empty without scoringPeriodId — see (2)."""
        return self.get(["mTransactions2"], params={"scoringPeriodId": scoring_period})

    def players(self, *, limit: int = 1200, scoring_period: int | None = None):
        """The season's whole player pool, rostered or not (for waiver analysis)."""
        flt = {"players": {"limit": limit,
                           "sortPercOwned": {"sortAsc": False, "sortPriority": 1}}}
        return self.get(["kona_player_info"],
                        params={"scoringPeriodId": scoring_period},
                        fantasy_filter=flt)
