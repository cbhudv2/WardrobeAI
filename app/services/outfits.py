from __future__ import annotations

import json
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.database import Database
from app.domain.enums import PendingActionKind, PendingStatus, RecommendationStatus, WearRole
from app.domain.schemas import OutfitSelection, RecommendationDecision, WeatherSnapshot
from app.errors import ConfirmationError, ConflictError, CooldownError, NotFoundError
from app.services.wardrobe import WardrobeService


class OutfitService:
    def __init__(self, database: Database, wardrobe: WardrobeService) -> None:
        self.database = database
        self.wardrobe = wardrobe

    def forbidden_pairs(self, owner_user_id: int, target_date: date) -> set[tuple[str, str]]:
        start = target_date - timedelta(days=4)
        end = target_date - timedelta(days=1)
        rows = (
            self.database.connect()
            .execute(
                "SELECT top_item_id, bottom_item_id FROM worn_outfits "
                "WHERE owner_user_id=? AND worn_date BETWEEN ? AND ?",
                (owner_user_id, start.isoformat(), end.isoformat()),
            )
            .fetchall()
        )
        return {(row["top_item_id"], row["bottom_item_id"]) for row in rows}

    def recommendation_context(self, owner_user_id: int, target_date: date) -> dict[str, Any]:
        items = self.wardrobe.list_items(owner_user_id, limit=100)
        forbidden = self.forbidden_pairs(owner_user_id, target_date)
        return {
            "items": [item.model_dump(mode="json") for item in items],
            "forbidden_pairs": [
                {"top_id": top_id, "bottom_id": bottom_id}
                for top_id, bottom_id in sorted(forbidden)
            ],
        }

    def validate_selection(
        self,
        owner_user_id: int,
        target_date: date,
        selection: OutfitSelection,
    ) -> None:
        self.validate_item_roles(owner_user_id, selection)
        if (selection.top_id, selection.bottom_id) in self.forbidden_pairs(
            owner_user_id, target_date
        ):
            raise CooldownError(
                "top and bottom pairing was worn in the preceding four calendar days"
            )

    def validate_item_roles(self, owner_user_id: int, selection: OutfitSelection) -> None:
        required = {
            selection.top_id: WearRole.MAIN_TOP,
            selection.bottom_id: WearRole.BOTTOM,
            selection.footwear_id: WearRole.FOOTWEAR,
        }
        if selection.outerwear_id:
            required[selection.outerwear_id] = WearRole.OUTERWEAR
        for item_id, role in required.items():
            item = self.wardrobe.get_item(owner_user_id, item_id)
            if role not in item.roles:
                raise CooldownError(f"item {item_id} cannot be used as {role.value}")

    def validate_active_items(self, owner_user_id: int, selection: OutfitSelection) -> None:
        """Ensure a historical outfit references active items owned by the user.

        History records what was actually worn, so wardrobe recommendation roles do not
        apply here. An item classified as a mid-layer may still have been worn as the top.
        """
        item_ids = {
            selection.top_id,
            selection.bottom_id,
            selection.footwear_id,
        }
        if selection.outerwear_id:
            item_ids.add(selection.outerwear_id)
        for item_id in item_ids:
            self.wardrobe.get_item(owner_user_id, item_id)

    def draft_manual_worn(
        self,
        owner_user_id: int,
        chat_id: int,
        worn_date: date,
        local_today: date,
        selection: OutfitSelection,
        *,
        mood: str | None = None,
        occasion: str | None = None,
    ) -> str:
        if worn_date > local_today:
            raise ConfirmationError("a future outfit cannot be recorded as worn")
        self.validate_active_items(owner_user_id, selection)
        now = datetime.now(UTC)
        token = secrets.token_urlsafe(12)
        payload = {
            "worn_date": worn_date.isoformat(),
            "selection": selection.model_dump(mode="json"),
            "mood": mood,
            "occasion": occasion or "casual",
        }
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
                "payload_json, status, created_at, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"action_{uuid.uuid4().hex}",
                    token,
                    owner_user_id,
                    chat_id,
                    PendingActionKind.LOG_MANUAL_OUTFIT.value,
                    json.dumps(payload, sort_keys=True),
                    PendingStatus.PENDING.value,
                    now.isoformat(),
                    (now + timedelta(minutes=20)).isoformat(),
                    now.isoformat(),
                ),
            )
        return token

    def confirm_manual_worn(self, token: str, owner_user_id: int, chat_id: int) -> str:
        now = datetime.now(UTC)
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM pending_actions WHERE callback_token=? AND owner_user_id=? "
                "AND chat_id=? AND kind=?",
                (
                    token,
                    owner_user_id,
                    chat_id,
                    PendingActionKind.LOG_MANUAL_OUTFIT.value,
                ),
            ).fetchone()
            if row is None or row["status"] != PendingStatus.PENDING.value:
                raise ConfirmationError("outfit confirmation is missing or already used")
            if datetime.fromisoformat(row["expires_at"]) <= now:
                connection.execute(
                    "UPDATE pending_actions SET status=?, updated_at=? WHERE id=?",
                    (PendingStatus.EXPIRED.value, now.isoformat(), row["id"]),
                )
                raise ConfirmationError("outfit confirmation has expired")
            payload = json.loads(row["payload_json"])
            selection = OutfitSelection.model_validate(payload["selection"])
            self.validate_active_items(owner_user_id, selection)
            outfit_id = f"worn_{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO worn_outfits("
                "id, owner_user_id, worn_date, top_item_id, bottom_item_id, footwear_item_id, "
                "outerwear_item_id, mood, occasion, confirmed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outfit_id,
                    owner_user_id,
                    payload["worn_date"],
                    selection.top_id,
                    selection.bottom_id,
                    selection.footwear_id,
                    selection.outerwear_id,
                    payload.get("mood"),
                    payload.get("occasion"),
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE pending_actions SET status=?, updated_at=? WHERE id=?",
                (PendingStatus.CONFIRMED.value, now.isoformat(), row["id"]),
            )
            connection.execute(
                "UPDATE recommendations SET status=?, updated_at=? "
                "WHERE owner_user_id=? AND chat_id=? AND status=?",
                (
                    RecommendationStatus.DISMISSED.value,
                    now.isoformat(),
                    owner_user_id,
                    chat_id,
                    RecommendationStatus.PENDING.value,
                ),
            )
        return outfit_id

    def save_recommendation(
        self,
        owner_user_id: int,
        chat_id: int,
        target_date: date,
        decision: RecommendationDecision,
        weather: WeatherSnapshot,
        *,
        occasion: str = "casual software office",
        mood: str | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> str:
        return self.save_recommendations(
            owner_user_id,
            chat_id,
            target_date,
            [decision],
            weather,
            occasion=occasion,
            mood=mood,
            constraints=constraints,
        )[0]

    def save_recommendations(
        self,
        owner_user_id: int,
        chat_id: int,
        target_date: date,
        decisions: list[RecommendationDecision],
        weather: WeatherSnapshot,
        *,
        occasion: str = "casual software office",
        mood: str | None = None,
        constraints: dict[str, Any] | None = None,
    ) -> list[str]:
        if not decisions:
            raise ValueError("at least one recommendation is required")
        selections = [decision.selection for decision in decisions]
        for selection in selections:
            self.validate_selection(owner_user_id, target_date, selection)
        selection_keys = {(selection.top_id, selection.bottom_id) for selection in selections}
        if len(selection_keys) != len(selections):
            raise ConflictError("outfit options must be different")

        now = datetime.now(UTC)
        choice_group_id = f"choices_{uuid.uuid4().hex}"
        recommendation_ids = [f"rec_{uuid.uuid4().hex}" for _ in decisions]
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE recommendations SET status=?, updated_at=? "
                "WHERE chat_id=? AND owner_user_id=? AND status=?",
                (
                    RecommendationStatus.SUPERSEDED.value,
                    now.isoformat(),
                    chat_id,
                    owner_user_id,
                    RecommendationStatus.PENDING.value,
                ),
            )
            for recommendation_id, decision in zip(
                recommendation_ids, decisions, strict=True
            ):
                selection = decision.selection
                connection.execute(
                    "INSERT INTO recommendations("
                    "id, owner_user_id, chat_id, target_date, top_item_id, bottom_item_id, "
                    "footwear_item_id, outerwear_item_id, weather_json, occasion, mood, "
                    "constraints_json, rationale, styling_adjustment, status, created_at, "
                    "expires_at, updated_at, choice_group_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        recommendation_id,
                        owner_user_id,
                        chat_id,
                        target_date.isoformat(),
                        selection.top_id,
                        selection.bottom_id,
                        selection.footwear_id,
                        selection.outerwear_id,
                        weather.model_dump_json(),
                        occasion,
                        mood,
                        json.dumps(constraints or {}, sort_keys=True),
                        decision.reason,
                        decision.styling_adjustment,
                        RecommendationStatus.PENDING.value,
                        now.isoformat(),
                        (now + timedelta(days=3)).isoformat(),
                        now.isoformat(),
                        choice_group_id,
                    ),
                )
        return recommendation_ids

    def list_pending(self, owner_user_id: int, chat_id: int) -> list[Any]:
        return (
            self.database.connect()
            .execute(
                "SELECT * FROM recommendations WHERE owner_user_id=? AND chat_id=? AND status=? "
                "ORDER BY created_at DESC, id",
                (owner_user_id, chat_id, RecommendationStatus.PENDING.value),
            )
            .fetchall()
        )

    def get_pending(self, owner_user_id: int, chat_id: int) -> Any:
        rows = self.list_pending(owner_user_id, chat_id)
        if not rows:
            raise NotFoundError("there is no pending outfit recommendation")
        if len(rows) != 1:
            raise ConfirmationError("choose one of the outfit options first")
        return rows[0]

    def select_recommendation(
        self, recommendation_id: str, owner_user_id: int, chat_id: int
    ) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM recommendations WHERE id=? AND owner_user_id=? AND chat_id=?",
                (recommendation_id, owner_user_id, chat_id),
            ).fetchone()
            if row is None or row["status"] != RecommendationStatus.PENDING.value:
                raise ConfirmationError("outfit option is missing or no longer available")
            connection.execute(
                "UPDATE recommendations SET status=?, updated_at=? "
                "WHERE choice_group_id=? AND id<>? AND status=?",
                (
                    RecommendationStatus.DISMISSED.value,
                    now,
                    row["choice_group_id"],
                    recommendation_id,
                    RecommendationStatus.PENDING.value,
                ),
            )

    def dismiss_choice_group(self, choice_group_id: str, owner_user_id: int, chat_id: int) -> int:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            return connection.execute(
                "UPDATE recommendations SET status=?, updated_at=? "
                "WHERE choice_group_id=? AND owner_user_id=? AND chat_id=? AND status=?",
                (
                    RecommendationStatus.DISMISSED.value,
                    now,
                    choice_group_id,
                    owner_user_id,
                    chat_id,
                    RecommendationStatus.PENDING.value,
                ),
            ).rowcount

    def dismiss_pending(self, owner_user_id: int, chat_id: int) -> int:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            return connection.execute(
                "UPDATE recommendations SET status=?, updated_at=? "
                "WHERE owner_user_id=? AND chat_id=? AND status=?",
                (
                    RecommendationStatus.DISMISSED.value,
                    now,
                    owner_user_id,
                    chat_id,
                    RecommendationStatus.PENDING.value,
                ),
            ).rowcount

    def set_telegram_message_id(self, recommendation_id: str, message_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE recommendations SET telegram_message_id=?, updated_at=? WHERE id=?",
                (message_id, datetime.now(UTC).isoformat(), recommendation_id),
            )

    def choice_group_id(self, recommendation_id: str, owner_user_id: int, chat_id: int) -> str:
        row = self.database.connect().execute(
            "SELECT choice_group_id FROM recommendations "
            "WHERE id=? AND owner_user_id=? AND chat_id=?",
            (recommendation_id, owner_user_id, chat_id),
        ).fetchone()
        if row is None or not row["choice_group_id"]:
            raise NotFoundError("outfit choices were not found")
        return str(row["choice_group_id"])

    def confirm_worn(
        self,
        owner_user_id: int,
        chat_id: int,
        local_today: date,
        *,
        recommendation_id: str | None = None,
    ) -> str:
        now = datetime.now(UTC)
        with self.database.transaction() as connection:
            if recommendation_id:
                row = connection.execute(
                    "SELECT * FROM recommendations WHERE id=? AND owner_user_id=? AND chat_id=?",
                    (recommendation_id, owner_user_id, chat_id),
                ).fetchone()
            else:
                rows = connection.execute(
                    "SELECT * FROM recommendations "
                    "WHERE owner_user_id=? AND chat_id=? AND status=?",
                    (owner_user_id, chat_id, RecommendationStatus.PENDING.value),
                ).fetchall()
                if len(rows) != 1:
                    raise ConfirmationError("there is no single unambiguous pending recommendation")
                row = rows[0]
            if row is None or row["status"] != RecommendationStatus.PENDING.value:
                raise ConfirmationError("recommendation is missing or no longer pending")
            target_date = date.fromisoformat(row["target_date"])
            if target_date > local_today:
                raise ConfirmationError("a future outfit cannot be recorded as worn")
            if target_date < local_today - timedelta(days=1):
                raise ConfirmationError("this recommendation is too old; specify the worn date")
            outfit_id = f"worn_{uuid.uuid4().hex}"
            connection.execute(
                "INSERT INTO worn_outfits("
                "id, owner_user_id, worn_date, top_item_id, bottom_item_id, footwear_item_id, "
                "outerwear_item_id, mood, occasion, recommendation_id, confirmed_at, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    outfit_id,
                    owner_user_id,
                    target_date.isoformat(),
                    row["top_item_id"],
                    row["bottom_item_id"],
                    row["footwear_item_id"],
                    row["outerwear_item_id"],
                    row["mood"],
                    row["occasion"],
                    row["id"],
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            connection.execute(
                "UPDATE recommendations SET status=?, updated_at=? WHERE id=?",
                (RecommendationStatus.CONFIRMED.value, now.isoformat(), row["id"]),
            )
            connection.execute(
                "UPDATE recommendations SET status=?, updated_at=? "
                "WHERE choice_group_id=? AND id<>? AND status=?",
                (
                    RecommendationStatus.DISMISSED.value,
                    now.isoformat(),
                    row["choice_group_id"],
                    row["id"],
                    RecommendationStatus.PENDING.value,
                ),
            )
        return outfit_id

    def history(self, owner_user_id: int, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = (
            self.database.connect()
            .execute(
                "SELECT worn_date, top_item_id, bottom_item_id, footwear_item_id, "
                "outerwear_item_id, "
                "mood, occasion FROM worn_outfits WHERE owner_user_id=? "
                "ORDER BY worn_date DESC, created_at DESC LIMIT ?",
                (owner_user_id, max(1, min(limit, 100))),
            )
            .fetchall()
        )
        return [dict(row) for row in rows]
