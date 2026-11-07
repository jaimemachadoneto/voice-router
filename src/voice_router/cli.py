"""The command line of the router.

The replay command sends the labeled sets of voice-lab through the decision
and prints the counters. The compare command reads the log of the router and
reports the agreement of the local route and the switch criteria. The
export-training command writes the next training data of the device question
from the log. The router itself runs as a service; these commands measure a
change before merge.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import httpx

from voice_router.aliases import load_word_data
from voice_router.compare import summary
from voice_router.house import House
from voice_router.jev import Jev
from voice_router.replay import replay, report
from voice_router.settings import (
    DEFAULT_JEV_LIMIT,
    DEFAULT_JEV_MODEL,
    _openrouter_key,
)
from voice_router.store import Store
from voice_router.training import export


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


def _compare(path: Path) -> int:
    """Read the log and print the report of the local route.

    The four criteria come from the counters of the summary, so the report and
    the promotion rule cannot disagree.
    """
    result = summary(Store(path).all_records())
    criteria = (
        ("commands 100 or more", result["n"] >= 100),
        ("agreement 97% or more", result["agreement"] >= 0.97),
        ("no wrong device", result["wrong"] == 0),
        ("p95 under 0.25s", result["p95"] < 0.25),
    )
    print(f"n {result['n']}")
    print(f"agreement {result['agreement'] * 100:.1f}%")
    print(f"wrong {result['wrong']}")
    print(f"p50 {result['p50']:.2f}s p95 {result['p95']:.2f}s")
    for name, passed in criteria:
        print(f"{name}: {'pass' if passed else 'fail'}")
    return 0


def _export_training(path: Path, out: Path) -> int:
    """Write the next training data of the device question from the log.

    The house of the newest sync gives the text of each entity. Without a sync
    the criteria cannot be exact, so the command stops with an error that names
    the sync.
    """
    store = Store(path)
    sync = store.last_sync()
    if sync is None:
        print(
            "voice-router: the store holds no sync; sync the house first",
            file=sys.stderr,
        )
        return 2
    house = House.from_sync(sync, load_word_data(None))
    rows = export(store.all_records(), house)
    out.mkdir(parents=True, exist_ok=True)
    target = out / "train.kev.jsonl"
    target.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    print(f"{len(rows)} records written to {target}")
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
    compare_parser = commands.add_parser(
        "compare", help="Report the agreement of the local route."
    )
    compare_parser.add_argument(
        "--db", type=Path, required=True, help="the database of the router."
    )
    export_parser = commands.add_parser(
        "export-training", help="Write the next training data from the log."
    )
    export_parser.add_argument(
        "--db", type=Path, required=True, help="the database of the router."
    )
    export_parser.add_argument(
        "--out", type=Path, required=True, help="the folder of the training data."
    )
    args = parser.parse_args(argv)
    if args.command == "compare":
        return _compare(args.db)
    if args.command == "export-training":
        return _export_training(args.db, args.out)
    try:
        key = _openrouter_key()
    except RuntimeError as err:
        # The message of the settings names the variables and never the key.
        print(f"voice-router: {err}", file=sys.stderr)
        return 2
    return asyncio.run(_replay(args.data, key, args.model, args.limit, args.explain))
