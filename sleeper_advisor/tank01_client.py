"""Tank01 weekly projections via RapidAPI (requires TANK01_API_KEY).

Docs: https://rapidapi.com/tank01/api/tank01-nfl-live-in-game-real-time-statistics-nfl

Join path to Sleeper: Tank01 ``getNFLPlayerList`` includes ``sleeperBotID``
(and ``espnID`` as fallback). The players directory is cached on disk.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import requests

from .projections import PlayerProjection, ScoringFormat

BASE_URL = (
    "https://tank01-nfl-live-in-game-real-time-statistics-nfl.p.rapidapi.com"
)
RAPIDAPI_HOST = (
    "tank01-nfl-live-in-game-real-time-statistics-nfl.p.rapidapi.com"
)
CACHE_DIR = Path("/tmp/sleeper_advisor_cache")
PLAYERS_CACHE_TTL_SECONDS = 12 * 60 * 60


class Tank01Client:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        timeout: int = 30,
    ):
        self.api_key = api_key
        self.session = session or requests.Session()
        self.timeout = timeout

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        resp = self.session.get(
            f"{BASE_URL}{path}",
            params=params or {},
            headers={
                "x-rapidapi-key": self.api_key,
                "x-rapidapi-host": RAPIDAPI_HOST,
                "Accept": "application/json",
            },
            timeout=self.timeout,
        )
        resp.raise_for_status()
        return resp.json()

    def get_id_maps(
        self, force_refresh: bool = False
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Return (sleeper_id→tank01, espn_id→tank01) maps from player list.

        One RapidAPI call (or cache hit) builds both. ``sleeperBotID`` is the
        primary join; ESPN is a fallback when Sleeper's espn_id is present.
        """
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sleeper_cache = CACHE_DIR / "tank01_sleeper_to_player_id.json"
        espn_cache = CACHE_DIR / "tank01_espn_to_player_id.json"

        if (
            not force_refresh
            and sleeper_cache.exists()
            and espn_cache.exists()
        ):
            age = time.time() - sleeper_cache.stat().st_mtime
            if age < PLAYERS_CACHE_TTL_SECONDS:
                return (
                    json.loads(sleeper_cache.read_text()),
                    json.loads(espn_cache.read_text()),
                )

        data = self._get("/getNFLPlayerList")
        rows = data.get("body") if isinstance(data, dict) else data
        if not isinstance(rows, list):
            return {}, {}

        sleeper_map: dict[str, str] = {}
        espn_map: dict[str, str] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            tank_id = row.get("playerID")
            if not tank_id:
                continue
            tank_id_s = str(tank_id)
            sleeper_id = row.get("sleeperBotID")
            if sleeper_id:
                sleeper_map[str(sleeper_id)] = tank_id_s
            espn_id = row.get("espnID")
            if espn_id:
                espn_map[str(espn_id)] = tank_id_s

        sleeper_cache.write_text(json.dumps(sleeper_map))
        espn_cache.write_text(json.dumps(espn_map))
        return sleeper_map, espn_map

    def get_week_projections(
        self,
        week: int,
        scoring: ScoringFormat = "ppr",
        *,
        archive_season: int | str | None = None,
    ) -> dict[str, PlayerProjection]:
        """Return projections keyed by Tank01 playerID (and teamAbv for DST)."""
        del scoring  # fantasyPointsDefault always includes all three buckets
        params: dict[str, Any] = {
            "week": str(int(week)),
            "itemFormat": "map",
        }
        if archive_season is not None:
            params["archiveSeason"] = str(archive_season)

        data = self._get("/getNFLProjections", params=params)
        body = data.get("body") if isinstance(data, dict) else None
        if not isinstance(body, dict):
            return {}

        out: dict[str, PlayerProjection] = {}
        player_proj = body.get("playerProjections") or {}
        if isinstance(player_proj, dict):
            for tank_id, row in player_proj.items():
                if not isinstance(row, dict):
                    continue
                proj = _parse_player_projection(row)
                if proj:
                    out[str(row.get("playerID") or tank_id)] = proj

        defense_proj = body.get("teamDefenseProjections") or {}
        if isinstance(defense_proj, dict):
            for _key, row in defense_proj.items():
                if not isinstance(row, dict):
                    continue
                proj = _parse_defense_projection(row)
                if not proj:
                    continue
                team_abv = row.get("teamAbv") or row.get("team")
                if team_abv:
                    out[str(team_abv).upper()] = proj
        return out

    def get_projections_by_sleeper_id(
        self,
        sleeper_players: dict[str, dict],
        week: int,
        scoring: ScoringFormat = "ppr",
        *,
        archive_season: int | str | None = None,
        roster_player_ids: list[str] | None = None,
    ) -> dict[str, PlayerProjection]:
        """Fetch Tank01 projections and re-key them to Sleeper player_ids."""
        by_tank = self.get_week_projections(
            week=week,
            scoring=scoring,
            archive_season=archive_season,
        )
        if not by_tank:
            return {}

        sleeper_to_tank, espn_to_tank = self.get_id_maps()
        target_ids = roster_player_ids or list(sleeper_players)
        out: dict[str, PlayerProjection] = {}
        for sleeper_id in target_ids:
            tank_id = sleeper_to_tank.get(str(sleeper_id))
            if not tank_id:
                player = sleeper_players.get(sleeper_id) or {}
                espn_id = player.get("espn_id")
                if espn_id is not None:
                    tank_id = espn_to_tank.get(str(espn_id))
            if not tank_id:
                # Team defenses on Sleeper use team abbreviations as player_id.
                tank_id = str(sleeper_id).upper()
            proj = by_tank.get(tank_id)
            if proj:
                out[sleeper_id] = proj
        return out


def _parse_player_projection(row: dict[str, Any]) -> PlayerProjection | None:
    fp = row.get("fantasyPointsDefault") or {}
    if isinstance(fp, dict):
        pts_ppr = _as_float(fp.get("PPR"))
        pts_half = _as_float(fp.get("halfPPR"))
        pts_std = _as_float(fp.get("standard"))
    else:
        # Some responses may collapse to a single string.
        pts_ppr = pts_half = pts_std = _as_float(fp)
    if pts_ppr is None and pts_half is None and pts_std is None:
        return None

    receiving = row.get("Receiving") or {}
    rec = _as_float(receiving.get("receptions")) if isinstance(receiving, dict) else None
    position = row.get("pos") or row.get("position")
    return PlayerProjection(
        source="tank01",
        pts_ppr=pts_ppr,
        pts_half_ppr=pts_half,
        pts_std=pts_std,
        rec=rec,
        bonus_rec_te=rec if (position or "").upper() == "TE" else None,
        position=position,
    )


def _parse_defense_projection(row: dict[str, Any]) -> PlayerProjection | None:
    fp = row.get("fantasyPointsDefault")
    if isinstance(fp, dict):
        pts_ppr = _as_float(fp.get("PPR"))
        pts_half = _as_float(fp.get("halfPPR"))
        pts_std = _as_float(fp.get("standard"))
    else:
        pts_ppr = pts_half = pts_std = _as_float(fp)
    if pts_ppr is None and pts_half is None and pts_std is None:
        return None
    return PlayerProjection(
        source="tank01",
        pts_ppr=pts_ppr,
        pts_half_ppr=pts_half,
        pts_std=pts_std,
        position="DEF",
    )


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
