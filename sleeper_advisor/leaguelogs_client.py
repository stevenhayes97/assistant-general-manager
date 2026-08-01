"""LeagueLogs free API — market values + status blurbs (no key).

Docs: https://leaguelogs.com/developers
Base: https://developer.leaguelogs.com/v1

Skill positions only (QB/RB/WR/TE). Attribution is required wherever this
data is shown — every response includes ``_attribution``.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from .projections import ScoringFormat

BASE_URL = "https://developer.leaguelogs.com/v1"
CACHE_DIR = Path("/tmp/sleeper_advisor_cache")
MARKET_CACHE_TTL_SECONDS = 6 * 60 * 60

# Profiles published by LeagueLogs as of 2026 (all are 12-team).
_KNOWN_PROFILES = (
    "redraft-1qb-12t-ppr1",
    "redraft-1qb-12t-ppr0_5",
    "redraft-2qb-12t-ppr1",
    "dynasty-1qb-12t-ppr1",
    "dynasty-2qb-12t-ppr1",
)

_SKILL_POSITIONS = frozenset({"QB", "RB", "WR", "TE"})


@dataclass(frozen=True)
class MarketValue:
    sleeper_player_id: str
    value: float
    raw_value: int | None
    overall_rank: int | None
    position_rank: int | None
    profile_key: str


@dataclass(frozen=True)
class StatusBlurb:
    sleeper_player_id: str
    text: str
    signals: list[str]
    generated_at: str | None


@dataclass(frozen=True)
class Attribution:
    text: str
    url: str


class LeagueLogsClient:
    def __init__(
        self,
        session: requests.Session | None = None,
        timeout: int = 20,
        max_blurb_workers: int = 8,
    ):
        self.session = session or requests.Session()
        self.timeout = timeout
        self.max_blurb_workers = max_blurb_workers

    def _get(self, path: str, *, allow_404: bool = False) -> Any | None:
        resp = self.session.get(
            f"{BASE_URL}{path}",
            headers={"Accept": "application/json"},
            timeout=self.timeout,
        )
        if allow_404 and resp.status_code == 404:
            return None
        resp.raise_for_status()
        return resp.json()

    @staticmethod
    def select_profile(
        league: dict[str, Any],
        scoring: ScoringFormat,
    ) -> str:
        """Pick the closest published Market Index profile for this league.

        LeagueLogs only publishes 12-team profiles; ``total_rosters`` is ignored
        for matching. Keeper leagues map to redraft profiles.
        """
        settings = league.get("settings") or {}
        # Sleeper: 0=redraft, 1=keeper, 2=dynasty
        is_dynasty = int(settings.get("type") or 0) == 2
        roster_positions = [str(p).upper() for p in (league.get("roster_positions") or [])]
        is_2qb = (
            "SUPER_FLEX" in roster_positions
            or roster_positions.count("QB") >= 2
        )
        if scoring == "half_ppr":
            ppr = 0.5
        elif scoring == "std":
            ppr = 0.0
        else:
            ppr = 1.0

        format_name = "dynasty" if is_dynasty else "redraft"
        num_qbs = 2 if is_2qb else 1

        def score(key: str) -> tuple:
            # key: {format}-{N}qb-12t-ppr{X}
            parts = key.split("-")
            fmt = parts[0]
            qbs = 2 if parts[1].startswith("2") else 1
            ppr_token = parts[-1].removeprefix("ppr").replace("_", ".")
            try:
                key_ppr = float(ppr_token)
            except ValueError:
                key_ppr = 1.0
            return (
                0 if fmt == format_name else 1,
                0 if qbs == num_qbs else 1,
                abs(key_ppr - ppr),
                key,
            )

        return min(_KNOWN_PROFILES, key=score)

    def get_market_values(
        self,
        profile_key: str,
        *,
        force_refresh: bool = False,
    ) -> tuple[dict[str, MarketValue], Attribution | None]:
        """Return market values keyed by Sleeper player_id for one profile."""
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"leaguelogs_market_{profile_key}.json"

        payload: dict[str, Any] | None = None
        if not force_refresh and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < MARKET_CACHE_TTL_SECONDS:
                payload = json.loads(cache_file.read_text())

        if payload is None:
            data = self._get(f"/market/{profile_key}")
            if not isinstance(data, dict):
                return {}, None
            cache_file.write_text(json.dumps(data))
            payload = data

        attribution = _parse_attribution(payload.get("_attribution"))
        out: dict[str, MarketValue] = {}
        for row in payload.get("data") or []:
            if not isinstance(row, dict):
                continue
            pid = row.get("sleeperPlayerId")
            if not pid:
                continue
            value = _as_float(row.get("value"))
            if value is None:
                continue
            out[str(pid)] = MarketValue(
                sleeper_player_id=str(pid),
                value=value,
                raw_value=_as_int(row.get("rawValue")),
                overall_rank=_as_int(row.get("overallRank")),
                position_rank=_as_int(row.get("positionRank")),
                profile_key=profile_key,
            )
        return out, attribution

    def get_blurb(self, sleeper_player_id: str) -> StatusBlurb | None:
        data = self._get(f"/players/{sleeper_player_id}/blurb", allow_404=True)
        if not isinstance(data, dict):
            return None
        text = data.get("blurb")
        if not text:
            return None
        signals = data.get("signals") or []
        if not isinstance(signals, list):
            signals = []
        return StatusBlurb(
            sleeper_player_id=str(data.get("sleeperPlayerId") or sleeper_player_id),
            text=str(text),
            signals=[str(s) for s in signals],
            generated_at=data.get("generatedAt"),
        )

    def get_blurbs(
        self, sleeper_player_ids: list[str]
    ) -> dict[str, StatusBlurb]:
        """Fetch status blurbs for many players (parallel, 404 → omit)."""
        ids = [str(pid) for pid in sleeper_player_ids]
        if not ids:
            return {}
        out: dict[str, StatusBlurb] = {}
        workers = min(self.max_blurb_workers, len(ids))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self.get_blurb, pid): pid for pid in ids}
            for fut in as_completed(futures):
                blurb = fut.result()
                if blurb:
                    out[blurb.sleeper_player_id] = blurb
        return out


def skill_position_ids(
    player_ids: list[str],
    sleeper_players: dict[str, dict],
) -> list[str]:
    """Filter roster ids to LeagueLogs skill positions (QB/RB/WR/TE)."""
    out: list[str] = []
    for pid in player_ids:
        pos = (sleeper_players.get(pid) or {}).get("position") or ""
        if str(pos).upper() in _SKILL_POSITIONS:
            out.append(pid)
    return out


def _parse_attribution(raw: Any) -> Attribution | None:
    if not isinstance(raw, dict):
        return None
    text = raw.get("text")
    url = raw.get("url")
    if not text or not url:
        return None
    return Attribution(text=str(text), url=str(url))


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
