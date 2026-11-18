from datetime import UTC, date, datetime

from app.domain.schemas import OutfitSelection, RecommendationDecision, WeatherSnapshot
from app.llm.client import ModelToolCall
from app.llm.tools import ToolDispatcher
from app.services.outfits import OutfitService
from app.services.wardrobe import WardrobeService


def weather(target: date) -> WeatherSnapshot:
    return WeatherSnapshot(
        target_date=target,
        source="test",
        fetched_at=datetime.now(UTC),
        morning_temperature_c=12,
        morning_feels_like_c=11,
        afternoon_temperature_c=18,
        afternoon_feels_like_c=18,
        max_precipitation_probability=10,
        precipitation_mm=0,
        max_wind_kph=8,
        max_gust_kph=14,
        condition="clear",
    )


def dispatcher(wardrobe: WardrobeService, outfits: OutfitService) -> ToolDispatcher:
    return ToolDispatcher(
        llm=None,  # type: ignore[arg-type]
        wardrobe=wardrobe,
        outfits=outfits,
        weather=None,  # type: ignore[arg-type]
        shopping=None,  # type: ignore[arg-type]
        timezone_name="Europe/Podgorica",
    )


async def test_wardrobe_list_is_grouped_and_hides_internal_ids(
    wardrobe: WardrobeService,
    outfits: OutfitService,
) -> None:
    result = await dispatcher(wardrobe, outfits).execute(
        ModelToolCall("wardrobe_search", {}), 1, 10, date(2026, 11, 18)
    )

    assert "TOPS\n" in result.text
    assert "OUTERWEAR\n" in result.text
    assert "JEANS\n" in result.text
    assert "SHOES\n" in result.text
    assert "jeans_02" not in result.text
    assert "Jeans #2 - Classic Mid-Blue Denim (Slim Taper)" in result.text


async def test_recent_history_is_compact_and_uses_names(
    wardrobe: WardrobeService,
    outfits: OutfitService,
) -> None:
    result = await dispatcher(wardrobe, outfits).execute(
        ModelToolCall("outfit_history", {}), 1, 10, date(2026, 11, 18)
    )

    assert result.text.startswith("RECENTLY WORN")
    assert "top_" not in result.text
    assert "jeans_" not in result.text
    assert result.text.count("\n\n") == 5


def test_recommendation_is_short_and_uses_full_jeans_name(
    wardrobe: WardrobeService,
    outfits: OutfitService,
) -> None:
    target = date(2026, 11, 18)
    decision = RecommendationDecision(
        selection=OutfitSelection(
            top_id="top_02",
            bottom_id="jeans_02",
            footwear_id="shoe_01",
        ),
        reason="The light top and mid-blue denim create clean contrast.",
    )
    result = dispatcher(wardrobe, outfits)._format_recommendation(
        1,
        target,
        decision,
        weather(target),
        "office",
    )

    assert "Jeans #2 - Classic Mid-Blue Denim (Slim Taper)" in result
    assert "jeans_02" not in result
    assert len(result) < 700
