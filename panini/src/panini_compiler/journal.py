"""SQLite compare-and-swap snapshots and atomic invocation/event history.

A revision fences stale local writers. External adapters must honor idempotency
keys (and fencing where available); SQLite alone cannot guarantee exactly-once
remote effects. Checkpoints on arbitrary storage remain trusted inputs.
"""

from __future__ import annotations

from contextlib import closing
import json
from pathlib import Path
import sqlite3


class JournalConflict(RuntimeError):
    pass


class SQLiteJournal:
    def __init__(self, path: str | Path):
        self.path = str(path)
        with closing(self._connect()) as connection, connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, revision INTEGER NOT NULL, snapshot TEXT NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS events (run_id TEXT NOT NULL, revision INTEGER NOT NULL, event TEXT NOT NULL, PRIMARY KEY(run_id,revision))"
            )

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def save(
        self, run_id: str, snapshot: dict, expected_revision: int, event: dict
    ) -> int:
        payload = json.dumps(snapshot, sort_keys=True, allow_nan=False)
        with closing(self._connect()) as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
            actual = row[0] if row else 0
            if actual != expected_revision:
                raise JournalConflict(
                    f"Stale run revision: expected {expected_revision}, found {actual}"
                )
            revision = actual + 1
            connection.execute(
                "INSERT INTO runs VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision,snapshot=excluded.snapshot",
                (run_id, revision, payload),
            )
            connection.execute(
                "INSERT INTO events VALUES (?,?,?)",
                (run_id, revision, json.dumps(event, sort_keys=True, allow_nan=False)),
            )
        return revision

    def load(self, run_id: str) -> tuple[dict, int]:
        with closing(self._connect()) as connection, connection:
            row = connection.execute(
                "SELECT snapshot,revision FROM runs WHERE run_id=?", (run_id,)
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        return json.loads(row[0]), row[1]

    def events(self, run_id: str) -> list[dict]:
        with closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT revision,event FROM events WHERE run_id=? ORDER BY revision",
                (run_id,),
            ).fetchall()
        return [
            dict(revision=revision, **json.loads(payload)) for revision, payload in rows
        ]
