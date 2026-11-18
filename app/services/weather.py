from __future__ import annotations

import asyncio
from collections import Counter
from datetime import UTC, date, datetime
from typing import Any

import httpx

from app.domain.schemas import WeatherSnapshot
from app.errors import WeatherUnavailable

WEATHER_CODES = {
    0: "clear",
    1: "mainly clear",
    2: "partly cloudy",
    3: "overcast",
    45: "fog",
    48: "freezing fog",
    51: "light drizzle",
    53: "drizzle",
    55: "heavy drizzle",
    61: "light rain",
    63: "rain",
    65: "heavy rain",
    71: "light snow",
    73: "snow",
    75: "heavy snow",
    80: "rain showers",
    81: "rain showers",
    82: "heavy rain showers",
    95: "thunderstorm",
    96: "thunderstorm with hail",
    99: "thunderstorm with hail",
}


class WeatherService:
    def __init__(
        self,
        base_url: str,
        latitude: float,
        longitude: float,
        timezone: str,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.latitude = latitude
        self.longitude = longitude
        self.timezone = timezone
        self._client = client

    async def fetch(self, target_date: date, *, attempts: int = 2) -> WeatherSnapshot:
        parameters = {
            "latitude": self.latitude,
            "longitude": self.longitude,
            "timezone": self.timezone,
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "hourly": ",".join(
                [
                    "temperature_2m",
                    "apparent_temperature",
                    "precipitation_probability",
                    "precipitation",
                    "weather_code",
                    "wind_speed_10m",
                    "wind_gusts_10m",
                ]
            ),
        }
        last_error: Exception | None = None
        for attempt in range(max(1, attempts)):
            try:
                if self._client:
                    response = await self._client.get("/forecast", params=parameters, timeout=6)
                else:
                    async with httpx.AsyncClient(base_url=self.base_url) as client:
                        response = await client.get("/forecast", params=parameters, timeout=6)
                response.raise_for_status()
                return self.parse(target_date, response.json())
            except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(1 + attempt * 2)
        raise WeatherUnavailable("Podgorica weather could not be verified") from last_error

    @staticmethod
    def parse(target_date: date, payload: dict[str, Any]) -> WeatherSnapshot:
        hourly = payload["hourly"]
        times = [datetime.fromisoformat(value) for value in hourly["time"]]
        morning_indexes = [index for index, value in enumerate(times) if 7 <= value.hour <= 9]
        afternoon_indexes = [index for index, value in enumerate(times) if 16 <= value.hour <= 20]
        all_indexes = [index for index, value in enumerate(times) if value.date() == target_date]
        if not morning_indexes or not afternoon_indexes or not all_indexes:
            raise ValueError("weather response does not cover commute windows")

        def average(field: str, indexes: list[int]) -> float:
            values = [float(hourly[field][index]) for index in indexes]
            return round(sum(values) / len(values), 1)

        def maximum(field: str) -> float:
            values = [float(hourly[field][index] or 0) for index in all_indexes]
            return round(max(values), 1)

        def total(field: str) -> float:
            values = [float(hourly[field][index] or 0) for index in all_indexes]
            return round(sum(values), 1)

        codes = [int(hourly["weather_code"][index]) for index in all_indexes]
        common_code = Counter(codes).most_common(1)[0][0]
        return WeatherSnapshot(
            target_date=target_date,
            source="Open-Meteo",
            fetched_at=datetime.now(UTC),
            morning_temperature_c=average("temperature_2m", morning_indexes),
            morning_feels_like_c=average("apparent_temperature", morning_indexes),
            afternoon_temperature_c=average("temperature_2m", afternoon_indexes),
            afternoon_feels_like_c=average("apparent_temperature", afternoon_indexes),
            max_precipitation_probability=int(maximum("precipitation_probability")),
            precipitation_mm=total("precipitation"),
            max_wind_kph=maximum("wind_speed_10m"),
            max_gust_kph=maximum("wind_gusts_10m"),
            condition=WEATHER_CODES.get(common_code, f"weather code {common_code}"),
        )
