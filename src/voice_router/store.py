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

from voice_router.words import fold

_SCHEMA = (
    (
        "CREATE TABLE IF NOT EXISTS syncs("
        "at TEXT, registry_hash TEXT, payload TEXT, alias_version TEXT)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS records("
        "id TEXT PRIMARY KEY, at TEXT, request TEXT, decision TEXT, result TEXT)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS proposals("
        "id TEXT PRIMARY KEY, word TEXT, word_key TEXT, meaning TEXT, domain TEXT,"
        " entity_id TEXT, at TEXT, status TEXT, commands TEXT,"
        " before_count INTEGER, after_count INTEGER,"
        " decided_at TEXT, decided_by TEXT)"
    ),
    (
        "CREATE TABLE IF NOT EXISTS word_progress("
        "word_key TEXT, entity_id TEXT, agreements INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY(word_key, entity_id))"
    ),
    (
        "CREATE TABLE IF NOT EXISTS word_events("
        "word_key TEXT, entity_id TEXT, record_id TEXT, at TEXT,"
        " PRIMARY KEY(word_key, entity_id, record_id))"
    ),
    (
        "CREATE TABLE IF NOT EXISTS word_changes("
        "at TEXT, word TEXT, entity_id TEXT, change TEXT, source TEXT,"
        " record_id TEXT)"
    ),
)

# The columns of a proposal row, in the order that `_proposal` reads them.
_PROPOSAL_COLUMNS = (
    "id, word, meaning, domain, entity_id, at, status, commands,"
    " before_count, after_count, decided_at, decided_by"
)

# The most commands that one proposal keeps.
PROPOSAL_COMMANDS = 10


def _proposal(row: tuple) -> dict[str, Any]:
    """One proposal row, with the commands parsed."""
    return {
        "id": row[0],
        "word": row[1],
        "meaning": row[2],
        "domain": row[3],
        "entity_id": row[4],
        "at": row[5],
        "status": row[6],
        "commands": json.loads(row[7]) or [],
        "before_count": row[8],
        "after_count": row[9],
        "decided_at": row[10],
        "decided_by": row[11],
    }


def _dedupe_proposals(db: sqlite3.Connection) -> None:
    """Keep one row for each folded word and entity.

    An old version could write two rows for one pair: two spellings of one
    word, or two writers of a race. The unique index cannot start on such a
    database. The row with the lowest rowid stays, its commands join the
    commands of the other rows, and the other rows go.
    """
    groups = db.execute(
        "SELECT word_key, entity_id FROM proposals"
        " GROUP BY word_key, entity_id HAVING COUNT(*) > 1"
    ).fetchall()
    for word_key, entity_id in groups:
        rows = db.execute(
            "SELECT id, commands FROM proposals"
            " WHERE word_key IS ? AND entity_id IS ? ORDER BY rowid",
            (word_key, entity_id),
        ).fetchall()
        commands: list[str] = []
        for _, stored in rows:
            for text in json.loads(stored) if stored else []:
                if text not in commands and len(commands) < PROPOSAL_COMMANDS:
                    commands.append(text)
        db.execute(
            "UPDATE proposals SET commands = ? WHERE id = ?",
            (json.dumps(commands), rows[0][0]),
        )
        db.executemany(
            "DELETE FROM proposals WHERE id = ?",
            [(row[0],) for row in rows[1:]],
        )


