"""Render an AdvisorContext as human/agent-friendly Markdown."""

from __future__ import annotations

from .context_builder import AdvisorContext, PlayerContext


def to_markdown(ctx: AdvisorContext) -> str:
    lines = [
        f"# Lineup context -- {ctx.league_name or ctx.league_id}, Week {ctx.week} ({ctx.season})",
        f"_Generated {ctx.generated_at_utc}_",
        f"_Scoring format for projections: `{ctx.scoring_format}` "
        f"(NFL `{ctx.season_type}`"
        + (
            f", Sleeper/RotoWire season_type `{ctx.projection_season_type}`"
            if ctx.projection_season_type
            else ""
        )
        + ")_",
        "",
    ]
    if not ctx.odds_available:
        lines.append(
            "> No odds API key configured (or lookup failed) -- Vegas spread/total/game-script "
            "signals are unavailable below. Set ODDS_API_KEY to enable them."
        )
        lines.append("")
    if not ctx.projections_available:
        lines.append(
            "> Weekly projections unavailable "
            "(no RotoWire/FantasyPros/Tank01 totals returned)."
        )
        lines.append("")
    else:
        sources = ", ".join(ctx.projection_sources_available) or "unknown"
        bonus_note = ""
        if ctx.reception_bonuses:
            bonus_note = (
                " League reception bonuses applied: "
                + ", ".join(ctx.reception_bonuses)
                + "."
            )
        lines.append(
            f"> Projected points are the mean of available sources ({sources}), "
            "each scored with the league PPR/half-PPR/standard bucket."
            + bonus_note
            + " Per-source values are in the RW / FP / T01 columns."
        )
        lines.append("")
    if ctx.leaguelogs_available:
        profile = ctx.leaguelogs_profile or "unknown"
        lines.append(
            f"> LeagueLogs Market Index profile `{profile}` — value / OVR / POS "
            "ranks are trade/roster context for LLM reasoning, **not** weekly "
            "point projections. Status blurbs (when present) are short injury/"
            "transaction notes; still verify with live web research."
        )
        lines.append("")
        attr = ctx.leaguelogs_attribution or {}
        if attr.get("text") and attr.get("url"):
            lines.append(f"> [{attr['text']}]({attr['url']})")
            lines.append("")

    starters = [p for p in ctx.players if p.roster_slot == "starter"]
    bench = [p for p in ctx.players if p.roster_slot == "bench"]

    lines.append("## Current starters")
    lines.append(_table(starters))
    lines.append("")
    lines.append("## Bench (candidates to consider starting)")
    lines.append(_table(bench))
    lines.append("")
    lines.append("## Notes / flags")
    for p in ctx.players:
        notes = []
        if p.injury_status:
            notes.append(
                f"injury: {p.injury_status}"
                + (f" ({p.injury_body_part})" if p.injury_body_part else "")
                + (f" -- {p.injury_notes}" if p.injury_notes else "")
            )
        if p.market_value is not None or p.market_overall_rank is not None:
            mbits = []
            if p.market_value is not None:
                mbits.append(f"value {p.market_value:g}")
            if p.market_overall_rank is not None:
                mbits.append(f"OVR #{p.market_overall_rank}")
            if p.market_position_rank is not None:
                mbits.append(f"{p.position}#{p.market_position_rank}")
            notes.append("market: " + ", ".join(mbits))
        if p.status_blurb:
            sig = f" [{', '.join(p.status_blurb_signals)}]" if p.status_blurb_signals else ""
            when = f" ({p.status_blurb_at})" if p.status_blurb_at else ""
            notes.append(f"blurb{sig}{when}: {p.status_blurb}")
        if p.weather and p.weather.get("note"):
            notes.append(f"weather: {p.weather['note']}")
        if p.game_script_note:
            notes.append(f"game script: {p.game_script_note}")
        if notes:
            lines.append(f"- **{p.name}** ({p.position}, {p.nfl_team}): " + " | ".join(notes))

    return "\n".join(lines)


def _table(players: list[PlayerContext]) -> str:
    header = (
        "| Player | Pos | Team | Opp | H/A | Kickoff (UTC) | Roof | Wind | Precip% | "
        "Proj | RW | FP | T01 | Mkt | OVR | Spread | Total | Implied | Injury | Script |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [header]
    for p in players:
        w = p.weather or {}
        by_src = p.projections_by_source or {}
        rows.append(
            "| {name} | {pos} | {team} | {opp} | {ha} | {ko} | {roof} | {wind} | {precip} | "
            "{proj} | {rw} | {fp} | {t01} | {mkt} | {ovr} | {spread} | {total} | {implied} | "
            "{injury} | {script} |".format(
                name=p.name,
                pos=p.position,
                team=p.nfl_team or "-",
                opp=p.opponent or "-",
                ha=p.home_away or "-",
                ko=(p.kickoff_utc or "-")[:16].replace("T", " "),
                roof=p.venue_roof or "-",
                wind=w.get("wind_mph", "-"),
                precip=w.get("precipitation_probability_pct", "-"),
                proj=p.projected_points if p.projected_points is not None else "-",
                rw=by_src.get("rotowire", "-"),
                fp=by_src.get("fantasypros", "-"),
                t01=by_src.get("tank01", "-"),
                mkt=p.market_value if p.market_value is not None else "-",
                ovr=p.market_overall_rank if p.market_overall_rank is not None else "-",
                spread=p.vegas_spread if p.vegas_spread is not None else "-",
                total=p.vegas_total if p.vegas_total is not None else "-",
                implied=p.implied_team_total if p.implied_team_total is not None else "-",
                injury=p.injury_status or "-",
                script=p.game_script_flag or "-",
            )
        )
    return "\n".join(rows)
