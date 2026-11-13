"""The replay of the labeled sets of voice-lab.

The replay sends each labeled text through the same decision that the router
uses, with the real house of voice-lab. It counts the routes that are right,
the commands that act, the actions that are right and the devices that are
correct, partial, missing or wrong. It also gives the p50 and the p95 of the
Jev time.

The texts and the data stay outside git. The command reads them at run time
from the folder in `--data`.
"""

from __future__ import annotations

import json
from pathlib import Path

from voice_router.aliases import load_word_data
from voice_router.decide import decide
from voice_router.house import House
from voice_router.jev import Jev

LANGUAGES = ("en", "es", "pt")


def _json(path: Path) -> list[dict]:
    """The objects of a JSON file."""
    return json.loads(path.read_text())


def _jsonl(path: Path) -> list[dict]:
    """The object of each non-empty line of a JSON lines file."""
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _dedupe(words: list[str]) -> list[str]:
    """The words in their order, with one copy of each."""
    out: list[str] = []
    for word in words:
        if word not in out:
            out.append(word)
    return out


def _entity_rows(data: Path, aliases: dict) -> list[dict]:
    """The entities of the tab separated file, with the measured aliases.

    The domain words come first and the words of one entity after them. This
    is the rule of `load_aliases` in voice-lab, so the option text of an entity
    is the text of the measurement.
    """
    path = data / "entities" / "entities.tsv"
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        parts = line.split("\t")
        parts += [""] * (4 - len(parts))
        entity_id = parts[0]
        domain = entity_id.split(".")[0]
        found = list(aliases.get("domains", {}).get(domain, ()))
        found += list(aliases.get("entities", {}).get(entity_id, ()))
        rows.append(
            {
                "entity_id": entity_id,
                "name": parts[1],
                "area": parts[2] or None,
                "aliases": _dedupe(found),
                "risky": False,
            }
        )
    return rows


def build_house(data: Path) -> House:
    """The house of the voice-lab data, with the aliases of the measurement."""
    aliases_path = data / "entities" / "aliases.json"
    words = load_word_data(aliases_path)
    aliases = json.loads(aliases_path.read_text())
    payload = {
        "entities": _entity_rows(data, aliases),
        "areas": aliases.get("areas", {}),
        "registry_hash": "",
    }
    return House.from_sync(payload, words)


def _action(decision) -> str | None:
    """The action that the model answered, or the action of the decision.

    The log holds the model answer when the call succeeded. A fallback to the
    words must not hide a wrong answer of the model.
    """
    jev = decision.jev or {}
    answer = jev.get("action") or {}
    choice = answer.get("choice")
    return choice if choice is not None else decision.action


def _device_outcome(decision, expected: dict) -> str:
    """correct, partial, missing or wrong, by the rule of voice-lab."""
    chosen = set(decision.entity_ids or ())
    want = set(expected.get("expected", ()))
    ok = set(expected.get("allowed", ()))
    if not want:
        return "correct" if not chosen else "wrong"
    if not chosen:
        return "missing"
    if chosen - (want | ok):
        return "wrong"
    return "correct" if want <= chosen else "partial"


def score(rows: list[dict]) -> dict:
    """The counters of the rows of a replay.

    The route counters split the commands from the other routes, because an
    action on a device when the person asked a question is the failure that
    matters. The action counters use the answer of the model when the log holds
    it. The device counters use the rule of the measurement.
    """
    counts = {
        "route_n": 0,
        "route_right": 0,
        "command_n": 0,
        "command_act": 0,
        "non_command_n": 0,
        "non_command_act": 0,
        "action_n": 0,
        "action_right": 0,
        "device_n": 0,
        "device_correct": 0,
        "device_partial": 0,
        "device_missing": 0,
        "device_wrong": 0,
    }
    for row in rows:
        decision = row["decision"]
        if row["kind"] == "route":
            expected = row["expected"]
            counts["route_n"] += 1
            if decision.route == expected:
                counts["route_right"] += 1
            if expected == "device_command":
                counts["command_n"] += 1
                if decision.act:
                    counts["command_act"] += 1
            else:
                counts["non_command_n"] += 1
                if decision.act:
                    counts["non_command_act"] += 1
        elif row["kind"] == "action":
            counts["action_n"] += 1
            if _action(decision) == row["expected"]:
                counts["action_right"] += 1
        elif row["kind"] == "device":
            counts["device_n"] += 1
            outcome = _device_outcome(decision, row["expected"])
            counts["device_" + outcome] += 1
    return counts


def _failed(row: dict) -> bool:
    """True when the row is a failure of the decision."""
    decision = row["decision"]
    if row["kind"] == "route":
        if decision.route != row["expected"]:
            return True
        return row["expected"] != "device_command" and bool(decision.act)
    if row["kind"] == "action":
        return _action(decision) != row["expected"]
    if row["kind"] == "device":
        return _device_outcome(decision, row["expected"]) in ("wrong", "missing")
    return False


