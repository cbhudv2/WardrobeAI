from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from app.database import Database


def record_audit_event(
    database: Database,
    event_type: str,
    result: str,
    *,
    actor_user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    safe_metadata = metadata or {}
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO audit_events(id, actor_user_id, event_type, entity_type, entity_id, "
            "result, metadata_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"audit_{uuid.uuid4().hex}",
                actor_user_id,
                event_type,
                entity_type,
                entity_id,
                result,
                json.dumps(safe_metadata, sort_keys=True),
                datetime.now(UTC).isoformat(),
            ),
        )
