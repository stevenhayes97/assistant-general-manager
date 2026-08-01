"""Render an AdvisorContext as human/agent-friendly Markdown."""

from __future__ import annotations

from .context_builder import AdvisorContext, PlayerContext


def to_markdown(ctx: AdvisorContext) -> str:
    lines = [
        f"# Lineup context -- {ctx.league_name or ctx.league_id}, Week {ctx.week} ({ctx.season})",
        f"_Generated {ctx.generated_at_utc}_",
        "",
    ]
    if not ctx.odds_available:
        lines.append(
            "> No odds API key configured (or lookup failed) -- Vegas spread/total/game-script "
            "signals are unavailable below. Set ODDS_API_KEY to enable them."
        )
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
        "Spread | Total | Implied | Injury | Script |\n"
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"
    )
    rows = [header]
    for p in players:
        w = p.weather or {}
        rows.append(
            "| {name} | {pos} | {team} | {opp} | {ha} | {ko} | {roof} | {wind} | {precip} | "
            "{spread} | {total} | {implied} | {injury} | {script} |".format(
                name=p.name,
                pos=p.position,
                team=p.nfl_team or "-",
                opp=p.opponent or "-",
                ha=p.home_away or "-",
                ko=(p.kickoff_utc or "-")[:16].replace("T", " "),
                roof=p.venue_roof or "-",
                wind=w.get("wind_mph", "-"),
                precip=w.get("precipitation_probability_pct", "-"),
                spread=p.vegas_spread if p.vegas_spread is not None else "-",
                total=p.vegas_total if p.vegas_total is not None else "-",
                implied=p.implied_team_total if p.implied_team_total is not None else "-",
                injury=p.injury_status or "-",
                script=p.game_script_flag or "-",
            )
        )
    return "\n".join(rows)
