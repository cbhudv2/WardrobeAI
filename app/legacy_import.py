from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.database import Database
from app.domain.enums import ItemCategory, WearRole

TOP_CATEGORY_MAP: dict[str, ItemCategory] = {
    "Quarter-Zip": ItemCategory.QUARTER_ZIP,
    "Waffle Quarter-Zip": ItemCategory.QUARTER_ZIP,
    "Crewneck": ItemCategory.CREWNECK,
    "Hoodie": ItemCategory.HOODIE,
    "Zip-Up Hoodie": ItemCategory.HOODIE,
    "Polo Shirt": ItemCategory.POLO,
    "T-Shirt": ItemCategory.T_SHIRT,
    "Lightweight Jacket / Windbreaker": ItemCategory.LIGHT_JACKET,
    "Puffer Jacket": ItemCategory.PUFFER,
    "Plush Zip Jacket": ItemCategory.LIGHT_JACKET,
}


def _read_json(path: Path) -> tuple[dict[str, Any], str]:
    raw = path.read_bytes()
    return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _top_roles(item_id: str) -> list[str]:
    if item_id in {"top_09", "top_12", "top_17"}:
        return [WearRole.OUTERWEAR.value]
    if item_id == "top_06":
        return [WearRole.LIGHT_LAYER.value, WearRole.MAIN_TOP.value]
    return [WearRole.MAIN_TOP.value]


def import_legacy(
    database: Database,
    wardrobe_path: Path,
    history_path: Path,
    owner_user_id: int,
    private_chat_id: int,
) -> dict[str, int]:
    wardrobe_document, wardrobe_digest = _read_json(wardrobe_path)
    history_document, history_digest = _read_json(history_path)
    now = datetime.now(UTC).isoformat()

    counts = {"items": 0, "outfits": 0}
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO telegram_users("
            "user_id, private_chat_id, is_authorized, created_at, updated_at) "
            "VALUES (?, ?, 1, ?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET private_chat_id=excluded.private_chat_id, "
            "updated_at=excluded.updated_at",
            (owner_user_id, private_chat_id, now, now),
        )

        wardrobe_done = connection.execute(
            "SELECT 1 FROM legacy_imports WHERE source_name=? AND source_sha256=?",
            (wardrobe_path.name, wardrobe_digest),
        ).fetchone()
        if not wardrobe_done:
            wardrobe = wardrobe_document["wardrobe"]
            normalized: list[dict[str, Any]] = []
            for raw in wardrobe["tops_and_outerwear"]:
                normalized.append(
                    {
                        "id": raw["id"],
                        "name": raw["name"],
                        "category": TOP_CATEGORY_MAP.get(raw["category"], ItemCategory.OTHER).value,
                        "color": raw["color"],
                        "roles": _top_roles(raw["id"]),
                        "attributes": {"legacy_category": raw["category"]},
                        "section": "tops_and_outerwear",
                    }
                )
            for raw in wardrobe["bottoms"]["jeans"]:
                normalized.append(
                    {
                        "id": raw["id"],
                        "name": raw["label"],
                        "category": ItemCategory.JEANS.value,
                        "color": raw["wash"],
                        "roles": [WearRole.BOTTOM.value],
                        "attributes": {"wash": raw["wash"]},
                        "section": "bottoms.jeans",
                    }
                )
            for raw in wardrobe["footwear"]:
                normalized.append(
                    {
                        "id": raw["id"],
                        "name": raw["model"],
                        "category": ItemCategory.SHOES.value,
                        "color": raw["color"],
                        "roles": [WearRole.FOOTWEAR.value],
                        "attributes": {},
                        "section": "footwear",
                    }
                )

            for item in normalized:
                try:
                    connection.execute(
                        "INSERT INTO wardrobe_items("
                        "id, owner_user_id, name, category, color, roles_json, attributes_json, "
                        "legacy_section, created_at, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            item["id"],
                            owner_user_id,
                            item["name"],
                            item["category"],
                            item["color"],
                            json.dumps(sorted(item["roles"])),
                            json.dumps(item["attributes"], sort_keys=True),
                            item["section"],
                            now,
                            now,
                        ),
                    )
                except Exception as exc:
                    raise ValueError(
                        f"legacy item ID conflicts with existing data: {item['id']}"
                    ) from exc
            connection.execute(
                "INSERT INTO legacy_imports VALUES (?, ?, ?, ?)",
                (wardrobe_path.name, wardrobe_digest, now, len(normalized)),
            )
            counts["items"] = len(normalized)

        history_done = connection.execute(
            "SELECT 1 FROM legacy_imports WHERE source_name=? AND source_sha256=?",
            (history_path.name, history_digest),
        ).fetchone()
        if not history_done:
            history = history_document["history"]
            for raw in history:
                legacy_key = _canonical_hash(raw)
                outfit_id = f"worn_{legacy_key[:24]}"
                connection.execute(
                    "INSERT INTO worn_outfits("
                    "id, owner_user_id, worn_date, top_item_id, bottom_item_id, footwear_item_id, "
                    "outerwear_item_id, mood, occasion, confirmed_at, legacy_key, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        outfit_id,
                        owner_user_id,
                        raw["date"],
                        raw["top"],
                        raw["bottom"],
                        raw["shoes"],
                        raw.get("outerwear"),
                        raw.get("mood"),
                        raw.get("occasion"),
                        now,
                        legacy_key,
                        now,
                    ),
                )
            connection.execute(
                "INSERT INTO legacy_imports VALUES (?, ?, ?, ?)",
                (history_path.name, history_digest, now, len(history)),
            )
            counts["outfits"] = len(history)
    return counts
