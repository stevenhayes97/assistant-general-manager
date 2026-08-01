"""NFL schedule/venue lookups via ESPN's public scoreboard endpoint.

This is an undocumented-but-widely-used, free, no-auth endpoint. It's used
here only for read-only schedule facts (who plays whom, home/away, venue,
kickoff time, dome vs. outdoor) -- nothing account-specific.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"

# Sleeper player `team` field uses standard two/three-letter abbreviations.
# ESPN sometimes uses slightly different ones for a handful of teams.
_ESPN_TO_SLEEPER_ABBR = {
    "WSH": "WAS",
    "JAX": "JAX",
    "LAR": "LAR",
    "LAC": "LAC",
}


@dataclass(frozen=True)
class GameInfo:
    opponent: str
    home_away: str  # "home" or "away"
    kickoff_utc: str | None
    venue_name: str | None
    venue_indoor: bool | None
    venue_city: str | None
    venue_state: str | None


class ScheduleClient:
    def __init__(self, session: requests.Session | None = None, timeout: int = 15):
        self.session = session or requests.Session()
        self.timeout = timeout
        self._week_cache: dict[int, dict[str, GameInfo]] = {}

    def get_week_games(self, week: int, season: int, season_type: int = 2) -> dict[str, GameInfo]:
        """Return {team_abbr: GameInfo} for every team playing in a given week.

        season_type: 1=preseason, 2=regular season, 3=postseason.
        """
        cache_key = week * 10 + season_type
        if cache_key in self._week_cache:
            return self._week_cache[cache_key]

        resp = self.session.get(
            BASE_URL,
            params={"week": week, "seasontype": season_type, "year": season},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        result: dict[str, GameInfo] = {}
        for event in data.get("events", []):
            competitions = event.get("competitions") or []
            if not competitions:
                continue
            comp = competitions[0]
            venue = comp.get("venue", {}) or {}
            address = venue.get("address", {}) or {}
            competitors = comp.get("competitors", []) or []
            if len(competitors) != 2:
                continue

            by_side = {c.get("homeAway"): c for c in competitors}
            home = by_side.get("home")
            away = by_side.get("away")
            if not home or not away:
                continue

            home_abbr = self._normalize(home["team"]["abbreviation"])
            away_abbr = self._normalize(away["team"]["abbreviation"])
            kickoff = comp.get("date") or event.get("date")

            result[home_abbr] = GameInfo(
                opponent=away_abbr,
                home_away="home",
                kickoff_utc=kickoff,
                venue_name=venue.get("fullName"),
                venue_indoor=venue.get("indoor"),
                venue_city=address.get("city"),
                venue_state=address.get("state"),
            )
            result[away_abbr] = GameInfo(
                opponent=home_abbr,
                home_away="away",
                kickoff_utc=kickoff,
                venue_name=venue.get("fullName"),
                venue_indoor=venue.get("indoor"),
                venue_city=address.get("city"),
                venue_state=address.get("state"),
            )

        self._week_cache[cache_key] = result
        return result

    @staticmethod
    def _normalize(espn_abbr: str) -> str:
        return _ESPN_TO_SLEEPER_ABBR.get(espn_abbr, espn_abbr)
