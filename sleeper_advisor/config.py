"""Configuration for the advisor, sourced from environment variables.

Kept deliberately simple for the POC (hardcoded via .env / shell env), but
every value is also accepted as a CLI override in cli.py so this same code
can later back a multi-tenant API where these become per-request inputs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


@dataclass(frozen=True)
class AdvisorConfig:
    league_id: str | None
    roster_id: int | None
    username: str | None
    odds_api_key: str | None
    week: int | None

    def require_league_id(self) -> str:
        if not self.league_id:
            raise ValueError(
                "SLEEPER_LEAGUE_ID is not set. Set it in .env or pass --league-id. "
                "Find it in your Sleeper league URL: "
                "https://sleeper.com/leagues/<LEAGUE_ID>/team"
            )
        return self.league_id


def load_config(
    *,
    league_id: str | None = None,
    roster_id: int | None = None,
    username: str | None = None,
    odds_api_key: str | None = None,
    week: int | None = None,
) -> AdvisorConfig:
    """Build config from explicit args first, falling back to env vars."""

    def env_int(name: str) -> int | None:
        val = os.environ.get(name)
        return int(val) if val else None

    return AdvisorConfig(
        league_id=league_id or os.environ.get("SLEEPER_LEAGUE_ID") or None,
        roster_id=roster_id if roster_id is not None else env_int("SLEEPER_ROSTER_ID"),
        username=username or os.environ.get("SLEEPER_USERNAME") or None,
        odds_api_key=odds_api_key or os.environ.get("ODDS_API_KEY") or None,
        week=week if week is not None else env_int("SLEEPER_WEEK"),
    )
