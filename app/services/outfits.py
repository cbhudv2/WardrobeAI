from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from app.database import Database
from app.domain.enums import RecommendationStatus, WearRole
from app.domain.schemas import OutfitSelection, RecommendationDecision, WeatherSnapshot
from app.errors import ConfirmationError, CooldownError, NotFoundError
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
        if (selection.top_id, selection.bottom_id) in self.forbidden_pairs(
            owner_user_id, target_date
        ):
            raise CooldownError(
                "top and bottom pairing was worn in the preceding four calendar days"
            )

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
        self.validate_selection(owner_user_id, target_date, decision.selection)
        now = datetime.now(UTC)
        recommendation_id = f"rec_{uuid.uuid4().hex}"
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
            selection = decision.selection
            connection.execute(
                "INSERT INTO recommendations("
                "id, owner_user_id, chat_id, target_date, top_item_id, bottom_item_id, "
                "footwear_item_id, outerwear_item_id, weather_json, occasion, mood, "
                "constraints_json, "
                "rationale, styling_adjustment, status, created_at, expires_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                ),
            )
        return recommendation_id

    def get_pending(self, owner_user_id: int, chat_id: int) -> Any:
        row = (
            self.database.connect()
            .execute(
                "SELECT * FROM recommendations WHERE owner_user_id=? AND chat_id=? AND status=?",
                (owner_user_id, chat_id, RecommendationStatus.PENDING.value),
            )
            .fetchone()
        )
        if row is None:
            raise NotFoundError("there is no pending outfit recommendation")
        return row

    def set_telegram_message_id(self, recommendation_id: str, message_id: int) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE recommendations SET telegram_message_id=?, updated_at=? WHERE id=?",
                (message_id, datetime.now(UTC).isoformat(), recommendation_id),
            )

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
