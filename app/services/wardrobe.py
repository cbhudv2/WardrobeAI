from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.database import Database
from app.domain.enums import PendingActionKind, PendingStatus, WearRole
from app.domain.schemas import WardrobeItem, WardrobeItemDraft, WardrobeItemPatch
from app.errors import ConfirmationError, ConflictError, NotFoundError


def _now() -> datetime:
    return datetime.now(UTC)


def _row_to_item(row: Any) -> WardrobeItem:
    return WardrobeItem(
        id=row["id"],
        owner_user_id=row["owner_user_id"],
        name=row["name"],
        category=row["category"],
        color=row["color"],
        roles=set(json.loads(row["roles_json"])),
        attributes=json.loads(row["attributes_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted_at=row["deleted_at"],
    )


class WardrobeService:
    def __init__(self, database: Database) -> None:
        self.database = database

    def get_item(
        self, owner_user_id: int, item_id: str, *, include_deleted: bool = False
    ) -> WardrobeItem:
        sql = "SELECT * FROM wardrobe_items WHERE owner_user_id=? AND id=?"
        parameters: list[Any] = [owner_user_id, item_id]
        if not include_deleted:
            sql += " AND deleted_at IS NULL"
        row = self.database.connect().execute(sql, parameters).fetchone()
        if row is None:
            raise NotFoundError(f"wardrobe item not found: {item_id}")
        return _row_to_item(row)

    def list_items(
        self,
        owner_user_id: int,
        *,
        query: str | None = None,
        category: str | None = None,
        role: WearRole | None = None,
        include_deleted: bool = False,
        limit: int = 50,
    ) -> list[WardrobeItem]:
        clauses = ["owner_user_id=?"]
        parameters: list[Any] = [owner_user_id]
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if query:
            clauses.append("(name LIKE ? COLLATE NOCASE OR color LIKE ? COLLATE NOCASE)")
            pattern = f"%{query.strip()}%"
            parameters.extend([pattern, pattern])
        if category:
            clauses.append("category=?")
            parameters.append(category)
        if role:
            clauses.append("roles_json LIKE ?")
            parameters.append(f'%"{role.value}"%')
        parameters.append(max(1, min(limit, 100)))
        query_sql = (
            "SELECT * FROM wardrobe_items WHERE "
            f"{' AND '.join(clauses)} ORDER BY category, name LIMIT ?"
        )
        rows = (
            self.database.connect()
            .execute(
                query_sql,
                parameters,
            )
            .fetchall()
        )
        return [_row_to_item(row) for row in rows]

    def find_by_name(self, owner_user_id: int, name: str) -> list[WardrobeItem]:
        normalized = name.strip()
        rows = (
            self.database.connect()
            .execute(
                "SELECT * FROM wardrobe_items WHERE owner_user_id=? AND deleted_at IS NULL "
                "AND name LIKE ? COLLATE NOCASE ORDER BY name LIMIT 10",
                (owner_user_id, f"%{normalized}%"),
            )
            .fetchall()
        )
        return [_row_to_item(row) for row in rows]

    def draft_add(
        self, owner_user_id: int, chat_id: int, draft: WardrobeItemDraft
    ) -> dict[str, str]:
        return self._create_action(
            owner_user_id,
            chat_id,
            PendingActionKind.ADD_ITEM,
            draft.model_dump(mode="json"),
        )

    def draft_update(
        self,
        owner_user_id: int,
        chat_id: int,
        item_id: str,
        patch: WardrobeItemPatch,
    ) -> dict[str, str]:
        current = self.get_item(owner_user_id, item_id)
        merged = current.model_dump(include={"name", "category", "color", "roles", "attributes"})
        merged.update(patch.model_dump(exclude_unset=True, mode="json"))
        validated = WardrobeItemDraft.model_validate(merged)
        return self._create_action(
            owner_user_id,
            chat_id,
            PendingActionKind.UPDATE_ITEM,
            validated.model_dump(mode="json"),
            target_item_id=item_id,
        )

    def draft_remove(self, owner_user_id: int, chat_id: int, item_id: str) -> dict[str, str]:
        item = self.get_item(owner_user_id, item_id)
        return self._create_action(
            owner_user_id,
            chat_id,
            PendingActionKind.REMOVE_ITEM,
            {"name": item.name},
            target_item_id=item_id,
        )

    def draft_restore(self, owner_user_id: int, chat_id: int, item_id: str) -> dict[str, str]:
        item = self.get_item(owner_user_id, item_id, include_deleted=True)
        if item.deleted_at is None:
            raise ConflictError("item is already active")
        return self._create_action(
            owner_user_id,
            chat_id,
            PendingActionKind.RESTORE_ITEM,
            {"name": item.name},
            target_item_id=item_id,
        )

    def _create_action(
        self,
        owner_user_id: int,
        chat_id: int,
        kind: PendingActionKind,
        payload: dict[str, Any],
        *,
        target_item_id: str | None = None,
    ) -> dict[str, str]:
        now = _now()
        action_id = f"action_{uuid.uuid4().hex}"
        token = secrets.token_urlsafe(12)
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE pending_actions SET status=?, updated_at=? "
                "WHERE owner_user_id=? AND chat_id=? AND status=?",
                (
                    PendingStatus.CANCELLED.value,
                    now.isoformat(),
                    owner_user_id,
                    chat_id,
                    PendingStatus.PENDING.value,
                ),
            )
            connection.execute(
                "INSERT INTO pending_actions(id, callback_token, owner_user_id, chat_id, kind, "
                "target_item_id, payload_json, status, created_at, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    action_id,
                    token,
                    owner_user_id,
                    chat_id,
                    kind.value,
                    target_item_id,
                    json.dumps(payload, sort_keys=True),
                    PendingStatus.PENDING.value,
                    now.isoformat(),
                    (now + timedelta(minutes=20)).isoformat(),
                    now.isoformat(),
                ),
            )
        return {"action_id": action_id, "token": token}

    def cancel_action(self, token: str, owner_user_id: int, chat_id: int) -> None:
        self._resolve_action(token, owner_user_id, chat_id, confirm=False)

    def cancel_latest(self, owner_user_id: int, chat_id: int) -> bool:
        now = _now().isoformat()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT id FROM pending_actions "
                "WHERE owner_user_id=? AND chat_id=? AND status=? "
                "ORDER BY created_at DESC LIMIT 1",
                (owner_user_id, chat_id, PendingStatus.PENDING.value),
            ).fetchone()
            if row is None:
                return False
            connection.execute(
                "UPDATE pending_actions SET status=?, updated_at=? WHERE id=?",
                (PendingStatus.CANCELLED.value, now, row["id"]),
            )
            return True

    def confirm_action(self, token: str, owner_user_id: int, chat_id: int) -> WardrobeItem | None:
        return self._resolve_action(token, owner_user_id, chat_id, confirm=True)

    def _resolve_action(
        self,
        token: str,
        owner_user_id: int,
        chat_id: int,
        *,
        confirm: bool,
    ) -> WardrobeItem | None:
        now = _now()
        result_item_id: str | None = None
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pending_actions "
                "WHERE callback_token=? AND owner_user_id=? AND chat_id=?",
                (token, owner_user_id, chat_id),
            ).fetchone()
            if row is None or row["status"] != PendingStatus.PENDING.value:
                raise ConfirmationError("confirmation is missing or already used")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute(
                    "UPDATE pending_actions SET status=?, updated_at=? WHERE id=?",
                    (PendingStatus.EXPIRED.value, now.isoformat(), row["id"]),
                )
                raise ConfirmationError("confirmation has expired")
            if not confirm:
                connection.execute(
                    "UPDATE pending_actions SET status=?, updated_at=? WHERE id=?",
                    (PendingStatus.CANCELLED.value, now.isoformat(), row["id"]),
                )
                return None

            payload = json.loads(row["payload_json"])
            kind = PendingActionKind(row["kind"])
            target_id = row["target_item_id"]
            if kind == PendingActionKind.ADD_ITEM:
                draft = WardrobeItemDraft.model_validate(payload)
                result_item_id = f"itm_{uuid.uuid4().hex}"
                connection.execute(
                    "INSERT INTO wardrobe_items("
                    "id, owner_user_id, name, category, color, roles_json, "
                    "attributes_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        result_item_id,
                        owner_user_id,
                        draft.name,
                        draft.category.value,
                        draft.color,
                        json.dumps(sorted(role.value for role in draft.roles)),
                        json.dumps(draft.attributes, sort_keys=True),
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            elif kind == PendingActionKind.UPDATE_ITEM:
                draft = WardrobeItemDraft.model_validate(payload)
                active = connection.execute(
                    "SELECT 1 FROM wardrobe_items "
                    "WHERE id=? AND owner_user_id=? AND deleted_at IS NULL",
                    (target_id, owner_user_id),
                ).fetchone()
                if active is None:
                    raise NotFoundError("item is no longer active")
                result_item_id = target_id
                connection.execute(
                    "UPDATE wardrobe_items SET name=?, category=?, color=?, roles_json=?, "
                    "attributes_json=?, updated_at=? WHERE id=? AND owner_user_id=?",
                    (
                        draft.name,
                        draft.category.value,
                        draft.color,
                        json.dumps(sorted(role.value for role in draft.roles)),
                        json.dumps(draft.attributes, sort_keys=True),
                        now.isoformat(),
                        target_id,
                        owner_user_id,
                    ),
                )
            elif kind == PendingActionKind.REMOVE_ITEM:
                result_item_id = target_id
                changed = connection.execute(
                    "UPDATE wardrobe_items SET deleted_at=?, updated_at=? "
                    "WHERE id=? AND owner_user_id=? AND deleted_at IS NULL",
                    (now.isoformat(), now.isoformat(), target_id, owner_user_id),
                ).rowcount
                if not changed:
                    raise NotFoundError("item is no longer active")
            elif kind == PendingActionKind.RESTORE_ITEM:
                result_item_id = target_id
                changed = connection.execute(
                    "UPDATE wardrobe_items SET deleted_at=NULL, updated_at=? "
                    "WHERE id=? AND owner_user_id=? AND deleted_at IS NOT NULL",
                    (now.isoformat(), target_id, owner_user_id),
                ).rowcount
                if not changed:
                    raise ConflictError("item is already active")

            connection.execute(
                "UPDATE pending_actions SET status=?, updated_at=? WHERE id=?",
                (PendingStatus.CONFIRMED.value, now.isoformat(), row["id"]),
            )
        if result_item_id is None:
            return None
        return self.get_item(owner_user_id, result_item_id, include_deleted=True)
