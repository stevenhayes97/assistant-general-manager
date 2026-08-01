# Assistant General Manager

A Sleeper fantasy football **lineup advisor**: a Python package pulls
objective, structured signals about your roster on every call (no lineup
persistence — always re-fetch); a Cursor **subagent** layers qualitative
reasoning (injury nuance, trends, expert consensus) via live web search to
produce the start/sit recommendation.

Adapted from a working POC
([cursor-scratch-pad#1](https://github.com/stevenhayes97/cursor-scratch-pad/pull/1))
into this dedicated repo, with fixes for game-venue weather/roof resolution
and retractable-roof uncertainty.

## Why this split

Structured data (schedule, weather, official injury designation, Vegas
lines) is cheap and reliable from APIs. "Is this WR trending up," "is this
RB playing through a nagging injury that's capping his workload," and "what
do three different analysts think" are not things a scraper does well —
that's what an LLM agent with web search is good at. So:

- **`sleeper_advisor/` (Python)** — gathers hard data into one JSON/Markdown bundle.
- **`.cursor/agents/lineup-advisor.md` (subagent)** — runs the script, fills gaps with web research, writes the final recommendation.

## What it pulls in, and from where

| Signal | Source | Auth |
|---|---|---|
| Roster, starters, official injury status | [Sleeper API](https://docs.sleeper.com/) | No |
| Weekly projected fantasy points | Sleeper projections (RotoWire; undocumented) + [FantasyPros API](https://www.fantasypros.com/api-data/) + [Tank01](https://rapidapi.com/tank01/api/tank01-nfl-live-in-game-real-time-statistics-nfl) | FantasyPros / Tank01 keys optional |
| Market value / ranks + status blurbs (LLM context) | [LeagueLogs API](https://leaguelogs.com/developers) | No (attribution required) |
| Depth-chart role (LLM context) | Tank01 `/getNFLDepthCharts` | Tank01 key optional |
| NFL opponent, home/away, kickoff, venue | ESPN scoreboard (public); **Tank01** `getNFLGamesForWeek` fallback when ESPN’s season year is stale | Tank01 key for future-season weeks |
| Weather at kickoff (outdoor / retractable only) | [Open-Meteo](https://open-meteo.com/) | No |
| Vegas spread / total / implied total / game-script flag | [The Odds API](https://the-odds-api.com/) (primary) + Tank01 multi-book median (second opinion) | Odds / Tank01 keys optional |

Projected points average every available source (RotoWire always attempted;
FantasyPros when `FANTASYPROS_API_KEY` is set; Tank01 when `TANK01_API_KEY`
is set). Each source uses the league's PPR / half-PPR / standard bucket
(`scoring_settings.rec`), then adds league reception bonuses such as TE
premium (`bonus_rec_te` × projected TE receptions). Per-source values are kept
in `projections_by_source`. FantasyPros players join via Sportradar /
sportsdata UUIDs; Tank01 joins via `sleeperBotID` (ESPN id fallback).

[LeagueLogs](https://leaguelogs.com/developers) is always attempted (no key):
Market Index value/ranks for the closest published profile (dynasty/redraft ×
1QB/2QB × PPR), plus short status blurbs for rostered skill players. These are
**reasoning aids for the subagent**, not weekly point projections. Attribution
is included in the markdown output.

When `TANK01_API_KEY` is set, Tank01 also supplies:
- **Multi-book odds** (`/getNFLBettingOdds`) — median spread/total across
  sportsbooks as a second opinion; disagreement notes when books diverge.
  Game-script flags still use The Odds API primary line when present.
- **Depth charts** (`/getNFLDepthCharts`) — e.g. WR2 + chart line for LLM
  reasoning (not snap-share guarantees).

### Weather rules

- Every stadium has a roof type: `outdoor`, `dome`, or `retractable`.
- **Dome** games skip weather entirely and label it a non-factor — no forecast is fetched or fabricated.
- **Outdoor / retractable** games fetch the Open-Meteo forecast at kickoff. Retractable roofs add an explicit uncertainty note (usually closed; do not guess open/closed).
- Weather/roof always use the **game venue** (home team's stadium), including for away players.

### Injury nuance (agent, not script)

Sleeper's `injury_status` is a starting point only. The subagent must
actively web-search beat writers / practice reports / "playing through X"
notes for flagged or close-call players, cite what was found and when, and
say explicitly when nothing current is found. Same for trends and expert
opinions — no fixed expert-consensus API.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # pytest
cp .env.example .env
# fill SLEEPER_LEAGUE_ID + SLEEPER_ROSTER_ID (preferred over username)
# optional: ODDS_API_KEY from https://the-odds-api.com/
```

## Usage

```bash
python -m sleeper_advisor.cli --format markdown
python -m sleeper_advisor.cli --format json > context.json

# Per-call overrides (same shape a future API will use as request params):
python -m sleeper_advisor.cli --league-id <id> --roster-id <id> --week <n> --format json
```

As a Cursor subagent (pinned to **Grok 4.5 High**):

```
/lineup-advisor who should I start this week?
```

## Deploy (Cursor Cloud / Mobile)

1. Merge this branch (or use it as the Cloud Agent ref).
2. Ensure `.cursor/environment.json` is present (installs Python deps on boot).
3. In [Cloud Agents → Environments → Secrets](https://cursor.com/dashboard/cloud-agents), set:
   - `SLEEPER_LEAGUE_ID`
   - `SLEEPER_ROSTER_ID`
   - `ODDS_API_KEY` (optional)
   - `FANTASYPROS_API_KEY` (optional; second projection source)
   - `TANK01_API_KEY` (optional; third projection source via RapidAPI)
4. From desktop, web ([cursor.com/agents](https://cursor.com/agents)), or the iOS app, start an agent on this repo and ask for lineup advice (or `/lineup-advisor` on desktop).

See `AGENTS.md` for cloud-specific agent instructions.

## Tests

```bash
python -m pytest tests/ -v
```

Covers stadium reference data, game-script classification, dome skip /
retractable uncertainty, and away-game venue resolution. No network
required.

## Roadmap: API + UI

Designed for, not built yet — see [`docs/api-design.md`](docs/api-design.md).

1. Wrap `build_context()` in FastAPI (league / roster / week / odds key as request params).
2. Invoke a Cursor Cloud Agent per request with the lineup-advisor prompt.
3. Thin UI that collects league/team and displays the recommendation.

## Known limitations

- ESPN's scoreboard can lag at the very start of a new season.
- Game-script thresholds (`BLOWOUT_SPREAD_THRESHOLD`,
  `LOW_COMPETITIVENESS_TOTAL_CEILING`) are heuristics, not fitted values.
- Odds API free tier is 500 req/mo — fine personally, not multi-user without a paid plan or caching.
- Sleeper's full player dictionary (~5MB) is cached under `/tmp` for 12h; everything else is fetched fresh each run.
- Weekly projections come from an **undocumented** Sleeper endpoint that
  currently serves RotoWire numbers. No API key is required today, but the
  path may change; the gatherer degrades gracefully if the lookup fails.
  During NFL preseason, `season_type=pre` is often ADP-only, so the client
  falls back to `regular` when that feed already has weekly point totals.
