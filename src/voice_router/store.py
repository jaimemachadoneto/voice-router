"""The state of the router in SQLite: the last sync and the log of requests.

A sync comes from the Home Assistant component and replaces nothing: each row
stays, and the newest row is the index that the router uses. A record holds
one request and its decision. A result joins the record when the route acts,
so a restart keeps the whole log.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

_SCHEMA = (
    (
        "CREATE TABLE IF NOT EXISTS syncs("
        "at TEXT, registry_hash TEXT, payload TEXT, alias_version TEXT)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS records("
        "id TEXT PRIMARY KEY, at TEXT, request TEXT, decision TEXT, result TEXT)"
    ),
)


def _migrate(db: sqlite3.Connection) -> None:
    """Add the alias version column to a database of an earlier version.

    `CREATE TABLE IF NOT EXISTS` keeps an old table as it is, so the column
    needs its own statement. A database with the column stays unchanged.
    """
    columns = {row[1] for row in db.execute("PRAGMA table_info(syncs)").fetchall()}
    if "alias_version" not in columns:
        db.execute("ALTER TABLE syncs ADD COLUMN alias_version TEXT")


class Store:
    """A SQLite file that holds the last sync and each request of the router."""

    def __init__(self, path: Path):
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock:
            for statement in _SCHEMA:
                self._db.execute(statement)
            _migrate(self._db)
            self._db.commit()

    @staticmethod
    def _now() -> str:
        """The current time in UTC, as a text that sorts by time."""
        return datetime.now(UTC).isoformat()

    def save_sync(self, payload: dict[str, Any], alias_version: str = "") -> None:
        """Keep the payload as the newest sync, with its words version."""
        with self._lock:
            self._db.execute(
                "INSERT INTO syncs(at, registry_hash, payload, alias_version)"
                " VALUES (?, ?, ?, ?)",
                (
                    self._now(),
                    payload.get("registry_hash", ""),
                    json.dumps(payload),
                    alias_version,
                ),
            )
            self._db.commit()

    def last_sync(self) -> dict[str, Any] | None:
        """The payload of the newest sync, or None when the store is empty."""
        with self._lock:
            row = self._db.execute(
                "SELECT payload FROM syncs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return json.loads(row[0]) if row else None

    def last_sync_version(self) -> str:
        """The words version of the newest sync, or "" when it is absent."""
        with self._lock:
            row = self._db.execute(
                "SELECT alias_version FROM syncs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        return (row[0] or "") if row else ""

    def first_sync_at(self, alias_version: str) -> str | None:
        """The time of the first sync that wrote this words version.

        This time starts the stale period of the local route. The newest sync
        can change no word, so it is not this time. None means that no sync
        wrote the version, for example an old row with no version.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT at FROM syncs WHERE alias_version = ? ORDER BY rowid LIMIT 1",
                (alias_version,),
            ).fetchone()
        return row[0] if row else None

    def add_record(self, record: dict[str, Any]) -> str:
        """Keep the request and its decision, and return the new record id."""
        record_id = uuid4().hex
        with self._lock:
            self._db.execute(
                "INSERT INTO records(id, at, request, decision, result)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    record_id,
                    self._now(),
                    json.dumps(record.get("request")),
                    json.dumps(record.get("decision")),
                    json.dumps(record.get("result")),
                ),
            )
            self._db.commit()
        return record_id

    def set_result(self, record_id: str, result: dict[str, Any]) -> bool:
        """Store the result of a record. False means that the id is unknown."""
        with self._lock:
            cursor = self._db.execute(
                "UPDATE records SET result = ? WHERE id = ?",
                (json.dumps(result), record_id),
            )
            self._db.commit()
            return cursor.rowcount > 0

    def merge_result(self, record_id: str, result: dict[str, Any]) -> bool:
        """Join a result to the record under one lock.

        A later mark joins the result of the action and does not replace it.
        False means that the id is unknown.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT result FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return False
            merged = {**(json.loads(row[0]) or {}), **result}
            self._db.execute(
                "UPDATE records SET result = ? WHERE id = ?",
                (json.dumps(merged), record_id),
            )
            self._db.commit()
            return True

    def set_local(self, record_id: str, local: dict[str, Any]) -> bool:
        """Keep the Kev answer in the decision of a record.

        The answer joins the decision, so one row holds both answers. False
        means that the id is unknown.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT decision FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return False
            decision = json.loads(row[0]) or {}
            decision["local"] = local
            self._db.execute(
                "UPDATE records SET decision = ? WHERE id = ?",
                (json.dumps(decision), record_id),
            )
            self._db.commit()
            return True

    def record(self, record_id: str) -> dict[str, Any] | None:
        """The record with its JSON parsed, or None when the id is unknown."""
        with self._lock:
            row = self._db.execute(
                "SELECT id, at, request, decision, result FROM records WHERE id = ?",
                (record_id,),
            ).fetchone()
        if row is None:
            return None
        return {
            "id": row[0],
            "at": row[1],
            "request": json.loads(row[2]),
            "decision": json.loads(row[3]),
            "result": json.loads(row[4]),
        }

    def all_records(self) -> list[dict[str, Any]]:
        """Every record, oldest first, with its JSON parsed.

        The report of the local route reads the whole log, so one query gives
        the rows in the order that the router wrote them.
        """
        with self._lock:
            rows = self._db.execute(
                "SELECT id, at, request, decision, result FROM records ORDER BY rowid"
            ).fetchall()
        return [
            {
                "id": row[0],
                "at": row[1],
                "request": json.loads(row[2]),
                "decision": json.loads(row[3]),
                "result": json.loads(row[4]),
            }
            for row in rows
        ]
