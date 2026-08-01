"""Unit tests for roof-aware weather rules and game-venue resolution."""

from __future__ import annotations

from dataclasses import dataclass

from sleeper_advisor.context_builder import _weather_for_stadium
from sleeper_advisor.stadiums import TEAM_STADIUMS, resolve_game_stadium
from sleeper_advisor.weather_client import WeatherForecast


@dataclass
class _FakeWeatherClient:
    calls: list

    def forecast_for_kickoff(self, lat, lon, kickoff_utc_iso):
        self.calls.append((lat, lon, kickoff_utc_iso))
        return WeatherForecast(
            temperature_f=45.0,
            wind_mph=22.0,
            precipitation_probability_pct=10.0,
            condition_note="High wind -- can meaningfully suppress passing/kicking volume.",
        )


def test_dome_skips_weather_fetch():
    client = _FakeWeatherClient(calls=[])
    result = _weather_for_stadium(client, TEAM_STADIUMS["DET"], "2026-09-13T17:00:00Z")
    assert client.calls == []
    assert result is not None
    assert result["roof"] == "dome"
    assert "non-factor" in result["note"].lower()
    assert "temperature_f" not in result


def test_outdoor_fetches_forecast():
    client = _FakeWeatherClient(calls=[])
    result = _weather_for_stadium(client, TEAM_STADIUMS["GB"], "2026-09-13T17:00:00Z")
    assert len(client.calls) == 1
    assert result["wind_mph"] == 22.0
    assert result["roof"] == "outdoor"
    assert "High wind" in (result["note"] or "")


def test_retractable_fetches_and_flags_uncertainty():
    client = _FakeWeatherClient(calls=[])
    result = _weather_for_stadium(client, TEAM_STADIUMS["DAL"], "2026-09-13T17:00:00Z")
    assert len(client.calls) == 1
    assert result["roof"] == "retractable"
    assert "uncertain" in result["note"].lower() or "usually closed" in result["note"].lower()


def test_away_game_uses_home_team_stadium():
    # GB (outdoor) playing away at DET (dome) → venue is Ford Field
    stadium = resolve_game_stadium("GB", "away", "DET")
    assert stadium is not None
    assert stadium.roof == "dome"
    assert stadium.name == "Ford Field"

    # DET at home vs GB → still Ford Field
    home = resolve_game_stadium("DET", "home", "GB")
    assert home is not None
    assert home.roof == "dome"


def test_home_outdoor_uses_own_stadium():
    stadium = resolve_game_stadium("BUF", "home", "MIA")
    assert stadium is TEAM_STADIUMS["BUF"]
