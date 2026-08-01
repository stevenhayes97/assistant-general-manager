"""Weekly fantasy point projections via Sleeper's undocumented projections API.

Sleeper surfaces RotoWire projections at (no auth, free):

    GET https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=...

This is **not** listed in https://docs.sleeper.com/. Community clients rely on
it; treat it as best-effort and degrade gracefully if it disappears or fails.

Returned point totals are RotoWire's standard / half-PPR / PPR buckets — not
recomputed against a league's custom scoring modifiers (e.g. TE premium).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import requests

BASE_URL = "https://api.sleeper.app/projections/nfl"

ScoringFormat = Literal["ppr", "half_ppr", "std"]

_SCORING_TO_STAT_KEY = {
    "ppr": "pts_ppr",
    "half_ppr": "pts_half_ppr",
    "std": "pts_std",
}


@dataclass(frozen=True)
class PlayerProjection:
    """One source's weekly projection for a player."""

    source: str
    pts_ppr: float | None
    pts_half_ppr: float | None
    pts_std: float | None

    def points_for(self, scoring: ScoringFormat) -> float | None:
        key = _SCORING_TO_STAT_KEY[scoring]
        return getattr(self, key)


def detect_scoring_format(scoring_settings: dict[str, Any] | None) -> ScoringFormat:
    """Map Sleeper league `scoring_settings.rec` to a standard bucket.

    Common values: 1.0 (PPR), 0.5 (half-PPR), 0 / missing (standard).
    Values between buckets snap to the nearer conventional format.
    """
    if scoring_settings is None:
        return "ppr"
    rec = scoring_settings.get("rec")
    if rec is None:
        return "std"
    try:
        rec_f = float(rec)
    except (TypeError, ValueError):
        return "ppr"
    if rec_f >= 0.75:
        return "ppr"
    if rec_f >= 0.25:
        return "half_ppr"
    return "std"


@dataclass(frozen=True)
class WeekProjections:
    """Projection map plus metadata about which Sleeper season_type was used."""

    by_player_id: dict[str, PlayerProjection]
    season_type: str


class SleeperProjectionsClient:
    """Fetch RotoWire weekly projections as exposed by Sleeper."""

    def __init__(self, session: requests.Session | None = None, timeout: int = 30):
        self.session = session or requests.Session()
        self.timeout = timeout

    def get_week_projections(
        self,
        season: int | str,
        week: int,
        season_type: str = "regular",
    ) -> WeekProjections:
        """Return projections keyed by Sleeper player_id.

        During the NFL preseason, Sleeper often serves ADP-only rows under
        `season_type=pre` while weekly point totals already exist under
        `regular`. If the preferred season_type yields no point totals, retry
        with `regular` before giving up.
        """
        preferred = season_type or "regular"
        candidates = [preferred]
        if preferred != "regular":
            candidates.append("regular")

        last_empty_type = preferred
        for stype in candidates:
            parsed = self._fetch_and_parse(season, week, stype)
            if parsed:
                return WeekProjections(by_player_id=parsed, season_type=stype)
            last_empty_type = stype
        return WeekProjections(by_player_id={}, season_type=last_empty_type)

    def _fetch_and_parse(
        self,
        season: int | str,
        week: int,
        season_type: str,
    ) -> dict[str, PlayerProjection]:
        resp = self.session.get(
            f"{BASE_URL}/{season}/{week}",
            params={"season_type": season_type},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list):
            return {}

        out: dict[str, PlayerProjection] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            player_id = row.get("player_id")
            if not player_id or player_id in out:
                continue
            stats = row.get("stats") or {}
            pts_ppr = _as_float(stats.get("pts_ppr"))
            pts_half = _as_float(stats.get("pts_half_ppr"))
            pts_std = _as_float(stats.get("pts_std"))
            if pts_ppr is None and pts_half is None and pts_std is None:
                continue
            source = (row.get("company") or "rotowire").lower()
            out[str(player_id)] = PlayerProjection(
                source=source,
                pts_ppr=pts_ppr,
                pts_half_ppr=pts_half,
                pts_std=pts_std,
            )
        return out


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
