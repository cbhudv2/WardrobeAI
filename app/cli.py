from __future__ import annotations

import argparse
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.database import Database
from app.legacy_import import import_legacy


def main() -> int:
    parser = argparse.ArgumentParser(prog="wardrobe-ai")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate")
    subparsers.add_parser("import-legacy")
    backup = subparsers.add_parser("backup")
    backup.add_argument("--output", type=Path)
    check = subparsers.add_parser("restore-check")
    check.add_argument("path", type=Path)
    arguments = parser.parse_args()

    if arguments.command == "restore-check":
        connection = sqlite3.connect(f"file:{arguments.path}?mode=ro", uri=True)
        try:
            result = connection.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            connection.close()
        print(result)
        return 0 if result == "ok" else 1

    settings = get_settings()
    database = Database(settings.database_path)
    database.migrate(settings.backup_dir)
    try:
        if arguments.command == "migrate":
            return 0
        if arguments.command == "import-legacy":
            counts = import_legacy(
                database,
                settings.legacy_wardrobe_path,
                settings.legacy_history_path,
                min(settings.allowed_user_ids),
                settings.telegram_daily_chat_id,
            )
            print(f"items={counts['items']} outfits={counts['outfits']}")
            return 0
        if arguments.command == "backup":
            output = arguments.output or settings.backup_dir / (
                f"manual-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}.sqlite3"
            )
            print(database.backup(output))
            return 0
    finally:
        database.close()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
