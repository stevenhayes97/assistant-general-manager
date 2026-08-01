"""CLI entrypoint for the POC.

Usage:
    python -m sleeper_advisor.cli --format markdown
    python -m sleeper_advisor.cli --league-id 123 --roster-id 4 --week 9 --format json

Designed so the same `build_context()` call this CLI wraps can later be
called directly from a FastAPI (or similar) handler, with league/roster/week
coming from request parameters instead of env vars/flags.
"""

from __future__ import annotations

import argparse
import json
import sys

from .config import load_config
from .context_builder import build_context
from .formatting import to_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", default=None, help="Overrides SLEEPER_LEAGUE_ID")
    parser.add_argument("--roster-id", type=int, default=None, help="Overrides SLEEPER_ROSTER_ID")
    parser.add_argument("--username", default=None, help="Overrides SLEEPER_USERNAME")
    parser.add_argument("--odds-api-key", default=None, help="Overrides ODDS_API_KEY")
    parser.add_argument("--week", type=int, default=None, help="Overrides SLEEPER_WEEK")
    parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
    args = parser.parse_args(argv)

    config = load_config(
        league_id=args.league_id,
        roster_id=args.roster_id,
        username=args.username,
        odds_api_key=args.odds_api_key,
        week=args.week,
    )

    try:
        context = build_context(config)
    except ValueError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # network/API failures, etc.
        print(f"Failed to build lineup context: {exc}", file=sys.stderr)
        return 2

    if args.format == "json":
        print(json.dumps(context.to_dict(), indent=2))
    else:
        print(to_markdown(context))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
