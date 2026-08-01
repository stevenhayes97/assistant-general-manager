"""Weekly fantasy point projections via Sleeper's undocumented projections API.

Sleeper surfaces RotoWire projections at (no auth, free):

    GET https://api.sleeper.app/projections/nfl/{season}/{week}?season_type=...

This is **not** listed in https://docs.sleeper.com/. Community clients rely on
it; treat it as best-effort and degrade gracefully if it disappears or fails.

RotoWire's `pts_ppr` / `pts_half_ppr` / `pts_std` are standard scoring buckets.
League-specific reception bonuses (e.g. TE premium via `bonus_rec_te`) are
applied afterward from the league's `scoring_settings` and the projection's
counting stats (`bonus_rec_te` ≈ projected TE receptions).
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

# Sleeper scoring keys whose projected counting stats are added on top of the
# standard PPR/half/std bucket (points-per-reception position premiums).
_RECEPTION_BONUS_KEYS = ("bonus_rec_te", "bonus_rec_wr", "bonus_rec_rb")


@dataclass(frozen=True)
class PlayerProjection:
    """One source's weekly projection for a player."""

    source: str
    pts_ppr: float | None
    pts_half_ppr: float | None
    pts_std: float | None
    # Counting stats used to apply league reception bonuses (TE premium, etc.).
    rec: float | None = None
    bonus_rec_te: float | None = None
    bonus_rec_wr: float | None = None
    bonus_rec_rb: float | None = None
    position: str | None = None

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


def league_adjusted_points(
    proj: PlayerProjection,
    scoring: ScoringFormat,
    scoring_settings: dict[str, Any] | None,
) -> float | None:
    """Base RotoWire bucket points + league reception bonuses (TE premium, etc.).

    Example (full PPR + 0.25 TE premium): a TE projected for 5.0 receptions
    gets ``pts_ppr + 0.25 * bonus_rec_te`` (≈ 1.25 fantasy points per catch).
    Non-TE reception bonuses from ``scoring_settings`` are applied the same way.
    """
    base = proj.points_for(scoring)
    if base is None:
        return None

    adjustment = reception_bonus_adjustment(proj, scoring_settings)
    return round(base + adjustment, 2)


def reception_bonus_adjustment(
    proj: PlayerProjection,
    scoring_settings: dict[str, Any] | None,
) -> float:
    if not scoring_settings:
        return 0.0

    total = 0.0
    for key in _RECEPTION_BONUS_KEYS:
        rate = _as_float(scoring_settings.get(key))
        if rate is None or rate == 0:
            continue
        count = _bonus_count_for(proj, key)
        if count is not None:
            total += rate * count
    return total


def describe_reception_bonuses(scoring_settings: dict[str, Any] | None) -> list[str]:
    """Human-readable reception bonus rules present in league settings."""
    if not scoring_settings:
        return []
    labels = {
        "bonus_rec_te": "TE",
        "bonus_rec_wr": "WR",
        "bonus_rec_rb": "RB",
    }
    notes: list[str] = []
    for key, label in labels.items():
        rate = _as_float(scoring_settings.get(key))
        if rate:
            notes.append(f"{label} +{rate:g}/rec")
    return notes


def _bonus_count_for(proj: PlayerProjection, key: str) -> float | None:
    count = getattr(proj, key)
    if count is not None:
        return count
    # Fallback: if Sleeper omitted bonus_rec_te but the player is a TE, use rec.
    if key == "bonus_rec_te" and (proj.position or "").upper() == "TE":
        return proj.rec
    return None


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
            player = row.get("player") or {}
            source = (row.get("company") or "rotowire").lower()
            out[str(player_id)] = PlayerProjection(
                source=source,
                pts_ppr=pts_ppr,
                pts_half_ppr=pts_half,
                pts_std=pts_std,
                rec=_as_float(stats.get("rec")),
                bonus_rec_te=_as_float(stats.get("bonus_rec_te")),
                bonus_rec_wr=_as_float(stats.get("bonus_rec_wr")),
                bonus_rec_rb=_as_float(stats.get("bonus_rec_rb")),
                position=player.get("position") or row.get("position"),
            )
        return out


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