def _jev_fields(decision) -> dict:
    """The choice and the probabilities of each question in the log."""
    jev = decision.jev or {}
    out = {}
    for name in ("route", "action", "device"):
        answer = jev.get(name)
        if isinstance(answer, dict) and "choice" in answer:
            out[name] = {
                "choice": answer.get("choice"),
                "probabilities": answer.get("probabilities"),
            }
    return out


def failures(rows: list[dict]) -> str:
    """One JSON line for each row that fails, with the fields that explain it."""
    lines = []
    for row in rows:
        if not _failed(row):
            continue
        decision = row["decision"]
        entry = {
            "id": row.get("id"),
            "language": row["language"],
            "text": row.get("text"),
            "expected": row["expected"],
            "route": decision.route,
            "act": decision.act,
            "action": decision.action,
            "entity_ids": list(decision.entity_ids or ()),
            "reason": decision.reason,
            "options": decision.options,
            "filter": decision.filter,
            "jev": _jev_fields(decision),
        }
        lines.append(json.dumps(entry, ensure_ascii=False, sort_keys=True))
    return "\n".join(lines)


async def replay(data: Path, jev: Jev, limit: float) -> list[dict]:
    """The decision on every labeled text of the data folder.

    The calls run one after the other, so the Jev time of a row is the time of
    one request and the rate stays under the limit of the service.
    """
    house = build_house(data)
    routes = data / "routes"
    rows = []
    for lang in LANGUAGES:
        for item in _json(routes / f"prompts-{lang}.json"):
            decision = await decide(item["text"], item.get("room"), house, jev, limit)
            rows.append(
                {
                    "kind": "route",
                    "id": item["id"],
                    "text": item["text"],
                    "expected": item["route"],
                    "language": item["language"],
                    "decision": decision,
                }
            )
    for lang in LANGUAGES:
        for item in _json(routes / f"actions-{lang}.json"):
            decision = await decide(item["text"], item.get("room"), house, jev, limit)
            rows.append(
                {
                    "kind": "action",
                    "id": item["id"],
                    "text": item["text"],
                    "expected": item["action"],
                    "language": item["language"],
                    "decision": decision,
                }
            )
    for item in _jsonl(data / "entities" / "commands.jsonl"):
        decision = await decide(item["text"], item.get("room"), house, jev, limit)
        rows.append(
            {
                "kind": "device",
                "id": item["id"],
                "text": item["text"],
                "expected": {
                    "expected": item.get("expected", []),
                    "allowed": item.get("allowed", []),
                },
                "language": item["language"],
                "decision": decision,
            }
        )
    return rows


def _times(rows: list[dict]) -> tuple[float, float] | None:
    """The p50 and the p95 of the Jev time of the online decisions."""
    values = []
    for row in rows:
        jev = row["decision"].jev or {}
        route = jev.get("route")
        if isinstance(route, dict) and route.get("seconds") is not None:
            values.append(route["seconds"])
    if not values:
        return None
    values.sort()
    p50 = values[len(values) // 2]
    p95 = values[min(len(values) - 1, int(len(values) * 0.95))]
    return p50, p95


def _section(title: str, rows: list[dict]) -> list[str]:
    """The lines of one language, or of all rows."""
    counts = score(rows)
    lines = [
        title,
        f"  route right {counts['route_right']} of {counts['route_n']}",
        (
            f"  device commands that act {counts['command_act']}"
            f" of {counts['command_n']}"
        ),
        (
            f"  non-commands that act {counts['non_command_act']}"
            f" of {counts['non_command_n']}"
        ),
        f"  actions right {counts['action_right']} of {counts['action_n']}",
        (
            f"  devices correct {counts['device_correct']},"
            f" partial {counts['device_partial']},"
            f" missing {counts['device_missing']},"
            f" wrong {counts['device_wrong']} of {counts['device_n']}"
        ),
    ]
    times = _times(rows)
    if times is not None:
        lines.append(f"  p50 {times[0]:.2f}s p95 {times[1]:.2f}s")
    return lines


def report(rows: list[dict], explain: bool = False) -> str:
    """The counters of each language and of all rows, one section for each.

    With `explain` the report adds the rows that fail, one line for each.
    """
    lines = []
    for lang in LANGUAGES:
        lines.extend(_section(lang, [row for row in rows if row["language"] == lang]))
    lines.extend(_section("all", rows))
    text = "\n".join(lines)
    if explain:
        found = failures(rows)
        text += "\n\nfailed rows:\n" + (found if found else "none")
    return text
