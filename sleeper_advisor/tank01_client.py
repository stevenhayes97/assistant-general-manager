"""Tank01 NFL data via RapidAPI (requires TANK01_API_KEY).

Docs: https://rapidapi.com/tank01/api/tank01-nfl-live-in-game-real-time-statistics-nfl

Used for:
- Weekly projections (``/getNFLProjections``)
- Multi-book betting odds second opinion (``/getNFLBettingOdds``)
- Depth charts for LLM reasoning (``/getNFLDepthCharts``)

Join path to Sleeper: Tank01 ``getNFLPlayerList`` includes ``sleeperBotID``
(and ``espnID`` as fallback). Player list / depth charts are cached on disk.
"""

from __future__ import annotations

import json
import statistics
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

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
DEPTH_CACHE_TTL_SECONDS = 6 * 60 * 60
ODDS_CACHE_TTL_SECONDS = 30 * 60

# Flag when books' spread/total ranges are wide enough to mention.
SPREAD_DISAGREE_THRESHOLD = 1.5
TOTAL_DISAGREE_THRESHOLD = 3.0

# Metadata keys on a game-odds row (everything else that is a dict is a book).
_ODDS_META_KEYS = frozenset(
    {
        "awayTeam",
        "homeTeam",
        "gameDate",
        "gameID",
        "teamIDAway",
        "teamIDHome",
        "last_updated_e_time",
        "espn_id",
        "odds",
    }
)

_TEAM_ABBR_ALIASES = {
    "WSH": "WAS",
    "JAC": "JAX",
}


@dataclass(frozen=True)
class Tank01BookConsensus:
    """Multi-book consensus for one game, keyed onto both teams."""

    favorite: str | None
    spread: float | None  # positive magnitude
    total: float | None
    spread_min: float | None
    spread_max: float | None
    total_min: float | None
    total_max: float | None
    books_count: int
    spread_disagreement: bool
    total_disagreement: bool
    team_implied_total: dict[str, float] = field(default_factory=dict)
    note: str | None = None


