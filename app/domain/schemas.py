from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.enums import ItemCategory, WearRole


class WardrobeItemDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    category: ItemCategory
    color: str = Field(min_length=1, max_length=120)
    roles: set[WearRole] = Field(min_length=1)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("name", "color")
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def validate_category_roles(self) -> WardrobeItemDraft:
        if self.category == ItemCategory.JEANS and WearRole.BOTTOM not in self.roles:
            raise ValueError("jeans must have the bottom role")
        if self.category == ItemCategory.SHOES and WearRole.FOOTWEAR not in self.roles:
            raise ValueError("shoes must have the footwear role")
        if WearRole.BOTTOM in self.roles and WearRole.FOOTWEAR in self.roles:
            raise ValueError("an item cannot be both a bottom and footwear")
        return self


class WardrobeItemPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: ItemCategory | None = None
    color: str | None = Field(default=None, min_length=1, max_length=120)
    roles: set[WearRole] | None = Field(default=None, min_length=1)
    attributes: dict[str, Any] | None = None

    @model_validator(mode="after")
    def require_change(self) -> WardrobeItemPatch:
        if not self.model_fields_set:
            raise ValueError("at least one field must be supplied")
        return self


class WardrobeItem(BaseModel):
    id: str
    owner_user_id: int
    name: str
    category: ItemCategory
    color: str
    roles: set[WearRole]
    attributes: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


class OutfitSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    top_id: str
    bottom_id: str
    footwear_id: str
    outerwear_id: str | None = None


class RecommendationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection: OutfitSelection
    reason: str = Field(min_length=1, max_length=280)
    styling_adjustment: str | None = Field(default=None, max_length=160)


class WeatherSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_date: date
    source: str
    fetched_at: datetime
    morning_temperature_c: float
    morning_feels_like_c: float
    afternoon_temperature_c: float
    afternoon_feels_like_c: float
    max_precipitation_probability: int
    precipitation_mm: float
    max_wind_kph: float
    max_gust_kph: float
    condition: str


class ToolResult(BaseModel):
    ok: bool
    code: str
    message: str
    data: dict[str, Any] = Field(default_factory=dict)
