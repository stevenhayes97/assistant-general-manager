"""Thin wrapper around the public, unauthenticated Sleeper API.

Docs: https://docs.sleeper.com/
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

BASE_URL = "https://api.sleeper.app/v1"
CACHE_DIR = Path("/tmp/sleeper_advisor_cache")
PLAYERS_CACHE_TTL_SECONDS = 12 * 60 * 60  # players/nfl is a ~5MB static-ish blob


class SleeperClient:
    def __init__(self, session: requests.Session | None = None, timeout: int = 15):
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        resp = self.session.get(f"{BASE_URL}{path}", timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def get_nfl_state(self) -> dict:
        """Current season/week per Sleeper. Great default for 'this week'."""
        return self._get("/state/nfl")

    def get_league(self, league_id: str) -> dict:
        return self._get(f"/league/{league_id}")

    def get_rosters(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/rosters")

    def get_users(self, league_id: str) -> list[dict]:
        return self._get(f"/league/{league_id}/users")

    def get_matchups(self, league_id: str, week: int) -> list[dict]:
        """Fantasy-league matchups (who plays whom in YOUR league this week)."""
        return self._get(f"/league/{league_id}/matchups/{week}")

    def resolve_roster_id(self, league_id: str, username: str) -> int:
        users = self.get_users(league_id)
        user = next(
            (u for u in users if u.get("display_name", "").lower() == username.lower()),
            None,
        )
        if user is None:
            raise ValueError(f"No user named '{username}' found in league {league_id}")
        rosters = self.get_rosters(league_id)
        roster = next((r for r in rosters if r.get("owner_id") == user["user_id"]), None)
        if roster is None:
            raise ValueError(f"No roster found for user '{username}' in league {league_id}")
        return roster["roster_id"]

    def get_all_players(self, force_refresh: bool = False) -> dict[str, dict]:
        """Full NFL player dictionary, keyed by Sleeper player_id.

        This endpoint is large (~5MB) and Sleeper explicitly asks callers to
        cache it (their guidance: fetch at most once per day), so we cache to
        disk with a TTL instead of hitting it on every invocation.
        """
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / "players_nfl.json"

        if not force_refresh and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < PLAYERS_CACHE_TTL_SECONDS:
                return json.loads(cache_file.read_text())

        data = self._get("/players/nfl")
        cache_file.write_text(json.dumps(data))
        return data
