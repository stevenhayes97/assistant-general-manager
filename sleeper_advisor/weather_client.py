"""Game-time weather forecast via Open-Meteo (free, no API key required)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import requests

BASE_URL = "https://api.open-meteo.com/v1/forecast"


@dataclass(frozen=True)
class WeatherForecast:
    temperature_f: float | None
    wind_mph: float | None
    precipitation_probability_pct: float | None
    condition_note: str | None


class WeatherClient:
    def __init__(self, session: requests.Session | None = None, timeout: int = 15):
        self.session = session or requests.Session()
        self.timeout = timeout

    def forecast_for_kickoff(
        self, lat: float, lon: float, kickoff_utc_iso: str | None
    ) -> WeatherForecast | None:
        """Best-effort hourly forecast nearest to kickoff.

        Open-Meteo's free forecast horizon is ~16 days, which comfortably
        covers "this week's" games. Returns None if kickoff is outside the
        forecast window or the lookup otherwise fails -- callers should
        treat that as "unknown," not "clear skies."
        """
        if not kickoff_utc_iso:
            return None

        try:
            kickoff = datetime.fromisoformat(kickoff_utc_iso.replace("Z", "+00:00"))
        except ValueError:
            return None

        now = datetime.now(timezone.utc)
        if (kickoff - now).days > 15:
            return None  # too far out for a meaningful forecast

        resp = self.session.get(
            BASE_URL,
            params={
                "latitude": lat,
                "longitude": lon,
                "hourly": "temperature_2m,wind_speed_10m,precipitation_probability",
                "temperature_unit": "fahrenheit",
                "wind_speed_unit": "mph",
                "timezone": "UTC",
                "forecast_days": 16,
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        hourly = data.get("hourly", {})
        times = hourly.get("time", [])
        if not times:
            return None

        target = kickoff.strftime("%Y-%m-%dT%H:00")
        if target not in times:
            # fall back to nearest available hour
            target = min(times, key=lambda t: abs(
                datetime.fromisoformat(t) - kickoff.replace(tzinfo=None)
            ))
        idx = times.index(target)

        def at(key: str):
            values = hourly.get(key, [])
            return values[idx] if idx < len(values) else None

        temp = at("temperature_2m")
        wind = at("wind_speed_10m")
        precip = at("precipitation_probability")

        note = None
        if wind is not None and wind >= 20:
            note = "High wind -- can meaningfully suppress passing/kicking volume."
        elif precip is not None and precip >= 60:
            note = "High chance of precipitation -- watch for a run-heavier game script."
        elif temp is not None and temp <= 20:
            note = "Extreme cold -- historically correlates with lower total scoring."

        return WeatherForecast(
            temperature_f=temp,
            wind_mph=wind,
            precipitation_probability_pct=precip,
            condition_note=note,
        )
