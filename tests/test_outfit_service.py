from datetime import date

import pytest

from app.domain.schemas import OutfitSelection, RecommendationDecision, WeatherSnapshot
from app.errors import ConfirmationError, CooldownError
from app.services.outfits import OutfitService


def weather(target: date) -> WeatherSnapshot:
    from datetime import UTC, datetime

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


def test_cooldown_uses_preceding_four_calendar_days(outfits: OutfitService) -> None:
    target = date(2026, 10, 10)
    assert ("top_04", "jeans_03") in outfits.forbidden_pairs(1, target)
    assert ("top_14", "jeans_03") not in outfits.forbidden_pairs(1, target)

    with pytest.raises(CooldownError):
        outfits.validate_selection(
            1,
            target,
            OutfitSelection(
                top_id="top_04",
                bottom_id="jeans_03",
                footwear_id="shoe_04",
                outerwear_id=None,
            ),
        )


def test_recommendation_is_logged_only_after_confirmation(outfits: OutfitService) -> None:
    target = date(2026, 11, 18)
    recommendation_id = outfits.save_recommendation(
        1,
        10,
        target,
        RecommendationDecision(
            selection=OutfitSelection(
                top_id="top_02",
                bottom_id="jeans_03",
                footwear_id="shoe_01",
                outerwear_id=None,
            ),
            reason="Clean contrast and practical proportions.",
        ),
        weather(target),
    )
    connection = outfits.database.connect()
    assert connection.execute("SELECT count(*) FROM worn_outfits").fetchone()[0] == 7

    outfit_id = outfits.confirm_worn(1, 10, target, recommendation_id=recommendation_id)
    assert outfit_id.startswith("worn_")
    assert connection.execute("SELECT count(*) FROM worn_outfits").fetchone()[0] == 8

    with pytest.raises(ConfirmationError):
        outfits.confirm_worn(1, 10, target, recommendation_id=recommendation_id)


def test_two_options_require_selection_and_only_chosen_option_remains_pending(
    outfits: OutfitService,
) -> None:
    target = date(2026, 11, 18)
    decisions = [
        RecommendationDecision(
            selection=OutfitSelection(
                top_id="top_02",
                bottom_id="jeans_03",
                footwear_id="shoe_01",
            ),
            reason="Clean and relaxed.",
        ),
        RecommendationDecision(
            selection=OutfitSelection(
                top_id="top_03",
                bottom_id="jeans_02",
                footwear_id="shoe_02",
            ),
            reason="Sportier alternative.",
        ),
    ]
    recommendation_ids = outfits.save_recommendations(
        1, 10, target, decisions, weather(target)
    )

    with pytest.raises(ConfirmationError):
        outfits.confirm_worn(1, 10, target)

    outfits.select_recommendation(recommendation_ids[1], 1, 10)
    pending = outfits.list_pending(1, 10)
    assert [row["id"] for row in pending] == [recommendation_ids[1]]

    outfits.confirm_worn(1, 10, target, recommendation_id=recommendation_ids[1])
    statuses = {
        row["id"]: row["status"]
        for row in outfits.database.connect()
        .execute(
            "SELECT id, status FROM recommendations WHERE id IN (?, ?)",
            recommendation_ids,
        )
        .fetchall()
    }
    assert statuses[recommendation_ids[0]] == "dismissed"
    assert statuses[recommendation_ids[1]] == "confirmed"


def test_dismissing_choice_group_records_nothing(outfits: OutfitService) -> None:
    target = date(2026, 11, 18)
    decisions = [
        RecommendationDecision(
            selection=OutfitSelection(
                top_id="top_02",
                bottom_id="jeans_03",
                footwear_id="shoe_01",
            ),
            reason="First option.",
        ),
        RecommendationDecision(
            selection=OutfitSelection(
                top_id="top_03",
                bottom_id="jeans_02",
                footwear_id="shoe_02",
            ),
            reason="Second option.",
        ),
    ]
    recommendation_ids = outfits.save_recommendations(
        1, 10, target, decisions, weather(target)
    )
    group_id = outfits.choice_group_id(recommendation_ids[0], 1, 10)
    worn_before = outfits.database.connect().execute(
        "SELECT count(*) FROM worn_outfits"
    ).fetchone()[0]

    assert outfits.dismiss_choice_group(group_id, 1, 10) == 2
    assert outfits.list_pending(1, 10) == []
    assert (
        outfits.database.connect().execute("SELECT count(*) FROM worn_outfits").fetchone()[0]
        == worn_before
    )


def test_manual_outfit_requires_confirmation_and_dismisses_recommendation(
    outfits: OutfitService,
) -> None:
    target = date(2026, 11, 18)
    outfits.save_recommendation(
        1,
        10,
        target,
        RecommendationDecision(
            selection=OutfitSelection(
                top_id="top_02",
                bottom_id="jeans_03",
                footwear_id="shoe_01",
            ),
            reason="Clean contrast.",
        ),
        weather(target),
    )
    manual = OutfitSelection(
        top_id="top_03",
        bottom_id="jeans_02",
        footwear_id="shoe_02",
    )
    token = outfits.draft_manual_worn(1, 10, target, target, manual, occasion="office")

    assert (
        outfits.database.connect().execute("SELECT count(*) FROM worn_outfits").fetchone()[0] == 7
    )

    outfits.confirm_manual_worn(token, 1, 10)
    connection = outfits.database.connect()
    recorded = connection.execute(
        "SELECT * FROM worn_outfits ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    pending = connection.execute(
        "SELECT count(*) FROM recommendations WHERE status='pending'"
    ).fetchone()[0]

    assert recorded["top_item_id"] == "top_03"
    assert recorded["bottom_item_id"] == "jeans_02"
    assert recorded["footwear_item_id"] == "shoe_02"
    assert pending == 0


def test_manual_outfit_rejects_future_date(outfits: OutfitService) -> None:
    selection = OutfitSelection(
        top_id="top_03",
        bottom_id="jeans_02",
        footwear_id="shoe_02",
    )
    with pytest.raises(ConfirmationError):
        outfits.draft_manual_worn(
            1,
            10,
            date(2026, 11, 19),
            date(2026, 11, 18),
            selection,
        )


def test_manual_outfit_records_actual_use_despite_recommendation_role(
    outfits: OutfitService,
) -> None:
    target = date(2026, 11, 18)
    selection = OutfitSelection(
        top_id="top_09",
        bottom_id="jeans_02",
        footwear_id="shoe_02",
        outerwear_id="top_17",
    )

    token = outfits.draft_manual_worn(1, 10, target, target, selection)
    outfits.confirm_manual_worn(token, 1, 10)

    recorded = outfits.database.connect().execute(
        "SELECT * FROM worn_outfits ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    assert recorded["top_item_id"] == "top_09"
    assert recorded["outerwear_item_id"] == "top_17"
