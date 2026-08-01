"""NFL schedule/venue lookups via ESPN's public scoreboard endpoint.

This is an undocumented-but-widely-used, free, no-auth endpoint. It's used
here only for read-only schedule facts (who plays whom, home/away, venue,
kickoff time, dome vs. outdoor) -- nothing account-specific.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

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
        self._week_cache: dict[tuple[int, int, int], dict[str, GameInfo]] = {}
        self.last_schedule_note: str | None = None

    def get_week_games(self, week: int, season: int, season_type: int = 2) -> dict[str, GameInfo]:
        """Return {team_abbr: GameInfo} for every team playing in a given week.

        season_type: 1=preseason, 2=regular season, 3=postseason.
        """
        cache_key = (int(season), int(week), int(season_type))
        if cache_key in self._week_cache:
            return self._week_cache[cache_key]

        resp = self.session.get(
            BASE_URL,
            params={"week": week, "seasontype": season_type, "year": season},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        espn_year = _espn_response_season_year(data)
        if espn_year is not None and espn_year != int(season):
            self.last_schedule_note = (
                f"ESPN scoreboard returned season year {espn_year} for requested "
                f"{season} week {week}; ignoring stale schedule."
            )
            self._week_cache[cache_key] = {}
            return {}

        result = _parse_espn_scoreboard(data)
        self._week_cache[cache_key] = result
        return result

    @staticmethod
    def merge_week_games(*sources: dict[str, GameInfo]) -> dict[str, GameInfo]:
        merged: dict[str, GameInfo] = {}
        for src in sources:
            merged.update(src)
        return merged


def _espn_response_season_year(data: dict) -> int | None:
    season = data.get("season") or {}
    year = season.get("year")
    try:
        return int(year) if year is not None else None
    except (TypeError, ValueError):
        return None


def _parse_espn_scoreboard(data: dict) -> dict[str, GameInfo]:
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

        home_abbr = _normalize_espn_abbr(home["team"]["abbreviation"])
        away_abbr = _normalize_espn_abbr(away["team"]["abbreviation"])
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

    return result


def _normalize_espn_abbr(espn_abbr: str) -> str:
    return _ESPN_TO_SLEEPER_ABBR.get(espn_abbr, espn_abbr)


def games_from_tank01_week(body: list[dict]) -> dict[str, GameInfo]:
    """Build GameInfo map from Tank01 ``getNFLGamesForWeek`` body rows."""
    from .stadiums import TEAM_STADIUMS

    result: dict[str, GameInfo] = {}
    for row in body:
        if not isinstance(row, dict):
            continue
        home = _normalize_tank01_team(row.get("home"))
        away = _normalize_tank01_team(row.get("away"))
        if not home or not away:
            continue
        kickoff = _tank01_kickoff_utc(row)
        home_stadium = TEAM_STADIUMS.get(home)
        venue_name = home_stadium.name if home_stadium else None
        venue_indoor = (
            True
            if home_stadium and home_stadium.roof == "dome"
            else False
            if home_stadium and home_stadium.roof == "outdoor"
            else None
        )
        result[home] = GameInfo(
            opponent=away,
            home_away="home",
            kickoff_utc=kickoff,
            venue_name=venue_name,
            venue_indoor=venue_indoor,
            venue_city=None,
            venue_state=None,
        )
        result[away] = GameInfo(
            opponent=home,
            home_away="away",
            kickoff_utc=kickoff,
            venue_name=venue_name,
            venue_indoor=venue_indoor,
            venue_city=None,
            venue_state=None,
        )
    return result


def _normalize_tank01_team(value: object) -> str | None:
    if value is None:
        return None
    abbr = str(value).strip().upper()
    if not abbr:
        return None
    return _ESPN_TO_SLEEPER_ABBR.get(abbr, abbr)


def _tank01_kickoff_utc(row: dict) -> str | None:
    epoch_raw = row.get("gameTime_epoch")
    if epoch_raw is not None:
        try:
            epoch = float(epoch_raw)
            return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
        except (TypeError, ValueError, OSError):
            pass
    game_date = row.get("gameDate")
    if game_date:
        return f"{str(game_date)[:4]}-{str(game_date)[4:6]}-{str(game_date)[6:8]}T17:00:00Z"
    return None
