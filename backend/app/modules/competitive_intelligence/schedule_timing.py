"""DST-safe schedule arithmetic; wall-time anchored, never blind +24h steps."""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.modules.competitive_intelligence.schedule_contracts import parse_run_at


def resolve_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"Unknown timezone: {name}.") from exc


def next_occurrence(after_utc: datetime, cadence: str, timezone_name: str, run_at: str,
                    day_of_week: int | None) -> datetime:
    """Next wall-time slot strictly after `after_utc`, returned in UTC.

    Each candidate day is localized independently, so spring-forward gaps shift the day
    and fall-back ambiguity resolves to the first occurrence; arithmetic never assumes
    fixed-length days.
    """
    zone = resolve_timezone(timezone_name)
    wall = parse_run_at(run_at)
    if after_utc.tzinfo is None:
        raise ValueError("Reference time must carry a timezone.")
    local = after_utc.astimezone(zone).replace(tzinfo=None)
    for offset in range(0, 9):
        day = (local + timedelta(days=offset)).date()
        if cadence == "weekly":
            # Python Monday=0..Sunday=6 maps to our Sunday=0..Saturday=6 via (weekday+1)%7.
            if (day.weekday() + 1) % 7 != (day_of_week if day_of_week is not None else 0):
                continue
        candidate = datetime(day.year, day.month, day.day, wall.hour, wall.minute, tzinfo=zone)
        moment = candidate.astimezone(timezone.utc)
        if moment > after_utc:
            return moment
    raise ValueError("No schedule slot found within the search window.")
