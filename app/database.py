from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path


class DatabaseError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path, migrations_dir: Path | None = None) -> None:
        self.path = Path(path)
        self.migrations_dir = migrations_dir or Path(__file__).with_name("migrations")
        self._connection: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._connection is None:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self.path, check_same_thread=False)
                connection.row_factory = sqlite3.Row
                connection.execute("PRAGMA foreign_keys = ON")
                connection.execute("PRAGMA journal_mode = WAL")
                connection.execute("PRAGMA synchronous = NORMAL")
                connection.execute("PRAGMA busy_timeout = 5000")
                self._connection = connection
            return self._connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            connection = self.connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                yield connection
            except Exception:
                connection.rollback()
                raise
            else:
                connection.commit()

    def migrate(self, backup_dir: Path | None = None) -> list[int]:
        connection = self.connect()
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.commit()
        applied = {
            row["version"]
            for row in connection.execute("SELECT version FROM schema_migrations").fetchall()
        }
        migrations: list[tuple[int, Path]] = []
        for path in sorted(self.migrations_dir.glob("*.sql")):
            try:
                version = int(path.name.split("_", 1)[0])
            except ValueError as exc:
                raise DatabaseError(f"invalid migration filename: {path.name}") from exc
            if version not in applied:
                migrations.append((version, path))

        if migrations and self.path.exists() and self.path.stat().st_size and backup_dir:
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            self.backup(Path(backup_dir) / f"pre-migration-{stamp}.sqlite3")

        completed: list[int] = []
        for version, path in migrations:
            sql = path.read_text(encoding="utf-8")
            with self._lock:
                try:
                    connection.executescript(sql)
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                        (version, datetime.now(UTC).isoformat()),
                    )
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            completed.append(version)
        return completed

    def backup(self, target: Path) -> Path:
        target = Path(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(target.suffix + ".tmp")
        if temporary.exists():
            temporary.unlink()
        with self._lock:
            destination = sqlite3.connect(temporary)
            try:
                self.connect().backup(destination)
                result = destination.execute("PRAGMA integrity_check").fetchone()[0]
                if result != "ok":
                    raise DatabaseError(f"backup integrity check failed: {result}")
            finally:
                destination.close()
        temporary.replace(target)
        return target

    def integrity_check(self) -> bool:
        with self._lock:
            return self.connect().execute("PRAGMA integrity_check").fetchone()[0] == "ok"