@dataclass(frozen=True)
class DepthChartSpot:
    """Where a player sits on Tank01's depth chart for their position group."""

    team: str
    position_group: str
    depth_order: int
    role_label: str  # e.g. "WR2"
    starter_name: str | None
    chart_line: str  # e.g. "WR: 1. Amon-Ra St. Brown, 2. Jameson Williams, ..."


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
        self.last_week_projection_rows: int = 0

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

    def get_week_games(
        self,
        week: int,
        season: int | str,
        *,
        season_type: str = "reg",
    ) -> list[dict]:
        """Schedule rows for one NFL week (``getNFLGamesForWeek``)."""
        data = self._get(
            "/getNFLGamesForWeek",
            params={
                "week": int(week),
                "season": str(season),
                "seasonType": season_type,
            },
        )
        body = data.get("body") if isinstance(data, dict) else None
        if not isinstance(body, list):
            return []
        return [row for row in body if isinstance(row, dict)]

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
            self.last_week_projection_rows = 0
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
        self.last_week_projection_rows = len(out)
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

    def get_odds_for_dates(
        self,
        game_dates: Iterable[str],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Tank01BookConsensus]:
        """Fetch multi-book odds for YYYYMMDD dates; return {team_abbr: consensus}.

        One RapidAPI call per distinct date (30-minute disk cache). Consensus is
        the median spread magnitude / total across sportsbooks; wide ranges set
        disagreement flags for the LLM.
        """
        result: dict[str, Tank01BookConsensus] = {}
        for date in sorted({d for d in game_dates if d}):
            for team, consensus in self._odds_for_date(
                date, force_refresh=force_refresh
            ).items():
                result[team] = consensus
        return result

    def get_odds_for_kickoffs(
        self,
        kickoffs_utc: Iterable[str | None],
        *,
        force_refresh: bool = False,
    ) -> dict[str, Tank01BookConsensus]:
        """Convenience: derive gameDate keys from ISO kickoff timestamps."""
        dates = []
        for ko in kickoffs_utc:
            d = _kickoff_to_gamedate(ko)
            if d:
                dates.append(d)
        return self.get_odds_for_dates(dates, force_refresh=force_refresh)

    def _odds_for_date(
        self, game_date: str, *, force_refresh: bool = False
    ) -> dict[str, Tank01BookConsensus]:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / f"tank01_odds_{game_date}.json"
        payload: dict[str, Any] | None = None
        if not force_refresh and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < ODDS_CACHE_TTL_SECONDS:
                payload = json.loads(cache_file.read_text())

        if payload is None:
            data = self._get(
                "/getNFLBettingOdds",
                params={
                    "gameDate": game_date,
                    "impliedTotals": "true",
                    "itemFormat": "map",
                },
            )
            if not isinstance(data, dict):
                return {}
            # Empty body + error is normal when no slate that day.
            if data.get("error") and not data.get("body"):
                return {}
            cache_file.write_text(json.dumps(data))
            payload = data

        body = payload.get("body") if isinstance(payload, dict) else None
        if not isinstance(body, dict):
            return {}

        out: dict[str, Tank01BookConsensus] = {}
        for _game_id, row in body.items():
            if not isinstance(row, dict):
                continue
            consensus = _parse_game_odds_consensus(row)
            if consensus is None:
                continue
            home = _norm_team(row.get("homeTeam"))
            away = _norm_team(row.get("awayTeam"))
            if home:
                out[home] = consensus
            if away:
                out[away] = consensus
        return out

    def get_depth_charts(
        self, force_refresh: bool = False
    ) -> dict[str, dict[str, list[dict[str, str]]]]:
        """Return {teamAbv: {position: [{playerID, longName, depthPosition}, ...]}}.

        Cached 6h. Position keys are whatever Tank01 publishes (QB/RB/WR/TE/…).
        """
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file = CACHE_DIR / "tank01_depth_charts.json"
        if not force_refresh and cache_file.exists():
            age = time.time() - cache_file.stat().st_mtime
            if age < DEPTH_CACHE_TTL_SECONDS:
                return json.loads(cache_file.read_text())

        data = self._get("/getNFLDepthCharts")
        body = data.get("body") if isinstance(data, dict) else data
        out: dict[str, dict[str, list[dict[str, str]]]] = {}
        rows = body if isinstance(body, list) else []
        if isinstance(body, dict):
            rows = list(body.values())
        for row in rows:
            if not isinstance(row, dict):
                continue
            team = _norm_team(row.get("teamAbv") or row.get("team"))
            chart = row.get("depthChart") or {}
            if not team or not isinstance(chart, dict):
                continue
            parsed: dict[str, list[dict[str, str]]] = {}
            for pos, entries in chart.items():
                if not isinstance(entries, list):
                    continue
                cleaned: list[dict[str, str]] = []
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    pid = entry.get("playerID")
                    if not pid:
                        continue
                    cleaned.append(
                        {
                            "playerID": str(pid),
                            "longName": str(entry.get("longName") or ""),
                            "depthPosition": str(entry.get("depthPosition") or ""),
                        }
                    )
                cleaned.sort(key=lambda e: _depth_sort_key(e.get("depthPosition")))
                if cleaned:
                    parsed[str(pos).upper()] = cleaned
            if parsed:
                out[team] = parsed

        cache_file.write_text(json.dumps(out))
        return out

    def get_depth_spots_by_sleeper_id(
        self,
        sleeper_players: dict[str, dict],
        roster_player_ids: list[str] | None = None,
        *,
        force_refresh: bool = False,
    ) -> dict[str, DepthChartSpot]:
        """Map roster Sleeper ids → depth-chart spots (skill positions)."""
        charts = self.get_depth_charts(force_refresh=force_refresh)
        if not charts:
            return {}
        sleeper_to_tank, espn_to_tank = self.get_id_maps(force_refresh=force_refresh)

        target_ids = roster_player_ids or list(sleeper_players)
        out: dict[str, DepthChartSpot] = {}
        for sleeper_id in target_ids:
            player = sleeper_players.get(sleeper_id) or {}
            team = _norm_team(player.get("team"))
            pos = (player.get("position") or "").upper()
            if not team or pos not in {"QB", "RB", "WR", "TE"}:
                continue
            team_chart = charts.get(team) or {}
            group_key, entries = _position_group_entries(team_chart, pos)
            if not entries:
                continue

            tank_id = sleeper_to_tank.get(str(sleeper_id))
            if not tank_id:
                espn_id = player.get("espn_id")
                if espn_id is not None:
                    tank_id = espn_to_tank.get(str(espn_id))

            depth_order = None
            if tank_id:
                for entry in entries:
                    if entry["playerID"] == tank_id:
                        depth_order = _as_int(entry.get("depthPosition"))
                        break
            if depth_order is None:
                # Name fallback within the position group.
                full_name = (player.get("full_name") or "").strip().lower()
                if full_name:
                    for entry in entries:
                        if (entry.get("longName") or "").strip().lower() == full_name:
                            depth_order = _as_int(entry.get("depthPosition"))
                            break
            if depth_order is None:
                continue

            starter_name = None
            if depth_order != 1 and entries:
                starter_name = entries[0].get("longName") or None
            chart_line = f"{group_key}: " + ", ".join(
                f"{e.get('depthPosition') or '?'}. {e.get('longName') or e['playerID']}"
                for e in entries[:6]
            )
            out[sleeper_id] = DepthChartSpot(
                team=team,
                position_group=group_key,
                depth_order=depth_order,
                role_label=f"{group_key}{depth_order}",
                starter_name=starter_name,
                chart_line=chart_line,
            )
        return out


