from datetime import date

from app.services.weather import WeatherService


def test_weather_parser_summarizes_commute_windows() -> None:
    target = date(2026, 11, 18)
    times = [f"2026-11-18T{hour:02d}:00" for hour in range(24)]
    payload = {
        "hourly": {
            "time": times,
            "temperature_2m": list(range(24)),
            "apparent_temperature": [value - 1 for value in range(24)],
            "precipitation_probability": [20] * 24,
            "precipitation": [0.1] * 24,
            "weather_code": [2] * 24,
            "wind_speed_10m": [10] * 24,
            "wind_gusts_10m": [20] * 24,
        }
    }

    result = WeatherService.parse(target, payload)

    assert result.morning_temperature_c == 8.0
    assert result.afternoon_temperature_c == 18.0
    assert result.max_precipitation_probability == 20
    assert result.condition == "partly cloudy"
