"""CI-P8 schedule and notification contracts.

Schedules fire the same approved revision/run command a user would issue manually.
Overlaps never double-execute; stale revisions never run; every firing is recorded.
"""

from datetime import datetime, time
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.modules.competitive_intelligence.models import Contract

Cadence = Literal["daily", "weekly"]
NotificationKind = Literal["run_completed", "run_failed", "brief_ready", "schedule_skipped",
                           "schedule_blocked", "schedule_fired"]


class Schedule(Contract):
    id: UUID
    watchlist_id: UUID
    owner_id: str = Field(min_length=1, max_length=36)
    cadence: Cadence
    timezone: str = Field(min_length=1, max_length=64)
    run_at: str = Field(min_length=5, max_length=5)
    day_of_week: int | None = Field(None, ge=0, le=6)
    active: bool = True
    next_due_at: datetime
    created_at: datetime
    updated_at: datetime


class CreateSchedule(Contract):
    cadence: Cadence = "weekly"
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    run_at: str = Field(default="08:00", min_length=5, max_length=5)
    day_of_week: int | None = Field(default=0, ge=0, le=6)


class UpdateSchedule(Contract):
    cadence: Cadence | None = None
    timezone: str | None = Field(None, min_length=1, max_length=64)
    run_at: str | None = Field(None, min_length=5, max_length=5)
    day_of_week: int | None = Field(None, ge=0, le=6)
    active: bool | None = None


class Notification(Contract):
    id: UUID
    owner_id: str = Field(min_length=1, max_length=36)
    watchlist_id: UUID | None = None
    run_id: UUID | None = None
    kind: NotificationKind
    title: str = Field(min_length=1, max_length=500)
    body: str = Field(default="", max_length=2000)
    read_at: datetime | None = None
    created_at: datetime


def parse_run_at(value: str) -> time:
    """Parse HH:MM wall time; rejects anything the scheduler cannot anchor."""
    try:
        hour_text, minute_text = value.split(":")
        parsed = time(int(hour_text), int(minute_text))
    except (ValueError, AttributeError):
        raise ValueError("run_at must be HH:MM in 24-hour wall time.") from None
    return parsed
