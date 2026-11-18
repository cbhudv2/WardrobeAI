from __future__ import annotations

from typing import Any

from app.services.wardrobe import WardrobeService


class ShoppingService:
    def __init__(self, wardrobe: WardrobeService) -> None:
        self.wardrobe = wardrobe

    def context_for_evaluation(
        self, owner_user_id: int, garment_description: str
    ) -> dict[str, Any]:
        description = garment_description.strip()
        if not description:
            raise ValueError("garment description is required")
        if len(description) > 1500:
            raise ValueError("garment description is too long")
        items = self.wardrobe.list_items(owner_user_id, limit=100)
        return {
            "garment_description": description,
            "owned_items": [item.model_dump(mode="json") for item in items],
            "market_data_available": False,
            "rules": [
                "Do not claim current price, stock, availability, or product specifications.",
                "Evaluate overlap and fit with the wardrobe, plus at least three outfits.",
                "Buying nothing is a valid conclusion.",
            ],
        }
