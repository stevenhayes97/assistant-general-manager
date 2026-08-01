"""Orchestrates all data sources into one structured context bundle.

This module intentionally produces *data*, not *advice*. The final
start/sit recommendation, weighting of "trends," and synthesis of injury
nuance / expert opinion is left to the calling agent (see
.cursor/agents/lineup-advisor.md), which pairs this structured context with
live web search.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .config import AdvisorConfig
from .odds_client import GameOdds, OddsClient
from .fantasypros_client import FantasyProsClient
from .leaguelogs_client import LeagueLogsClient, skill_position_ids
from .projections import (
    PlayerProjection,
    ScoringFormat,
    SleeperProjectionsClient,
    aggregate_projections,
    describe_reception_bonuses,
    detect_scoring_format,
)
from .tank01_client import Tank01Client
from .schedule_client import GameInfo, ScheduleClient
from .sleeper_client import SleeperClient
from .stadiums import Stadium, resolve_game_stadium
from .weather_client import WeatherClient, WeatherForecast

# Rough thresholds for flagging a lopsided matchup ("weak opponent, could
# take their foot off the gas" scenario). Tune freely -- these are starting
# points, not gospel.
BLOWOUT_SPREAD_THRESHOLD = 9.5
LOW_COMPETITIVENESS_TOTAL_CEILING = 41.0  # low total + big spread = likely to stay run-heavy/clock-controlled


@dataclass
class PlayerContext:
    player_id: str
    name: str
    position: str
    nfl_team: str | None
    roster_slot: str  # "starter" or "bench" per current Sleeper lineup
    injury_status: str | None
    injury_body_part: str | None
    injury_notes: str | None
    opponent: str | None
    home_away: str | None
    kickoff_utc: str | None
    venue_name: str | None
    venue_roof: str | None
    weather: dict | None
    vegas_spread: float | None
    vegas_favorite: str | None
    vegas_total: float | None
    implied_team_total: float | None
    game_script_flag: str | None
    game_script_note: str | None
    # Mean of available sources after scoring-bucket + reception-bonus adjust.
    projected_points: float | None
    projection_source: str | None  # e.g. "rotowire", "fantasypros+rotowire+tank01"
    projections_by_source: dict[str, float] = field(default_factory=dict)
    # LeagueLogs Market Index + status blurb (LLM reasoning aids; not projections).
    market_value: float | None = None
    market_overall_rank: int | None = None
    market_position_rank: int | None = None
    status_blurb: str | None = None
    status_blurb_signals: list[str] = field(default_factory=list)
    status_blurb_at: str | None = None


@dataclass
class AdvisorContext:
    generated_at_utc: str
    league_id: str
    league_name: str | None
    roster_id: int
    week: int
    season: int
    season_type: str
    scoring_format: ScoringFormat
    # e.g. ["TE +0.25/rec"] when league has reception premiums applied to projs.
    reception_bonuses: list[str] = field(default_factory=list)
    odds_available: bool = False
    projections_available: bool = False
    # Which projection feeds contributed at least one player this run.
    projection_sources_available: list[str] = field(default_factory=list)
    # Season type actually used for the Sleeper/RotoWire fetch (may fall back
    # to "regular" when NFL state is still "pre" but weekly pts already exist).
    projection_season_type: str | None = None
    leaguelogs_available: bool = False
    leaguelogs_profile: str | None = None
    leaguelogs_attribution: dict | None = None  # {text, url} — required by ToS
    players: list[PlayerContext] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def classify_game_script(
    team_abbr: str, odds: GameOdds | None
) -> tuple[str | None, str | None]:
    """Pure, unit-testable classification of blowout / garbage-time risk."""
    if odds is None or odds.spread is None:
        return None, None

    is_favorite = odds.favorite == team_abbr
    lopsided = odds.spread >= BLOWOUT_SPREAD_THRESHOLD
    low_total = odds.total is not None and odds.total <= LOW_COMPETITIVENESS_TOTAL_CEILING

    if lopsided and is_favorite:
        note = (
            f"Big favorite (spread {odds.spread}). Real risk this team builds an "
            "early lead and eases off the gas -- starters (esp. lead RB) could see "
            "the 4th quarter go to backups, capping upside for volume-dependent players."
        )
        return "blowout_risk_favorite", note

    if lopsided and not is_favorite:
        note = (
            f"Big underdog (spread {odds.spread}). Likely to fall behind and be forced "
            "to abandon the run -- can boost pass-catcher volume (WR/TE, pass-catching RB) "
            "but hurts a between-the-tackles RB1's floor."
        )
        return "blowout_risk_underdog", note

    if low_total:
        note = f"Low implied total ({odds.total}) -- expect a grind-it-out, lower-scoring game."
        return "low_total", note

    return "competitive", "Projected as a competitive, roughly even game script."


def build_context(config: AdvisorConfig) -> AdvisorContext:
    sleeper = SleeperClient()
    schedule = ScheduleClient()
    weather_client = WeatherClient()

    league_id = config.require_league_id()
    league = sleeper.get_league(league_id)
    state = sleeper.get_nfl_state()

    week = config.week or state["week"] or state.get("display_week") or 1
    season = int(state["season"])
    season_type = state.get("season_type") or "regular"
    scoring_settings = league.get("scoring_settings") or {}
    scoring_format = detect_scoring_format(scoring_settings)
    reception_bonuses = describe_reception_bonuses(scoring_settings)

    roster_id = config.roster_id
    if roster_id is None and config.username:
        roster_id = sleeper.resolve_roster_id(league_id, config.username)
    if roster_id is None:
        raise ValueError(
            "Provide SLEEPER_ROSTER_ID or SLEEPER_USERNAME so we know which roster is yours."
        )

    rosters = sleeper.get_rosters(league_id)
    roster = next((r for r in rosters if r["roster_id"] == roster_id), None)
    if roster is None:
        raise ValueError(f"Roster {roster_id} not found in league {league_id}")

    all_players = sleeper.get_all_players()
    week_games = schedule.get_week_games(week, season)

    odds_by_team: dict[str, GameOdds] = {}
    odds_available = False
    if config.odds_api_key:
        try:
            odds_by_team = OddsClient(config.odds_api_key).get_week_odds()
            odds_available = True
        except Exception:
            odds_available = False  # degrade gracefully; agent can note odds were unavailable

    starters = set(roster.get("starters") or [])
    player_ids = roster.get("players") or []

    rotowire_by_id: dict[str, PlayerProjection] = {}
    projection_season_type: str | None = None
    try:
        week_projections = SleeperProjectionsClient().get_week_projections(
            season=season,
            week=int(week),
            season_type=season_type,
        )
        rotowire_by_id = week_projections.by_player_id
        if rotowire_by_id:
            projection_season_type = week_projections.season_type
    except Exception:
        rotowire_by_id = {}
        projection_season_type = None

    fantasypros_by_id: dict[str, PlayerProjection] = {}
    if config.fantasypros_api_key:
        try:
            fantasypros_by_id = FantasyProsClient(
                config.fantasypros_api_key
            ).get_projections_by_sleeper_id(
                sleeper_players=all_players,
                season=season,
                week=int(week),
                scoring=scoring_format,
                season_type=season_type,
                roster_player_ids=player_ids,
            )
        except Exception:
            fantasypros_by_id = {}

    tank01_by_id: dict[str, PlayerProjection] = {}
    if config.tank01_api_key:
        try:
            tank01_by_id = Tank01Client(
                config.tank01_api_key
            ).get_projections_by_sleeper_id(
                sleeper_players=all_players,
                week=int(week),
                scoring=scoring_format,
                roster_player_ids=player_ids,
            )
        except Exception:
            tank01_by_id = {}

    sources_available = sorted(
        {
            *(["rotowire"] if rotowire_by_id else []),
            *(["fantasypros"] if fantasypros_by_id else []),
            *(["tank01"] if tank01_by_id else []),
        }
    )
    projections_available = bool(sources_available)

    leaguelogs_available = False
    leaguelogs_profile: str | None = None
    leaguelogs_attribution: dict | None = None
    market_by_id: dict = {}
    blurbs_by_id: dict = {}
    try:
        ll = LeagueLogsClient()
        leaguelogs_profile = LeagueLogsClient.select_profile(league, scoring_format)
        market_by_id, attribution = ll.get_market_values(leaguelogs_profile)
        if attribution:
            leaguelogs_attribution = {
                "text": attribution.text,
                "url": attribution.url,
            }
        skill_ids = skill_position_ids(player_ids, all_players)
        blurbs_by_id = ll.get_blurbs(skill_ids)
        leaguelogs_available = bool(market_by_id or blurbs_by_id)
    except Exception:
        leaguelogs_available = False
        leaguelogs_profile = None
        leaguelogs_attribution = None
        market_by_id = {}
        blurbs_by_id = {}

    players_ctx: list[PlayerContext] = []
    for pid in player_ids:
        p = all_players.get(pid)
        if not p:
            continue

        nfl_team = p.get("team")
        game: GameInfo | None = week_games.get(nfl_team) if nfl_team else None
        odds = odds_by_team.get(nfl_team) if nfl_team else None
        source_projs = [
            proj
            for proj in (
                rotowire_by_id.get(pid),
                fantasypros_by_id.get(pid),
                tank01_by_id.get(pid),
            )
            if proj is not None
        ]
        aggregated = aggregate_projections(
            source_projs, scoring_format, scoring_settings
        )

        stadium: Stadium | None = None
        weather_dict = None
        if game and nfl_team:
            stadium = resolve_game_stadium(nfl_team, game.home_away, game.opponent)
            weather_dict = _weather_for_stadium(
                weather_client, stadium, game.kickoff_utc
            )

        script_flag, script_note = (
            classify_game_script(nfl_team, odds) if nfl_team else (None, None)
        )

        market = market_by_id.get(pid)
        blurb = blurbs_by_id.get(pid)

        players_ctx.append(
            PlayerContext(
                player_id=pid,
                name=p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip(),
                position=p.get("position") or "UNK",
                nfl_team=nfl_team,
                roster_slot="starter" if pid in starters else "bench",
                injury_status=p.get("injury_status"),
                injury_body_part=p.get("injury_body_part"),
                injury_notes=p.get("injury_notes"),
                opponent=game.opponent if game else None,
                home_away=game.home_away if game else None,
                kickoff_utc=game.kickoff_utc if game else None,
                venue_name=(game.venue_name if game else None) or (stadium.name if stadium else None),
                venue_roof=stadium.roof if stadium else None,
                weather=weather_dict,
                vegas_spread=odds.spread if odds else None,
                vegas_favorite=odds.favorite if odds else None,
                vegas_total=odds.total if odds else None,
                implied_team_total=(odds.team_implied_total.get(nfl_team) if odds and nfl_team else None),
                game_script_flag=script_flag,
                game_script_note=script_note,
                projected_points=aggregated.mean if aggregated else None,
                projection_source=aggregated.source_label if aggregated else None,
                projections_by_source=dict(aggregated.by_source) if aggregated else {},
                market_value=market.value if market else None,
                market_overall_rank=market.overall_rank if market else None,
                market_position_rank=market.position_rank if market else None,
                status_blurb=blurb.text if blurb else None,
                status_blurb_signals=list(blurb.signals) if blurb else [],
                status_blurb_at=blurb.generated_at if blurb else None,
            )
        )

    # Bench-first-then-starters, then by position, reads nicely for a human/agent.
    players_ctx.sort(key=lambda pc: (pc.roster_slot != "bench", pc.position, pc.name))

    return AdvisorContext(
        generated_at_utc=datetime.now(timezone.utc).isoformat(),
        league_id=league_id,
        league_name=league.get("name"),
        roster_id=roster_id,
        week=week,
        season=season,
        season_type=season_type,
        scoring_format=scoring_format,
        reception_bonuses=reception_bonuses,
        odds_available=odds_available,
        projections_available=projections_available,
        projection_sources_available=sources_available,
        projection_season_type=projection_season_type,
        leaguelogs_available=leaguelogs_available,
        leaguelogs_profile=leaguelogs_profile,
        leaguelogs_attribution=leaguelogs_attribution,
        players=players_ctx,
    )


def _weather_for_stadium(
    weather_client: WeatherClient,
    stadium: Stadium | None,
    kickoff_utc: str | None,
) -> dict | None:
    """Apply roof-aware weather rules.

    - dome: skip forecast entirely; label weather a non-factor.
    - outdoor: fetch Open-Meteo forecast at kickoff.
    - retractable: fetch forecast but flag that the roof is usually closed
      and open/closed status is uncertain (do not guess).
    """
    if stadium is None:
        return None

    if stadium.is_dome:
        return {
            "roof": stadium.roof,
            "note": "Indoor/dome -- weather is a non-factor. No forecast fetched.",
        }

    forecast = weather_client.forecast_for_kickoff(
        stadium.lat, stadium.lon, kickoff_utc
    )
    weather_dict = _weather_to_dict(forecast) if forecast else {
        "roof": stadium.roof,
        "temperature_f": None,
        "wind_mph": None,
        "precipitation_probability_pct": None,
        "note": "Forecast unavailable (kickoff outside forecast window or lookup failed).",
    }
    weather_dict["roof"] = stadium.roof

    if stadium.roof == "retractable":
        uncertainty = (
            "Retractable roof -- usually closed; open/closed status is uncertain, "
            "so treat outdoor conditions as a contingency rather than a given."
        )
        existing = weather_dict.get("note")
        weather_dict["note"] = f"{existing} {uncertainty}".strip() if existing else uncertainty

    return weather_dict


def _weather_to_dict(forecast: WeatherForecast) -> dict:
    return {
        "temperature_f": forecast.temperature_f,
        "wind_mph": forecast.wind_mph,
        "precipitation_probability_pct": forecast.precipitation_probability_pct,
        "note": forecast.condition_note,
    }
