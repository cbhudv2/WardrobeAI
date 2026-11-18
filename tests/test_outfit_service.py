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
