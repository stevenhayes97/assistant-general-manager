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

When asked for start/sit advice, run the lineup-advisor flow: execute the
CLI for structured context, then web-search injury nuance / trends / expert
opinion, and return recommendations with confidence levels. Do not
fabricate injury reports or stats.

Allowed outbound APIs (all free / public except Odds):

- `api.sleeper.app`
- `site.api.espn.com`
- `api.open-meteo.com`
- `api.the-odds-api.com` (if key configured)
