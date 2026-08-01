# Future API design (not implemented yet)

The data-gathering layer is intentionally shaped so a thin HTTP API can
wrap it without rewriting core logic. This document is the design target;
do **not** build the FastAPI service until that roadmap item is picked up.

## Goal

Expose `sleeper_advisor.context_builder.build_context()` behind an HTTP
API so any user can pass their own league / roster / week and receive the
structured context bundle. A later step can have that API invoke a Cursor
Cloud Agent (same prompt as `.cursor/agents/lineup-advisor.md`) per
request, then a thin UI on top.

## Suggested surface

```
POST /v1/lineup-context
```

Request body (JSON):

| Field | Type | Required | Notes |
|---|---|---|---|
| `league_id` | string | yes | Sleeper league ID |
| `roster_id` | int | one of roster_id / username | Sleeper roster ID in that league |
| `username` | string | one of roster_id / username | Resolved via Sleeper users endpoint |
| `week` | int | no | Defaults to Sleeper NFL state week |
| `odds_api_key` | string | no | Optional; omit to skip Vegas/game-script |
| `format` | `"json"` \| `"markdown"` | no | Default `json` |

Response: the existing `AdvisorContext.to_dict()` payload (or markdown
string when `format=markdown`). No persistence of roster/lineup data —
every call re-fetches live sources.

## Mapping to today's code

```python
# Conceptual handler — not shipped yet
from sleeper_advisor.config import AdvisorConfig
from sleeper_advisor.context_builder import build_context
from sleeper_advisor.formatting import to_markdown

config = AdvisorConfig(
    league_id=body.league_id,
    roster_id=body.roster_id,
    username=body.username,
    odds_api_key=body.odds_api_key,  # or a server-side key pool later
    week=body.week,
)
ctx = build_context(config)
return ctx.to_dict()  # or to_markdown(ctx)
```

`AdvisorConfig` + CLI flags already accept per-call overrides; env vars
remain the local/dev default. No change to clients (Sleeper / ESPN /
Open-Meteo / Odds) should be required for the first API cut.

## Agent invocation (phase 2)

1. API builds structured context (or returns it as a tool the agent can call).
2. API starts a Cursor Cloud Agent run with the lineup-advisor prompt +
   league/roster/week params (or the prebuilt context).
3. Agent performs the web-research pass (injury nuance, trends, expert
   opinion) and returns the start/sit recommendation.
4. API streams/returns that recommendation to the UI client.

## Multi-tenancy notes

- Sleeper league/roster data is public (no Sleeper auth).
- Odds API free tier is 500 req/mo — for multi-user, plan on a paid key,
  per-user keys, or caching odds for the current slate (short TTL).
- Rate-limit by API consumer; do not cache lineup context across users
  beyond short in-request reuse (player dictionary cache at `/tmp` is fine).
