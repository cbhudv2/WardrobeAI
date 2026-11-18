from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_env: str = "development"
    log_level: str = "INFO"
    http_host: str = "0.0.0.0"
    http_port: int = 3000

    database_path: Path = Path("data/wardrobe.sqlite3")
    backup_dir: Path = Path("backups")
    legacy_wardrobe_path: Path = Path("legacy_data/wardrobe.json")
    legacy_history_path: Path = Path("legacy_data/outfit_history.json")

    telegram_bot_token: SecretStr
    telegram_allowed_user_ids: str
    telegram_daily_chat_id: int

    mistral_api_key: SecretStr
    mistral_base_url: str = "https://api.mistral.ai/v1"
    mistral_model: str = "zai-glm-5-3-flash"
    mistral_reasoning_effort: str = "low"

    weather_base_url: str = "https://api.open-meteo.com/v1"
    weather_latitude: float = 42.4304
    weather_longitude: float = 19.2594
    weather_location_name: str = "Podgorica"

    app_timezone: str = "Europe/Podgorica"
    daily_suggestion_time: str = "07:00"
    daily_suggestion_weekdays: str = "MON,TUE,WED,THU,FRI"

    @field_validator("mistral_base_url", "weather_base_url")
    @classmethod
    def strip_url(cls, value: str) -> str:
        return value.rstrip("/")

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        ZoneInfo(value)
        return value

    @field_validator("daily_suggestion_time")
    @classmethod
    def validate_daily_time(cls, value: str) -> str:
        parts = value.split(":")
        if len(parts) != 2 or not all(part.isdigit() for part in parts):
            raise ValueError("daily_suggestion_time must be HH:MM")
        hour, minute = map(int, parts)
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise ValueError("daily_suggestion_time must be HH:MM")
        return f"{hour:02d}:{minute:02d}"

    @property
    def allowed_user_ids(self) -> frozenset[int]:
        values = {
            int(part.strip()) for part in self.telegram_allowed_user_ids.split(",") if part.strip()
        }
        if not values:
            raise ValueError("TELEGRAM_ALLOWED_USER_IDS must contain at least one ID")
        return frozenset(values)

    @property
    def timezone(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def schedule_weekdays(self) -> frozenset[int]:
        mapping = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
        try:
            return frozenset(
                mapping[value.strip().upper()]
                for value in self.daily_suggestion_weekdays.split(",")
            )
        except KeyError as exc:
            raise ValueError(f"invalid weekday: {exc.args[0]}") from exc


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
