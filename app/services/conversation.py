from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.database import Database


class ConversationMemory:
    def __init__(self, database: Database, *, retained_messages: int = 40) -> None:
        self.database = database
        self.retained_messages = max(10, min(retained_messages, 100))

    def recent(
        self,
        owner_user_id: int,
        chat_id: int,
        *,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        rows = (
            self.database.connect()
            .execute(
                "SELECT role, content FROM ("
                "SELECT id, role, content FROM conversation_messages "
                "WHERE owner_user_id=? AND chat_id=? ORDER BY id DESC LIMIT ?"
                ") ORDER BY id",
                (owner_user_id, chat_id, max(1, min(limit, 20))),
            )
            .fetchall()
        )
        return [{"role": row["role"], "content": row["content"]} for row in rows]

    def append_exchange(
        self,
        owner_user_id: int,
        chat_id: int,
        user_text: str,
        assistant_text: str,
    ) -> None:
        user_content = user_text.strip()[:6000]
        assistant_content = assistant_text.strip()[:6000]
        if not user_content or not assistant_content:
            return
        created_at = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.executemany(
                "INSERT INTO conversation_messages("
                "owner_user_id, chat_id, role, content, created_at) VALUES (?, ?, ?, ?, ?)",
                (
                    (owner_user_id, chat_id, "user", user_content, created_at),
                    (owner_user_id, chat_id, "assistant", assistant_content, created_at),
                ),
            )
            connection.execute(
                "DELETE FROM conversation_messages WHERE owner_user_id=? AND chat_id=? "
                "AND id NOT IN (SELECT id FROM conversation_messages "
                "WHERE owner_user_id=? AND chat_id=? ORDER BY id DESC LIMIT ?)",
                (
                    owner_user_id,
                    chat_id,
                    owner_user_id,
                    chat_id,
                    self.retained_messages,
                ),
            )

    def clear(self, owner_user_id: int, chat_id: int) -> int:
        with self.database.transaction() as connection:
            return connection.execute(
                "DELETE FROM conversation_messages WHERE owner_user_id=? AND chat_id=?",
                (owner_user_id, chat_id),
            ).rowcount
