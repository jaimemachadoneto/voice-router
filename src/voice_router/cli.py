"""The command line of the router.

The replay command sends the labeled sets of voice-lab through the decision
and prints the counters. The router itself runs as a service; this command
measures a change before merge.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import httpx

from voice_router.jev import Jev
from voice_router.replay import replay, report
from voice_router.settings import (
    DEFAULT_JEV_LIMIT,
    DEFAULT_JEV_MODEL,
    _openrouter_key,
)


async def _replay(data: Path, key: str, model: str, limit: float, explain: bool) -> int:
    """Run the replay with one HTTP client, then print the report.

    The client closes with the `async with`, so the command leaves no open
    connection. The key reaches the client only, and no print or error holds
    it.
    """
    async with httpx.AsyncClient() as client:
        jev = Jev(key, model=model, client=client)
        rows = await replay(data, jev, limit)
    print(report(rows, explain=explain))
    return 0


def main(argv: list[str] | None = None) -> int:
    """Run the command of the arguments, and return the exit code."""
    parser = argparse.ArgumentParser(
        prog="voice-router",
        description="Measure the voice router with the labeled sets of voice-lab.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    replay_parser = commands.add_parser(
        "replay", help="Replay the labeled sets of voice-lab."
    )
    replay_parser.add_argument(
        "--data", type=Path, required=True, help="the voice-lab data folder."
    )
    replay_parser.add_argument(
        "--limit",
        type=float,
        default=DEFAULT_JEV_LIMIT,
        help="the most seconds for one Jev answer.",
    )
    replay_parser.add_argument(
        "--model", default=DEFAULT_JEV_MODEL, help="the Jev model alias."
    )
    replay_parser.add_argument(
        "--explain",
        action="store_true",
        help="print one line for each row that fails.",
    )
    args = parser.parse_args(argv)
    try:
        key = _openrouter_key()
    except RuntimeError as err:
        # The message of the settings names the variables and never the key.
        print(f"voice-router: {err}", file=sys.stderr)
        return 2
    return asyncio.run(_replay(args.data, key, args.model, args.limit, args.explain))