def _parse_game_odds_consensus(row: dict[str, Any]) -> Tank01BookConsensus | None:
    home = _norm_team(row.get("homeTeam"))
    away = _norm_team(row.get("awayTeam"))
    if not home or not away:
        return None

    spreads: list[float] = []
    totals: list[float] = []
    favorites: list[str] = []
    implied_home: list[float] = []
    implied_away: list[float] = []

    for key, book in row.items():
        if key in _ODDS_META_KEYS or not isinstance(book, dict):
            continue
        if "homeTeamSpread" not in book and "awayTeamSpread" not in book:
            continue
        home_spread = _as_float(book.get("homeTeamSpread"))
        away_spread = _as_float(book.get("awayTeamSpread"))
        if home_spread is None and away_spread is not None:
            home_spread = -away_spread
        if away_spread is None and home_spread is not None:
            away_spread = -home_spread
        if home_spread is None:
            continue

        if home_spread < 0:
            favorites.append(home)
            spreads.append(abs(home_spread))
        elif away_spread is not None and away_spread < 0:
            favorites.append(away)
            spreads.append(abs(away_spread))
        else:
            # Pick-em / missing sign — skip favorite, still keep total.
            pass

        total = _as_float(book.get("totalOver"))
        if total is None:
            total = _as_float(book.get("totalUnder"))
        if total is not None:
            totals.append(total)

        # impliedTotals=true may add home/away totals on each book.
        ih = _as_float(book.get("homeTotal")) or _as_float(book.get("homeImpliedTotal"))
        ia = _as_float(book.get("awayTotal")) or _as_float(book.get("awayImpliedTotal"))
        nested = book.get("implied_totals") or book.get("impliedTotals")
        if isinstance(nested, dict):
            ih = ih or _as_float(nested.get("homeTotal"))
            ia = ia or _as_float(nested.get("awayTotal"))
        if ih is not None:
            implied_home.append(ih)
        if ia is not None:
            implied_away.append(ia)

    if not spreads and not totals:
        return None

    consensus_spread = _median(spreads)
    consensus_total = _median(totals)
    spread_min = min(spreads) if spreads else None
    spread_max = max(spreads) if spreads else None
    total_min = min(totals) if totals else None
    total_max = max(totals) if totals else None
    spread_disagree = (
        spread_min is not None
        and spread_max is not None
        and (spread_max - spread_min) >= SPREAD_DISAGREE_THRESHOLD
    )
    total_disagree = (
        total_min is not None
        and total_max is not None
        and (total_max - total_min) >= TOTAL_DISAGREE_THRESHOLD
    )

    favorite = None
    if favorites:
        # Majority vote among books.
        favorite = max(set(favorites), key=favorites.count)

    implied: dict[str, float] = {}
    if implied_home and implied_away:
        implied[home] = round(statistics.median(implied_home), 1)
        implied[away] = round(statistics.median(implied_away), 1)
    elif (
        favorite is not None
        and consensus_spread is not None
        and consensus_total is not None
    ):
        implied[favorite] = round(consensus_total / 2 + consensus_spread / 2, 1)
        dog = away if favorite == home else home
        implied[dog] = round(consensus_total / 2 - consensus_spread / 2, 1)

    note_parts = []
    if spread_disagree and spread_min is not None and spread_max is not None:
        note_parts.append(
            f"books disagree on spread ({spread_min:g}–{spread_max:g} across {len(spreads)} books)"
        )
    if total_disagree and total_min is not None and total_max is not None:
        note_parts.append(
            f"books disagree on total ({total_min:g}–{total_max:g} across {len(totals)} books)"
        )
    if consensus_spread is not None and consensus_total is not None and favorite:
        note_parts.append(
            f"Tank01 multi-book median: {favorite} -{consensus_spread:g}, "
            f"O/U {consensus_total:g} ({max(len(spreads), len(totals))} books)"
        )

    return Tank01BookConsensus(
        favorite=favorite,
        spread=consensus_spread,
        total=consensus_total,
        spread_min=spread_min,
        spread_max=spread_max,
        total_min=total_min,
        total_max=total_max,
        books_count=max(len(spreads), len(totals)),
        spread_disagreement=spread_disagree,
        total_disagreement=total_disagree,
        team_implied_total=implied,
        note="; ".join(note_parts) if note_parts else None,
    )


