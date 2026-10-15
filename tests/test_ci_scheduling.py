"""CI-P8 scheduling and notifications; deterministic fixtures, mocked services, no network."""

import os
import sys
import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.competitive_intelligence.models import (
    CreateWatchlist, Revision, Watchlist, WatchlistConfig, digest,
)
from app.modules.competitive_intelligence.schedule_contracts import CreateSchedule, UpdateSchedule
from app.modules.competitive_intelligence.schedule_timing import next_occurrence
from app.modules.competitive_intelligence.scheduling_service import SchedulingService
from app.modules.runs.models import RunDocument
from app.shared.errors import ConflictError, ResourceNotFoundError

NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


def watchlist_fixture(approved: bool = True):
    request = CreateWatchlist(name="W", config=WatchlistConfig(
        goal="Track pricing", dimensions=["pricing"], workflow_version_id="v",
        comparison_criteria=[{"field": "price", "objective": "Assess affordability"}],
        products=[{"name": "Ours", "kind": "own", "official_website": "https://ours.invalid/"},
                  {"name": "Rival", "kind": "competitor", "official_website": "https://rival.invalid/",
                   "sources": [{"id": uuid4(), "url": "https://rival.invalid/pricing", "kind": "pricing"}]}]))
    now = NOW
    return Watchlist(id=uuid4(), owner_id="owner", name="W", description="", status="active",
        created_at=now, updated_at=now, current_revision=Revision(
            id=uuid4(), watchlist_id=uuid4(), revision_number=1, config=request.config,
            config_hash=digest(request.config),
            approval_status="approved" if approved else "unapproved", created_at=now))


def schedule_fixture(**overrides):
    from app.modules.competitive_intelligence.schedule_contracts import Schedule
    values = {"id": uuid4(), "watchlist_id": uuid4(), "owner_id": "owner", "cadence": "weekly",
              "timezone": "UTC", "run_at": "08:00", "day_of_week": 1, "active": True,
              "next_due_at": NOW, "created_at": NOW, "updated_at": NOW}
    values.update(overrides)
    return Schedule(**values)


class TestScheduleTiming(unittest.TestCase):
    def test_daily_and_weekly_slots(self) -> None:
        after = datetime(2026, 10, 9, 12, 0, tzinfo=timezone.utc)  # Friday
        self.assertEqual(next_occurrence(after, "daily", "UTC", "08:00", None),
                         datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc))
        self.assertEqual(next_occurrence(after, "weekly", "UTC", "08:00", 1),
                         datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc))
        same_day = datetime(2026, 10, 12, 6, 0, tzinfo=timezone.utc)  # Monday
        self.assertEqual(next_occurrence(same_day, "weekly", "UTC", "08:00", 1),
                         datetime(2026, 10, 12, 8, 0, tzinfo=timezone.utc))

    def test_dst_gap_and_fold(self) -> None:
        zone = "America/New_York"
        gap_after = datetime(2026, 3, 8, 6, 0, tzinfo=timezone.utc)
        result = next_occurrence(gap_after, "daily", zone, "02:30", None)
        self.assertGreater(result, gap_after)
        self.assertEqual(result.astimezone(ZoneInfo(zone)).date().isoformat(), "2026-03-08")
        fold_after = datetime(2026, 11, 1, 4, 0, tzinfo=timezone.utc)
        self.assertEqual(next_occurrence(fold_after, "daily", zone, "01:30", None),
                         datetime(2026, 11, 1, 5, 30, tzinfo=timezone.utc))

    def test_invalid_inputs_rejected(self) -> None:
        with self.assertRaises(ValueError):
            next_occurrence(NOW, "daily", "Mars/Olympus", "08:00", None)
        with self.assertRaises(ValueError):
            next_occurrence(NOW, "daily", "UTC", "8am", None)
        with self.assertRaises(ValueError):
            next_occurrence(datetime(2026, 10, 9, 12, 0), "daily", "UTC", "08:00", None)


class TestSchedulingTick(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.schedules = AsyncMock()
        self.intelligence = AsyncMock()
        self.service = SchedulingService(self.schedules, self.intelligence)

    async def test_due_approved_schedule_fires_once(self) -> None:
        schedule = schedule_fixture()
        self.schedules.claim_due = AsyncMock(return_value=[schedule])
        self.schedules.advance = AsyncMock(return_value=schedule)
        self.schedules.notify = AsyncMock(return_value=MagicMock())
        self.intelligence.get = AsyncMock(return_value=watchlist_fixture())
        run = RunDocument(run_id=str(uuid4()), flow_id="f", user_id="owner", workflow_version_id="v",
                          status="queued", plan=[])
        self.intelligence.start = AsyncMock(return_value=run)
        reports = await self.service.tick(NOW)
        self.assertEqual(reports[0]["outcome"], "fired")
        self.assertEqual(reports[0]["run_id"], run.run_id)
        key = self.intelligence.start.call_args.args[3]
        self.assertTrue(key.startswith(f"ci-sched:{schedule.id}:"))

    async def test_overlap_skips_and_stale_blocks_without_running(self) -> None:
        schedule = schedule_fixture()
        self.schedules.claim_due = AsyncMock(return_value=[schedule])
        self.schedules.advance = AsyncMock(return_value=schedule)
        self.schedules.notify = AsyncMock(return_value=MagicMock())
        self.intelligence.get = AsyncMock(return_value=watchlist_fixture())
        self.intelligence.start = AsyncMock(side_effect=ConflictError("Active.", code="watchlist_run_active"))
        reports = await self.service.tick(NOW)
        self.assertEqual(reports[0]["outcome"], "skipped")
        self.schedules.notify.assert_awaited()
        self.intelligence.get = AsyncMock(return_value=watchlist_fixture(approved=False))
        reports = await self.service.tick(NOW)
        self.assertEqual(reports[0]["outcome"], "blocked")
        self.intelligence.start.assert_awaited_once()

    async def test_missing_watchlist_blocks_visibly(self) -> None:
        schedule = schedule_fixture()
        self.schedules.claim_due = AsyncMock(return_value=[schedule])
        self.schedules.advance = AsyncMock(return_value=schedule)
        self.schedules.notify = AsyncMock(return_value=MagicMock())
        self.intelligence.get = AsyncMock(side_effect=ResourceNotFoundError("gone"))
        reports = await self.service.tick(NOW)
        self.assertEqual(reports[0]["outcome"], "blocked")

    async def test_weekly_schedule_requires_weekday(self) -> None:
        with self.assertRaises(ValueError):
            await self.service.create_schedule(str(uuid4()), "owner", CreateSchedule(cadence="weekly",
                                                                                     day_of_week=None), NOW)

    async def test_notifications_list_and_read(self) -> None:
        self.schedules.list_notifications = AsyncMock(return_value=[])
        self.schedules.count_unread = AsyncMock(return_value=0)
        self.schedules.count_notifications = AsyncMock(return_value=0)
        items, unread, total = await self.service.list_notifications("owner", 50, False)
        self.assertEqual((items, unread, total), ([], 0, 0))


if __name__ == "__main__":
    unittest.main()
