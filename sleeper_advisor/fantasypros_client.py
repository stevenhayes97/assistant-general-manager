"""FantasyPros weekly projections (requires FANTASYPROS_API_KEY).

Docs: https://api.fantasypros.com/v2/docs

Base URL: ``https://api.fantasypros.com/public/v2/json`` (the legacy
``/v2/json`` host returns 403 for most keys).

Join path to Sleeper: FantasyPros ``sportsdata_player_id`` matches Sleeper
``sportradar_id`` (UUID). The players directory is cached on disk (large).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from .projections import PlayerProjection, ScoringFormat

# Public docs use this base; the legacy ``/v2/json`` host returns 403 for most keys.
BASE_URL = "https://api.fantasypros.com/public/v2/json"
CACHE_DIR = Path("/tmp/sleeper_advisor_cache")
PLAYERS_CACHE_TTL_SECONDS = 12 * 60 * 60

_SCORING_PARAM = {
    "ppr": "PPR",
    "half_ppr": "HALF",
    "std": "STD",
}

_SKILL_POSITIONS = "QB:RB:WR:TE:K:DST"


class FantasyProsClient:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout
        # Populated by the most recent projections fetch (for CLI / agent notes).
        self.last_projection_note: str | None = None
        self.last_week_projection_rows: int = 0

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.session.get(
            f"{BASE_URL}{path}",
            params=params or {},
            headers={
                "x-api-key": self.api_key,
                "Accept": "application/json",
            },
            timeout=self.timeout,
        )
        if resp.status_code == 403:
            raise PermissionError(
                "FantasyPros API returned 403 Forbidden. Confirm the key at "
                "https://secure.fantasypros.com/api-keys/request and that it is "
                "sent as the x-api-key header. Premium projection endpoints "
                "require an active HOF subscription."
            )
        resp.raise_for_status()
        return resp.json()

    def get_sportradar_to_fpid(self, force_refresh: bool = False) -> dict[str, str]:
        """Map Sportradar/sportsdata UUID → FantasyPros player_id (string)."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / "fantasypros_sportradar_to_fpid.json"

        if not force_refresh and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < PLAYERS_CACHE_TTL_SECONDS:
                return json.loads(cache_file.read_text())

        data = self._get("/nfl/players")
        mapping: dict[str, str] = {}
        for row in data.get("players") or []:
            if not isinstance(row, dict):
                continue
            sportsdata_id = row.get("sportsdata_player_id")
            fpid = row.get("player_id")
            if sportsdata_id and fpid is not None:
                mapping[str(sportsdata_id).lower()] = str(fpid)

        cache_file.write_text(json.dumps(mapping))
        return mapping

    def get_week_projections(
        self,
        season: int | str,
        week: int,
        scoring: ScoringFormat = "ppr",
        *,
        season_type: str | None = None,
    ) -> dict[str, PlayerProjection]:
        """Return projections keyed by FantasyPros player id (fpid string).

        Does not fall back to ``week=0`` — on the public/free tier that bucket
        is often **season** point totals, not a single week.
        """
        parsed, note = self._fetch_projections(season, int(week), scoring)
        self.last_week_projection_rows = len(parsed)
        if note:
            self.last_projection_note = note
        return parsed

    def _fetch_projections(
        self,
        season: int | str,
        week: int,
        scoring: ScoringFormat,
    ) -> tuple[dict[str, PlayerProjection], str | None]:
        data = self._get(
            f"/nfl/{season}/projections",
            params={
                "week": week,
                "positions": _SKILL_POSITIONS,
                "scoring": _SCORING_PARAM[scoring],
            },
        )
        note: str | None = None
        if data.get("public_api_limited"):
            tier = data.get("tier") or "unknown"
            count = data.get("count")
            note = (
                f"FantasyPros tier={tier}, public_api_limited=true "
                f"(week={week}, count={count}). Weekly projections for this "
                "week may require premium/HOF API access."
            )
        out: dict[str, PlayerProjection] = {}
        players = data.get("players") or []
        for row in players:
            if not isinstance(row, dict):
                continue
            fpid = row.get("fpid")
            if fpid is None:
                fpid = row.get("player_id")
            if fpid is None:
                continue
            stats = row.get("stats") or {}
            pts_std = _as_float(stats.get("points"))
            pts_ppr = _as_float(stats.get("points_ppr"))
            pts_half = _as_float(stats.get("points_half"))
            # Kickers/DST/QB often only expose a single ``points`` field.
            if pts_ppr is None:
                pts_ppr = pts_std
            if pts_half is None:
                pts_half = pts_std
            if pts_ppr is None and pts_half is None and pts_std is None:
                continue

            position = row.get("position_id") or row.get("position")
            rec = _as_float(stats.get("rec_rec"))
            out[str(fpid)] = PlayerProjection(
                source="fantasypros",
                pts_ppr=pts_ppr,
                pts_half_ppr=pts_half,
                pts_std=pts_std,
                rec=rec,
                bonus_rec_te=rec if (position or "").upper() == "TE" else None,
                position=position,
            )
        return out, note

    def get_projections_by_sleeper_id(
        self,
        sleeper_players: dict[str, dict],
        season: int | str,
        week: int,
        scoring: ScoringFormat = "ppr",
        *,
        season_type: str | None = None,
        roster_player_ids: list[str] | None = None,
    ) -> dict[str, PlayerProjection]:
        """Fetch FP projections and re-key them to Sleeper player_ids."""
        fp_by_fpid = self.get_week_projections(
            season=season,
            week=week,
            scoring=scoring,
            season_type=season_type,
        )
        if not fp_by_fpid:
            return {}

        sportradar_to_fpid = self.get_sportradar_to_fpid()
        target_ids = roster_player_ids or list(sleeper_players)
        out: dict[str, PlayerProjection] = {}
        for sleeper_id in target_ids:
            player = sleeper_players.get(sleeper_id) or {}
            sportradar = player.get("sportradar_id")
            if not sportradar:
                continue
            fpid = sportradar_to_fpid.get(str(sportradar).lower())
            if not fpid:
                continue
            proj = fp_by_fpid.get(fpid)
            if proj:
                out[sleeper_id] = proj
        return out


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
