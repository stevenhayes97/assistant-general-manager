"""Vegas lines via The Odds API (https://the-odds-api.com/) -- optional.

Spread and total are the single best proxy available for "game script":
a big spread + moderate/low total signals blowout risk (bench workhorse
backs/possession WRs on the trailing team who might see garbage-time-only
work; conversely, players on a big favorite in a low-competition matchup
may see their volume cut once the game is decided early -- exactly the
"weak opponent, takes foot off the gas" scenario you described).

Requires a free-tier API key. If none is configured, callers should treat
this signal as unavailable rather than failing the whole run.
"""

from __future__ import annotations

from dataclasses import dataclass

import requests

BASE_URL = "https://api.the-odds-api.com/v4/sports/americanfootball_nfl/odds"

_TEAM_NAME_TO_ABBR = {
    "Arizona Cardinals": "ARI", "Atlanta Falcons": "ATL", "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF", "Carolina Panthers": "CAR", "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN", "Cleveland Browns": "CLE", "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN", "Detroit Lions": "DET", "Green Bay Packers": "GB",
    "Houston Texans": "HOU", "Indianapolis Colts": "IND", "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC", "Las Vegas Raiders": "LV", "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR", "Miami Dolphins": "MIA", "Minnesota Vikings": "MIN",
    "New England Patriots": "NE", "New Orleans Saints": "NO", "New York Giants": "NYG",
    "New York Jets": "NYJ", "Philadelphia Eagles": "PHI", "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF", "Seattle Seahawks": "SEA", "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN", "Washington Commanders": "WAS",
}


@dataclass(frozen=True)
class GameOdds:
    favorite: str | None
    spread: float | None  # positive magnitude, e.g. 7.5
    total: float | None
    team_implied_total: dict[str, float]


class OddsClient:
    def __init__(self, api_key: str, session: requests.Session | None = None, timeout: int = 15):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def get_week_odds(self) -> dict[str, GameOdds]:
        """Return {team_abbr: GameOdds} for all currently-listed upcoming games."""
        resp = self.session.get(
            BASE_URL,
            params={
                "apiKey": self.api_key,
                "regions": "us",
                "markets": "spreads,totals",
                "oddsFormat": "american",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        events = resp.json()

        result: dict[str, GameOdds] = {}
        for event in events:
            home_name = event.get("home_team")
            away_name = event.get("away_team")
            home_abbr = _TEAM_NAME_TO_ABBR.get(home_name)
            away_abbr = _TEAM_NAME_TO_ABBR.get(away_name)
            if not home_abbr or not away_abbr:
                continue

            spread_by_team: dict[str, float] = {}
            total_points: float | None = None
            for bookmaker in event.get("bookmakers", []):
                for market in bookmaker.get("markets", []):
                    if market["key"] == "spreads":
                        for outcome in market.get("outcomes", []):
                            abbr = _TEAM_NAME_TO_ABBR.get(outcome["name"])
                            if abbr:
                                spread_by_team.setdefault(abbr, outcome["point"])
                    elif market["key"] == "totals" and total_points is None:
                        outcomes = market.get("outcomes", [])
                        if outcomes:
                            total_points = outcomes[0]["point"]
                if spread_by_team and total_points is not None:
                    break  # one bookmaker's numbers are enough for the POC

            favorite = None
            magnitude = None
            if spread_by_team:
                favorite = min(spread_by_team, key=lambda t: spread_by_team[t])
                magnitude = abs(spread_by_team[favorite])

            implied: dict[str, float] = {}
            if magnitude is not None and total_points is not None:
                implied[favorite] = round(total_points / 2 + magnitude / 2, 1)
                underdog = away_abbr if favorite == home_abbr else home_abbr
                implied[underdog] = round(total_points / 2 - magnitude / 2, 1)

            odds = GameOdds(
                favorite=favorite,
                spread=magnitude,
                total=total_points,
                team_implied_total=implied,
            )
            result[home_abbr] = odds
            result[away_abbr] = odds

        return result
