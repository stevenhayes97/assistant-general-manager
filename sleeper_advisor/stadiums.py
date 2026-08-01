"""Static NFL stadium reference data: coordinates + roof type.

Small, stable dataset (32 teams share a handful of stadiums) -- easier and
more reliable for weather lookups than geocoding a city name at runtime.

Roof types:
- ``outdoor`` — always open; weather applies.
- ``dome`` — always closed; weather is a non-factor (do not forecast).
- ``retractable`` — usually closed, but we still fetch a forecast and flag
  the uncertainty rather than guessing whether the roof will be open.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stadium:
    name: str
    lat: float
    lon: float
    roof: str  # "outdoor" | "dome" | "retractable"

    @property
    def is_dome(self) -> bool:
        return self.roof == "dome"

    @property
    def should_fetch_weather(self) -> bool:
        """True for outdoor and retractable venues; False for fixed domes."""
        return self.roof in ("outdoor", "retractable")

    # Back-compat alias used by older call sites / tests.
    @property
    def is_outdoor_exposed(self) -> bool:
        return self.should_fetch_weather


def resolve_game_stadium(
    team_abbr: str, home_away: str | None, opponent: str | None
) -> Stadium | None:
    """Return the stadium where the game is played (home team's venue).

    Weather and roof type must follow the *game venue*, not the player's
    home stadium — an away outdoor team playing in a dome has no weather risk.
    """
    if home_away == "home":
        return TEAM_STADIUMS.get(team_abbr)
    if home_away == "away" and opponent:
        return TEAM_STADIUMS.get(opponent)
    return TEAM_STADIUMS.get(team_abbr)


TEAM_STADIUMS: dict[str, Stadium] = {
    "ARI": Stadium("State Farm Stadium", 33.5276, -112.2626, "retractable"),
    "ATL": Stadium("Mercedes-Benz Stadium", 33.7554, -84.4008, "retractable"),
    "BAL": Stadium("M&T Bank Stadium", 39.2780, -76.6227, "outdoor"),
    "BUF": Stadium("Highmark Stadium", 42.7738, -78.7870, "outdoor"),
    "CAR": Stadium("Bank of America Stadium", 35.2258, -80.8528, "outdoor"),
    "CHI": Stadium("Soldier Field", 41.8623, -87.6167, "outdoor"),
    "CIN": Stadium("Paycor Stadium", 39.0955, -84.5161, "outdoor"),
    "CLE": Stadium("Huntington Bank Field", 41.5061, -81.6995, "outdoor"),
    "DAL": Stadium("AT&T Stadium", 32.7473, -97.0945, "retractable"),
    "DEN": Stadium("Empower Field at Mile High", 39.7439, -105.0201, "outdoor"),
    "DET": Stadium("Ford Field", 42.3400, -83.0456, "dome"),
    "GB": Stadium("Lambeau Field", 44.5013, -88.0622, "outdoor"),
    "HOU": Stadium("NRG Stadium", 29.6847, -95.4107, "retractable"),
    "IND": Stadium("Lucas Oil Stadium", 39.7601, -86.1639, "retractable"),
    "JAX": Stadium("EverBank Stadium", 30.3239, -81.6373, "outdoor"),
    "KC": Stadium("GEHA Field at Arrowhead Stadium", 39.0489, -94.4839, "outdoor"),
    "LV": Stadium("Allegiant Stadium", 36.0909, -115.1833, "dome"),
    "LAC": Stadium("SoFi Stadium", 33.9535, -118.3392, "dome"),
    "LAR": Stadium("SoFi Stadium", 33.9535, -118.3392, "dome"),
    "MIA": Stadium("Hard Rock Stadium", 25.9580, -80.2389, "outdoor"),
    "MIN": Stadium("U.S. Bank Stadium", 44.9735, -93.2575, "dome"),
    "NE": Stadium("Gillette Stadium", 42.0909, -71.2643, "outdoor"),
    "NO": Stadium("Caesars Superdome", 29.9511, -90.0812, "dome"),
    "NYG": Stadium("MetLife Stadium", 40.8135, -74.0745, "outdoor"),
    "NYJ": Stadium("MetLife Stadium", 40.8135, -74.0745, "outdoor"),
    "PHI": Stadium("Lincoln Financial Field", 39.9008, -75.1675, "outdoor"),
    "PIT": Stadium("Acrisure Stadium", 40.4468, -80.0158, "outdoor"),
    "SF": Stadium("Levi's Stadium", 37.4032, -121.9698, "outdoor"),
    "SEA": Stadium("Lumen Field", 47.5952, -122.3316, "outdoor"),
    "TB": Stadium("Raymond James Stadium", 27.9759, -82.5033, "outdoor"),
    "TEN": Stadium("Nissan Stadium", 36.1665, -86.7713, "outdoor"),
    "WAS": Stadium("Northwest Stadium", 38.9077, -76.8645, "outdoor"),
}