def _migrate(db: sqlite3.Connection) -> None:
    """Add the columns of a later version to a database of an earlier one.

    `CREATE TABLE IF NOT EXISTS` keeps an old table as it is, so each column
    needs its own statement. A database with the column stays unchanged.
    """
    sync_columns = {row[1] for row in db.execute("PRAGMA table_info(syncs)").fetchall()}
    if "alias_version" not in sync_columns:
        db.execute("ALTER TABLE syncs ADD COLUMN alias_version TEXT")
    proposal_columns = {
        row[1] for row in db.execute("PRAGMA table_info(proposals)").fetchall()
    }
    if "before_count" not in proposal_columns:
        db.execute("ALTER TABLE proposals ADD COLUMN before_count INTEGER")
    if "after_count" not in proposal_columns:
        db.execute("ALTER TABLE proposals ADD COLUMN after_count INTEGER")
    if "word_key" not in proposal_columns:
        db.execute("ALTER TABLE proposals ADD COLUMN word_key TEXT")
    if "decided_at" not in proposal_columns:
        db.execute("ALTER TABLE proposals ADD COLUMN decided_at TEXT")
    if "decided_by" not in proposal_columns:
        db.execute("ALTER TABLE proposals ADD COLUMN decided_by TEXT")
    # A row of an old version has no folded key, so fill it before the dedupe.
    rows = db.execute(
        "SELECT id, word FROM proposals WHERE word_key IS NULL"
    ).fetchall()
    for record_id, word in rows:
        db.execute(
            "UPDATE proposals SET word_key = ? WHERE id = ?",
            (fold(word or ""), record_id),
        )
    # The unique index cannot start while an old duplicate exists.
    _dedupe_proposals(db)
    # The folded word and the entity give one row, also after a race of two
    # writers. The display spelling stays in `word`. The display pair is
    # unique too, as the table contract says.
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS proposals_word_entity"
        " ON proposals(word_key, entity_id)"
    )
    db.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS proposals_word_display"
        " ON proposals(word, entity_id)"
    )


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

    def merge_decision(self, record_id: str, patch: dict[str, Any]) -> bool:
        """Join keys to the decision of a record under one lock.

        False means that the id is unknown. The merge changes only the keys of
        the patch, so an answer that another writer added stays in the
        decision.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT decision FROM records WHERE id = ?", (record_id,)
            ).fetchone()
            if row is None:
                return False
            decision = json.loads(row[0]) or {}
            decision.update(patch)
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

    def merge_proposal(self, proposal: dict[str, Any], text: str) -> dict[str, Any]:
        """Insert a proposal, or join a command to the row of the same word.

        The lookup key is the folded word, so two spellings of one word give
        one row. The row keeps the display spelling of the first command. A
        refused row stays as it is: a refused word does not come back. The
        find, the insert and the command join run under one lock. A UNIQUE
        index stops a second row after a race.
        """
        key = fold(proposal["word"])
        with self._lock:
            row = self._db.execute(
                f"SELECT {_PROPOSAL_COLUMNS} FROM proposals"
                " WHERE word_key = ? AND entity_id = ? ORDER BY rowid LIMIT 1",
                (key, proposal["entity_id"]),
            ).fetchone()
            if row is not None:
                if row[6] == "refused":
                    return _proposal(row)
                commands = json.loads(row[7]) or []
                if text not in commands and len(commands) < PROPOSAL_COMMANDS:
                    commands.append(text)
                    self._db.execute(
                        "UPDATE proposals SET commands = ? WHERE id = ?",
                        (json.dumps(commands), row[0]),
                    )
                    self._db.commit()
                    row = (
                        *row[:7],
                        json.dumps(commands),
                        row[8],
                        row[9],
                        row[10],
                        row[11],
                    )
                return _proposal(row)
            commands = list(proposal.get("commands") or [text])
            self._db.execute(
                "INSERT INTO proposals"
                "(id, word, word_key, meaning, domain, entity_id, at, status,"
                " commands, before_count, after_count)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    proposal["id"],
                    proposal["word"],
                    key,
                    proposal["meaning"],
                    proposal["domain"],
                    proposal["entity_id"],
                    proposal["at"],
                    proposal["status"],
                    json.dumps(commands),
                    proposal["before_count"],
                    proposal["after_count"],
                ),
            )
            self._db.commit()
        return _proposal(
            (
                proposal["id"],
                proposal["word"],
                proposal["meaning"],
                proposal["domain"],
                proposal["entity_id"],
                proposal["at"],
                proposal["status"],
                json.dumps(commands),
                proposal["before_count"],
                proposal["after_count"],
                None,
                None,
            )
        )

    def list_proposals(self, status: str = "proposed") -> list[dict[str, Any]]:
        """The proposals with one status, oldest first."""
        with self._lock:
            rows = self._db.execute(
                f"SELECT {_PROPOSAL_COLUMNS}"
                " FROM proposals WHERE status = ? ORDER BY rowid",
                (status,),
            ).fetchall()
        return [_proposal(row) for row in rows]

    def get_proposal(self, proposal_id: str) -> dict[str, Any] | None:
        """The proposal with one id, or None when the id is unknown."""
        with self._lock:
            row = self._db.execute(
                f"SELECT {_PROPOSAL_COLUMNS} FROM proposals WHERE id = ?",
                (proposal_id,),
            ).fetchone()
        return _proposal(row) if row else None

    def count_proposals(self, status: str) -> int:
        """The number of proposals with one status."""
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM proposals WHERE status = ?", (status,)
            ).fetchone()
        return row[0] if row else 0

    def is_refused(self, word_key: str, entity_id: str) -> bool:
        """True when a refused proposal holds this folded word and entity."""
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM proposals"
                " WHERE word_key = ? AND entity_id = ? AND status = 'refused'"
                " LIMIT 1",
                (word_key, entity_id),
            ).fetchone()
        return row is not None

    def decide_proposal(self, proposal_id: str, status: str, by: str, at: str) -> bool:
        """Record a decision on a proposed row. False means that it changed no row.

        The update touches only a row that still waits for a decision, so two
        decisions of one proposal do not both apply.
        """
        with self._lock:
            cursor = self._db.execute(
                "UPDATE proposals SET status = ?, decided_at = ?, decided_by = ?"
                " WHERE id = ? AND status = 'proposed'",
                (status, at, by, proposal_id),
            )
            self._db.commit()
            return cursor.rowcount > 0

    def word_agreement(self, word_key: str, entity_id: str) -> int:
        """The number of agreeing records of one shadow word, or 0.

        One row for each record gives the count, so the same record counts
        once, also when `/log` writes it again.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) FROM word_events WHERE word_key = ? AND entity_id = ?",
                (word_key, entity_id),
            ).fetchone()
        return int(row[0] or 0) if row else 0

    def add_word_event(self, word_key: str, entity_id: str, record_id: str) -> int:
        """Keep one agreeing record and return the count of the word.

        The primary key of the table holds the record, so a second call with
        the same record changes no row.
        """
        with self._lock:
            self._db.execute(
                "INSERT OR IGNORE INTO word_events"
                "(word_key, entity_id, record_id, at) VALUES (?, ?, ?, ?)",
                (word_key, entity_id, record_id, self._now()),
            )
            row = self._db.execute(
                "SELECT COUNT(*) FROM word_events WHERE word_key = ? AND entity_id = ?",
                (word_key, entity_id),
            ).fetchone()
            self._db.commit()
        return int(row[0] or 0) if row else 0

    def reset_word_agreement(self, word_key: str, entity_id: str) -> None:
        """Delete the agreeing records of a shadow word and its count row."""
        with self._lock:
            self._db.execute(
                "DELETE FROM word_events WHERE word_key = ? AND entity_id = ?",
                (word_key, entity_id),
            )
            self._db.execute(
                "DELETE FROM word_progress WHERE word_key = ? AND entity_id = ?",
                (word_key, entity_id),
            )
            self._db.commit()

    def add_word_change(
        self,
        word: str,
        entity_id: str,
        change: str,
        source: str,
        record_id: str | None,
    ) -> None:
        """Keep one change of a word list with its date, source and command.

        The rearrangement of a word list is the record that lets a person find
        a bad word later.
        """
        with self._lock:
            self._db.execute(
                "INSERT INTO word_changes"
                "(at, word, entity_id, change, source, record_id)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (self._now(), word, entity_id, change, source, record_id),
            )
            self._db.commit()
