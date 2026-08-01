# Agent notes — Assistant General Manager

## Lineup advisor

Custom subagent: `.cursor/agents/lineup-advisor.md` (pinned to Grok 4.5 High).

Invoke from Cursor Agent chat:

```text
/lineup-advisor who should I start this week?
```

Data gatherer (no advice, structured context only):

```bash
python3 -m sleeper_advisor.cli --format markdown
```

Identity: prefer `SLEEPER_ROSTER_ID` (stable) over username.

## Cursor Cloud specific instructions

Cloud Agents / Mobile use this repo's `.cursor/environment.json` install
script (`pip install -r requirements.txt`).

Add these as **Secrets** on the Cloud Agents environment (never commit them):

| Secret | Required | Notes |
|---|---|---|
| `SLEEPER_LEAGUE_ID` | yes | From sleeper.com league URL |
| `SLEEPER_ROSTER_ID` | yes | Numeric roster ID in that league |
| `ODDS_API_KEY` | no | Free key from the-odds-api.com; enables game-script flags |
| `FANTASYPROS_API_KEY` | no | FantasyPros API key; second weekly projection source |

When asked for start/sit advice, run the lineup-advisor flow: execute the
CLI for structured context, then web-search injury nuance / trends / expert
opinion, and return recommendations with confidence levels. Do not
fabricate injury reports or stats. Treat `projected_points` as the mean of
available structured sources (RotoWire via Sleeper, FantasyPros when keyed),
with per-source values in `projections_by_source` — not a scraped web consensus.

Allowed outbound APIs (all free / public except Odds / FantasyPros):

- `api.sleeper.app` (includes undocumented `/projections/nfl/...` — no key)
- `api.fantasypros.com` (if `FANTASYPROS_API_KEY` configured)
- `site.api.espn.com`
- `api.open-meteo.com`
- `api.the-odds-api.com` (if key configured)
