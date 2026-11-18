from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services.scheduler import SchedulerService


def scheduler_shell(database, tmp_path: Path) -> SchedulerService:
    return SchedulerService(
        database,
        telegram=None,  # type: ignore[arg-type]
        dispatcher=None,  # type: ignore[arg-type]
        owner_user_id=1,
        chat_id=10,
        timezone=ZoneInfo("Europe/Podgorica"),
        suggestion_time="07:00",
        weekdays=frozenset({0, 1, 2, 3, 4}),
        backup_dir=tmp_path,
    )


def test_scheduler_runs_after_0700_on_weekdays_only(database, tmp_path: Path) -> None:
    scheduler = scheduler_shell(database, tmp_path)
    timezone = ZoneInfo("Europe/Podgorica")

    assert scheduler.should_schedule_outfit(datetime(2026, 11, 18, 7, 0, tzinfo=timezone))
    assert not scheduler.should_schedule_outfit(datetime(2026, 11, 18, 6, 59, tzinfo=timezone))
    assert not scheduler.should_schedule_outfit(datetime(2026, 11, 21, 8, 0, tzinfo=timezone))


def test_schedule_row_is_idempotent(database, tmp_path: Path) -> None:
    scheduler = scheduler_shell(database, tmp_path)
    scheduler._ensure_row("weekday_outfit", datetime(2026, 11, 18).date())
    scheduler._ensure_row("weekday_outfit", datetime(2026, 11, 18).date())
    count = (
        database.connect()
        .execute("SELECT count(*) FROM scheduled_sends WHERE schedule_kind='weekday_outfit'")
        .fetchone()[0]
    )
    assert count == 1