def _position_group_entries(
    team_chart: dict[str, list[dict[str, str]]], position: str
) -> tuple[str, list[dict[str, str]]]:
    """Pick the depth-chart list for a fantasy position."""
    if position in team_chart:
        return position, team_chart[position]
    # Some feeds split WRs; merge LWR/RWR/SWR/WR.
    if position == "WR":
        merged: list[dict[str, str]] = []
        for key in ("WR", "LWR", "RWR", "SWR", "WR1", "WR2", "WR3"):
            merged.extend(team_chart.get(key) or [])
        if merged:
            # Re-number by listed depth then name for stable order.
            merged.sort(key=lambda e: (_depth_sort_key(e.get("depthPosition")), e.get("longName") or ""))
            return "WR", merged
    return position, []


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


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    return float(statistics.median(values))


def _norm_team(value: Any) -> str | None:
    if value is None:
        return None
    abbr = str(value).strip().upper()
    if not abbr:
        return None
    return _TEAM_ABBR_ALIASES.get(abbr, abbr)


def _kickoff_to_gamedate(kickoff_utc: str | None) -> str | None:
    """Convert ISO kickoff (e.g. 2026-09-10T00:20Z) → YYYYMMDD for Tank01."""
    if not kickoff_utc:
        return None
    try:
        # Handle trailing Z
        text = kickoff_utc.replace("Z", "+00:00")
        dt = datetime.fromisoformat(text)
        return dt.strftime("%Y%m%d")
    except ValueError:
        digits = "".join(c for c in kickoff_utc if c.isdigit())
        return digits[:8] if len(digits) >= 8 else None


def _depth_sort_key(value: Any) -> int:
    n = _as_int(value)
    return n if n is not None else 99
