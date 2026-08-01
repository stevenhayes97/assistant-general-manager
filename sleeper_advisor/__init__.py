"""Sleeper fantasy football lineup advisor.

This package gathers *objective* signals about your current Sleeper roster
(opponent, venue, weather, injury status, Vegas lines) into a single
structured "context" bundle. It intentionally does NOT try to generate the
final start/sit advice itself -- that qualitative reasoning (trends, expert
opinion synthesis, "playing through a nagging injury" nuance, game-script
judgement) is left to the calling agent, which can pair this structured data
with live web search.

The public entry points intended for a future API wrapper are
``AdvisorConfig``, ``load_config``, and ``build_context``.
"""

from .config import AdvisorConfig, load_config
from .context_builder import AdvisorContext, build_context

__all__ = [
    "AdvisorConfig",
    "AdvisorContext",
    "build_context",
    "load_config",
]
