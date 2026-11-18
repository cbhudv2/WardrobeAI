from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from telegram.error import NetworkError, RetryAfter, TelegramError, TimedOut

from app.database import Database
from app.domain.enums import ScheduledSendStatus
from app.errors import ModelUnavailable, WeatherUnavailable
from app.llm.tools import ToolDispatcher
from app.telegram.bot import TelegramBotService

logger = logging.getLogger(__name__)


class SchedulerService:
    def __init__(
        self,
        database: Database,
        telegram: TelegramBotService,
        dispatcher: ToolDispatcher,
        *,
        owner_user_id: int,
        chat_id: int,
        timezone: ZoneInfo,
        suggestion_time: str,
        weekdays: frozenset[int],
        backup_dir: Path,
    ) -> None:
        self.database = database
        self.telegram = telegram
        self.dispatcher = dispatcher
        self.owner_user_id = owner_user_id
        self.chat_id = chat_id
        self.timezone = timezone
        hour, minute = map(int, suggestion_time.split(":"))
        self.suggestion_time = time(hour, minute)
        self.weekdays = weekdays
        self.backup_dir = Path(backup_dir)
        self.task: asyncio.Task | None = None

    def start(self) -> None:
        self.recover_ambiguous_sends()
        self.task = asyncio.create_task(self.run(), name="wardrobe-scheduler")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass

    async def run(self) -> None:
        while True:
            try:
                await self.tick(datetime.now(self.timezone))
            except Exception:
                logger.exception("scheduler_tick_failed")
            await asyncio.sleep(30)

    async def tick(self, local_now: datetime) -> None:
        if self.should_schedule_outfit(local_now):
            self._ensure_row("weekday_outfit", local_now.date())
        if local_now.time() >= time(2, 30):
            self._ensure_row("nightly_backup", local_now.date())
        await self._process_due(local_now)

    def should_schedule_outfit(self, local_now: datetime) -> bool:
        return local_now.weekday() in self.weekdays and local_now.time() >= self.suggestion_time

    def _ensure_row(self, kind: str, local_date: date) -> None:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO scheduled_sends("
                "id, schedule_kind, local_date, chat_id, status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"schedule_{uuid.uuid4().hex}",
                    kind,
                    local_date.isoformat(),
                    self.chat_id,
                    ScheduledSendStatus.PENDING.value,
                    now,
                    now,
                ),
            )

    async def _process_due(self, local_now: datetime) -> None:
        rows = (
            self.database.connect()
            .execute(
                "SELECT * FROM scheduled_sends WHERE local_date=? AND status IN (?, ?) "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) ORDER BY created_at",
                (
                    local_now.date().isoformat(),
                    ScheduledSendStatus.PENDING.value,
                    ScheduledSendStatus.FAILED.value,
                    local_now.astimezone(UTC).isoformat(),
                ),
            )
            .fetchall()
        )
        for row in rows:
            if not self._claim(row["id"]):
                continue
            if row["schedule_kind"] == "nightly_backup":
                await asyncio.to_thread(self._perform_backup, row["id"], local_now.date())
            else:
                await self._send_outfit(row["id"], int(row["attempts"]) + 1, local_now)

    def _claim(self, row_id: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self.database.transaction() as connection:
            return bool(
                connection.execute(
                    "UPDATE scheduled_sends SET status=?, attempts=attempts+1, updated_at=? "
                    "WHERE id=? AND status IN (?, ?)",
                    (
                        ScheduledSendStatus.SENDING.value,
                        now,
                        row_id,
                        ScheduledSendStatus.PENDING.value,
                        ScheduledSendStatus.FAILED.value,
                    ),
                ).rowcount
            )

    async def _send_outfit(self, row_id: str, attempt: int, local_now: datetime) -> None:
        try:
            response = await self.dispatcher.recommend(
                self.owner_user_id,
                self.chat_id,
                local_now.date(),
                mood=None,
                occasion="casual software office",
                constraints=None,
            )
            message_id = await self.telegram.send_response(self.chat_id, response)
            self._mark_sent(row_id, message_id)
        except (WeatherUnavailable, ModelUnavailable):
            if attempt < 3:
                retry_minute = 15 if attempt == 1 else 45
                retry_at = datetime.combine(
                    local_now.date(), time(7, retry_minute), self.timezone
                ).astimezone(UTC)
                self._mark_retry(row_id, retry_at, "dependency_unavailable")
            else:
                message = await self.telegram.bot.send_message(
                    chat_id=self.chat_id,
                    text=(
                        "I couldn't verify the weather or generate a safe outfit this morning. "
                        "I won't invent one; use /outfit to try again later."
                    ),
                )
                self._mark_sent(row_id, message.message_id)
        except RetryAfter as exc:
            retry_at = datetime.now(UTC) + timedelta(seconds=float(exc.retry_after))
            self._mark_retry(row_id, retry_at, "telegram_rate_limited")
        except (TimedOut, NetworkError):
            self._mark_ambiguous(row_id, "telegram_delivery_ambiguous")
        except TelegramError:
            self._mark_retry(row_id, datetime.now(UTC) + timedelta(minutes=15), "telegram_rejected")

    def _perform_backup(self, row_id: str, local_date: date) -> None:
        try:
            target = self.backup_dir / f"wardrobe-{local_date.isoformat()}.sqlite3"
            self.database.backup(target)
            backups = sorted(self.backup_dir.glob("wardrobe-*.sqlite3"), reverse=True)
            for old_backup in backups[14:]:
                old_backup.unlink()
            self._mark_sent(row_id, None)
        except Exception:
            self._mark_retry(row_id, datetime.now(UTC) + timedelta(hours=1), "backup_failed")

    def _mark_sent(self, row_id: str, message_id: int | None) -> None:
        self._update_row(row_id, ScheduledSendStatus.SENT, message_id=message_id)

    def _mark_retry(self, row_id: str, retry_at: datetime, error_code: str) -> None:
        self._update_row(
            row_id,
            ScheduledSendStatus.FAILED,
            next_attempt_at=retry_at.isoformat(),
            error_code=error_code,
        )

    def _mark_ambiguous(self, row_id: str, error_code: str) -> None:
        self._update_row(row_id, ScheduledSendStatus.AMBIGUOUS, error_code=error_code)

    def _update_row(
        self,
        row_id: str,
        status: ScheduledSendStatus,
        *,
        message_id: int | None = None,
        next_attempt_at: str | None = None,
        error_code: str | None = None,
    ) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE scheduled_sends SET status=?, telegram_message_id=?, next_attempt_at=?, "
                "last_error_code=?, updated_at=? WHERE id=?",
                (
                    status.value,
                    message_id,
                    next_attempt_at,
                    error_code,
                    datetime.now(UTC).isoformat(),
                    row_id,
                ),
            )

    def recover_ambiguous_sends(self) -> None:
        with self.database.transaction() as connection:
            connection.execute(
                "UPDATE scheduled_sends SET status=?, last_error_code=?, updated_at=? "
                "WHERE status=?",
                (
                    ScheduledSendStatus.AMBIGUOUS.value,
                    "process_restarted_during_delivery",
                    datetime.now(UTC).isoformat(),
                    ScheduledSendStatus.SENDING.value,
                ),
            )
