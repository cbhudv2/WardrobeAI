from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from pydantic import BaseModel, Field

from app.domain.enums import ItemCategory, WearRole
from app.domain.schemas import RecommendationDecision, WardrobeItemDraft, WardrobeItemPatch
from app.errors import NotFoundError
from app.llm.client import MistralClient, ModelToolCall
from app.llm.prompts import OUTFIT_DECISION_PROMPT, SHOPPING_PROMPT, SYSTEM_PROMPT
from app.services.outfits import OutfitService
from app.services.shopping import ShoppingService
from app.services.wardrobe import WardrobeService
from app.services.weather import WeatherService


@dataclass(frozen=True)
class DispatchResponse:
    text: str
    confirmation_token: str | None = None
    confirmation_kind: str | None = None
    recommendation_id: str | None = None


class IntentEnvelope(BaseModel):
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    response: str = ""


def _function(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
        },
    }


TOOL_SCHEMAS = [
    _function(
        "wardrobe_add",
        "Create a confirmation draft for a fully described new wardrobe item.",
        {
            "name": {"type": "string"},
            "category": {"type": "string", "enum": [value.value for value in ItemCategory]},
            "color": {"type": "string"},
            "roles": {
                "type": "array",
                "items": {"type": "string", "enum": [value.value for value in WearRole]},
                "minItems": 1,
            },
            "attributes": {"type": "object"},
        },
        ["name", "category", "color", "roles"],
    ),
    _function(
        "wardrobe_search",
        "List or search active wardrobe items.",
        {
            "query": {"type": ["string", "null"]},
            "category": {"type": ["string", "null"]},
            "role": {"type": ["string", "null"], "enum": [*[r.value for r in WearRole], None]},
        },
        [],
    ),
    _function(
        "wardrobe_get",
        "View one wardrobe item by exact stable ID.",
        {"item_id": {"type": "string"}},
        ["item_id"],
    ),
    _function(
        "wardrobe_update",
        "Create a confirmation draft to correct one exact wardrobe item.",
        {
            "item_id": {"type": "string"},
            "patch": {"type": "object", "minProperties": 1},
        },
        ["item_id", "patch"],
    ),
    _function(
        "wardrobe_remove",
        "Create the mandatory confirmation draft to remove one exact item.",
        {"item_id": {"type": "string"}},
        ["item_id"],
    ),
    _function(
        "outfit_recommend",
        "Recommend an outfit. Omit target_date when the user gave no date.",
        {
            "target_date": {"type": ["string", "null"], "format": "date"},
            "mood": {"type": ["string", "null"]},
            "occasion": {"type": ["string", "null"]},
            "constraints": {"type": ["string", "null"]},
        },
        [],
    ),
    _function(
        "outfit_refine",
        "Replace the pending outfit based on a requested change.",
        {"request": {"type": "string"}},
        ["request"],
    ),
    _function(
        "outfit_confirm_worn",
        "Confirm that the single active pending outfit was actually worn.",
        {},
        [],
    ),
    _function(
        "outfit_history",
        "Read recently confirmed worn outfits.",
        {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
        [],
    ),
    _function(
        "purchase_evaluate",
        "Evaluate a garment the user may buy using only user-provided facts.",
        {"description": {"type": "string"}},
        ["description"],
    ),
    _function(
        "purchase_suggest",
        "Suggest zero to three wardrobe purchases only after an explicit request.",
        {"request": {"type": "string"}},
        ["request"],
    ),
]


class ToolDispatcher:
    def __init__(
        self,
        llm: MistralClient,
        wardrobe: WardrobeService,
        outfits: OutfitService,
        weather: WeatherService,
        shopping: ShoppingService,
        timezone_name: str,
    ) -> None:
        self.llm = llm
        self.wardrobe = wardrobe
        self.outfits = outfits
        self.weather = weather
        self.shopping = shopping
        self.timezone_name = timezone_name

    async def handle(
        self,
        owner_user_id: int,
        chat_id: int,
        user_text: str,
        local_today: date,
    ) -> DispatchResponse:
        inventory = self.wardrobe.list_items(owner_user_id, limit=100)
        inventory_text = "\n".join(
            f"{item.id}: {item.name}; category={item.category.value}; color={item.color}; "
            f"roles={','.join(sorted(role.value for role in item.roles))}"
            for item in inventory
        )
        pending_text = "none"
        try:
            pending = self.outfits.get_pending(owner_user_id, chat_id)
            pending_text = (
                f"id={pending['id']}, target_date={pending['target_date']}, "
                f"top={pending['top_item_id']}, bottom={pending['bottom_item_id']}, "
                f"shoes={pending['footwear_item_id']}, outerwear={pending['outerwear_item_id']}"
            )
        except NotFoundError:
            pass

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "system",
                "content": (
                    f"Local date: {local_today.isoformat()} ({self.timezone_name}).\n"
                    f"Active pending recommendation: {pending_text}.\n"
                    f"Active wardrobe:\n{inventory_text}"
                ),
            },
            {"role": "user", "content": user_text[:4000]},
        ]
        if self.llm.supports_function_calling:
            reply = await self.llm.complete(messages, tools=TOOL_SCHEMAS)
            if not reply.tool_calls:
                return DispatchResponse(reply.text or "Please rephrase that request.")
            call = reply.tool_calls[0]
        else:
            fallback_messages = list(messages)
            fallback_messages[0] = {
                "role": "system",
                "content": SYSTEM_PROMPT
                + "\nReturn an IntentEnvelope naming one available tool and its arguments, "
                "or a response.",
            }
            envelope = await self.llm.complete_structured(
                fallback_messages,
                IntentEnvelope,
                schema_name="intent_envelope",
            )
            if not envelope.tool_name:
                return DispatchResponse(envelope.response or "Please rephrase that request.")
            call = ModelToolCall(envelope.tool_name, envelope.arguments)
        return await self.execute(call, owner_user_id, chat_id, local_today)

    async def execute(
        self,
        call: ModelToolCall,
        owner_user_id: int,
        chat_id: int,
        local_today: date,
    ) -> DispatchResponse:
        arguments = call.arguments
        if call.name == "wardrobe_add":
            draft = WardrobeItemDraft.model_validate(arguments)
            action = self.wardrobe.draft_add(owner_user_id, chat_id, draft)
            return DispatchResponse(
                text=f"Add {draft.name} ({draft.color}, {draft.category.value})?",
                confirmation_token=action["token"],
                confirmation_kind="change",
            )
        if call.name == "wardrobe_search":
            role = WearRole(arguments["role"]) if arguments.get("role") else None
            items = self.wardrobe.list_items(
                owner_user_id,
                query=arguments.get("query"),
                category=arguments.get("category"),
                role=role,
            )
            if not items:
                return DispatchResponse("No matching active wardrobe items.")
            lines = [f"• {item.name} — {item.color} (`{item.id}`)" for item in items]
            return DispatchResponse("\n".join(lines))
        if call.name == "wardrobe_get":
            item = self.wardrobe.get_item(owner_user_id, arguments["item_id"])
            roles = ", ".join(sorted(role.value for role in item.roles))
            return DispatchResponse(
                f"{item.name}\nID: `{item.id}`\nCategory: {item.category.value}\n"
                f"Color: {item.color}\nRoles: {roles}"
            )
        if call.name == "wardrobe_update":
            patch = WardrobeItemPatch.model_validate(arguments["patch"])
            action = self.wardrobe.draft_update(owner_user_id, chat_id, arguments["item_id"], patch)
            item = self.wardrobe.get_item(owner_user_id, arguments["item_id"])
            return DispatchResponse(
                f"Apply the proposed correction to {item.name}?",
                confirmation_token=action["token"],
                confirmation_kind="change",
            )
        if call.name == "wardrobe_remove":
            item = self.wardrobe.get_item(owner_user_id, arguments["item_id"])
            action = self.wardrobe.draft_remove(owner_user_id, chat_id, item.id)
            return DispatchResponse(
                f"Remove {item.name}? It will be soft-deleted and can be restored.",
                confirmation_token=action["token"],
                confirmation_kind="remove",
            )
        if call.name == "outfit_recommend":
            target = self._target_date(arguments.get("target_date"), local_today)
            return await self.recommend(
                owner_user_id,
                chat_id,
                target,
                mood=arguments.get("mood"),
                occasion=arguments.get("occasion") or "casual software office",
                constraints=arguments.get("constraints"),
            )
        if call.name == "outfit_refine":
            pending = self.outfits.get_pending(owner_user_id, chat_id)
            return await self.recommend(
                owner_user_id,
                chat_id,
                date.fromisoformat(pending["target_date"]),
                mood=pending["mood"],
                occasion=pending["occasion"],
                constraints=arguments["request"],
            )
        if call.name == "outfit_confirm_worn":
            outfit_id = self.outfits.confirm_worn(owner_user_id, chat_id, local_today)
            return DispatchResponse(f"Recorded as worn. History entry: `{outfit_id}`.")
        if call.name == "outfit_history":
            return self._format_history(owner_user_id, int(arguments.get("limit", 10)))
        if call.name in {"purchase_evaluate", "purchase_suggest"}:
            description = arguments.get("description") or arguments.get("request") or ""
            context = self.shopping.context_for_evaluation(owner_user_id, description)
            reply = await self.llm.complete(
                [
                    {"role": "system", "content": SYSTEM_PROMPT + "\n" + SHOPPING_PROMPT},
                    {"role": "user", "content": json.dumps(context, default=str)},
                ],
                max_tokens=1000,
            )
            return DispatchResponse(reply.text)
        raise ValueError(f"unsupported tool: {call.name}")

    async def recommend(
        self,
        owner_user_id: int,
        chat_id: int,
        target_date: date,
        *,
        mood: str | None,
        occasion: str,
        constraints: str | None,
    ) -> DispatchResponse:
        weather = await self.weather.fetch(target_date)
        context = self.outfits.recommendation_context(owner_user_id, target_date)
        decision = await self.llm.complete_structured(
            [
                {"role": "system", "content": SYSTEM_PROMPT + "\n" + OUTFIT_DECISION_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "target_date": target_date.isoformat(),
                            "occasion": occasion,
                            "mood": mood,
                            "constraints": constraints,
                            "weather": weather.model_dump(mode="json"),
                            **context,
                        },
                        default=str,
                    ),
                },
            ],
            RecommendationDecision,
            schema_name="outfit_recommendation",
        )
        recommendation_id = self.outfits.save_recommendation(
            owner_user_id,
            chat_id,
            target_date,
            decision,
            weather,
            occasion=occasion,
            mood=mood,
            constraints={"request": constraints} if constraints else {},
        )
        return DispatchResponse(
            self._format_recommendation(owner_user_id, target_date, decision, weather, occasion),
            recommendation_id=recommendation_id,
        )

    @staticmethod
    def _target_date(raw: str | None, local_today: date) -> date:
        if not raw:
            return local_today + timedelta(days=1)
        return date.fromisoformat(raw)

    def _format_recommendation(
        self,
        owner_user_id: int,
        target_date: date,
        decision: RecommendationDecision,
        weather: Any,
        occasion: str,
    ) -> str:
        selection = decision.selection
        top = self.wardrobe.get_item(owner_user_id, selection.top_id).name
        bottom = self.wardrobe.get_item(owner_user_id, selection.bottom_id).name
        shoes = self.wardrobe.get_item(owner_user_id, selection.footwear_id).name
        outerwear = (
            self.wardrobe.get_item(owner_user_id, selection.outerwear_id).name
            if selection.outerwear_id
            else "Not needed"
        )
        adjustment = (
            f"\n\nStyling adjustment: {decision.styling_adjustment}"
            if decision.styling_adjustment
            else ""
        )
        return (
            f"Outfit for {target_date.strftime('%A, %d %B')} — {occasion}\n\n"
            f"Top: {top}\nJeans: {bottom}\nSneakers: {shoes}\nOuterwear: {outerwear}\n\n"
            f"Weather: {weather.morning_temperature_c}°C in the morning, "
            f"{weather.afternoon_temperature_c}°C later; feels like "
            f"{weather.morning_feels_like_c}°C early; "
            f"{weather.max_precipitation_probability}% max rain chance, "
            f"winds up to {weather.max_wind_kph} km/h ({weather.condition}).\n\n"
            f"Why it works: {decision.reason}{adjustment}\n\n"
            "Use “I wore it” or the button below only after you actually wear this outfit."
        )

    def _format_history(self, owner_user_id: int, limit: int) -> DispatchResponse:
        rows = self.outfits.history(owner_user_id, limit=limit)
        if not rows:
            return DispatchResponse("No confirmed worn outfits yet.")
        lines = []
        for row in rows:
            top = self.wardrobe.get_item(owner_user_id, row["top_item_id"], include_deleted=True)
            bottom = self.wardrobe.get_item(
                owner_user_id, row["bottom_item_id"], include_deleted=True
            )
            lines.append(f"• {row['worn_date']}: {top.name} + {bottom.name} ({row['occasion']})")
        return DispatchResponse("Recently worn:\n" + "\n".join(lines))
