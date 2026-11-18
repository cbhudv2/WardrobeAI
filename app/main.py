from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Response, status

from app.config import Settings, get_settings
from app.database import Database
from app.legacy_import import import_legacy
from app.llm.client import MistralClient
from app.llm.tools import ToolDispatcher
from app.logging_config import configure_logging
from app.services.conversation import ConversationMemory
from app.services.outfits import OutfitService
from app.services.scheduler import SchedulerService
from app.services.shopping import ShoppingService
from app.services.wardrobe import WardrobeService
from app.services.weather import WeatherService
from app.telegram.bot import TelegramBotService

logger = logging.getLogger(__name__)


@dataclass
class Runtime:
    database: Database
    llm: MistralClient
    telegram: TelegramBotService
    scheduler: SchedulerService
    llm_error: str | None = None


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.log_level)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        database = Database(resolved.database_path)
        database.migrate(resolved.backup_dir)
        owner_user_id = min(resolved.allowed_user_ids)
        import_legacy(
            database,
            resolved.legacy_wardrobe_path,
            resolved.legacy_history_path,
            owner_user_id,
            resolved.telegram_daily_chat_id,
        )
        wardrobe = WardrobeService(database)
        outfits = OutfitService(database, wardrobe)
        weather = WeatherService(
            resolved.weather_base_url,
            resolved.weather_latitude,
            resolved.weather_longitude,
            resolved.app_timezone,
        )
        shopping = ShoppingService(wardrobe)
        memory = ConversationMemory(database)
        llm = MistralClient(
            resolved.mistral_api_key.get_secret_value(),
            resolved.mistral_base_url,
            resolved.mistral_model,
            reasoning_effort=resolved.mistral_reasoning_effort,
        )
        llm_error = None
        try:
            await llm.check_capabilities()
        except Exception as exc:
            llm_error = type(exc).__name__
            logger.error("llm_capability_check_failed", exc_info=True)
        dispatcher = ToolDispatcher(
            llm,
            wardrobe,
            outfits,
            weather,
            shopping,
            resolved.app_timezone,
            memory,
        )
        telegram = TelegramBotService(
            resolved.telegram_bot_token.get_secret_value(),
            resolved.allowed_user_ids,
            resolved.telegram_daily_chat_id,
            database,
            wardrobe,
            outfits,
            dispatcher,
            resolved.timezone,
        )
        scheduler = SchedulerService(
            database,
            telegram,
            dispatcher,
            owner_user_id=owner_user_id,
            chat_id=resolved.telegram_daily_chat_id,
            timezone=resolved.timezone,
            suggestion_time=resolved.daily_suggestion_time,
            weekdays=resolved.schedule_weekdays,
            backup_dir=resolved.backup_dir,
        )
        runtime = Runtime(database, llm, telegram, scheduler, llm_error)
        application.state.runtime = runtime
        await telegram.start()
        scheduler.start()
        logger.info("application_started")
        try:
            yield
        finally:
            await scheduler.stop()
            await telegram.stop()
            await llm.close()
            database.close()
            logger.info("application_stopped")

    application = FastAPI(
        title="WardrobeAI",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/ready")
    async def ready(response: Response) -> dict[str, str]:
        runtime: Runtime | None = getattr(application.state, "runtime", None)
        if (
            runtime is None
            or not runtime.database.integrity_check()
            or runtime.llm_error
            or runtime.scheduler.task is None
            or runtime.scheduler.task.done()
        ):
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
            return {"status": "not_ready"}
        return {"status": "ready"}

    return application


app = create_app()
