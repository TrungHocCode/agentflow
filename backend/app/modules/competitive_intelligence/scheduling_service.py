"""CI-P8 scheduling use cases: due schedules fire the approved command exactly once.

Each firing re-audits approval and scope through the standard start path, so a newly
unapproved revision can never run. Overlaps are reported, never double-executed.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.modules.competitive_intelligence.models import StartRunRequest
from app.modules.competitive_intelligence.ports import ScheduleRepository
from app.modules.competitive_intelligence.schedule_contracts import CreateSchedule, Schedule, UpdateSchedule
from app.modules.competitive_intelligence.schedule_timing import next_occurrence
from app.modules.competitive_intelligence.service import IntelligenceService
from app.shared.errors import ConflictError



class SchedulingService:
    def __init__(self, schedules: ScheduleRepository, intelligence: IntelligenceService) -> None:
        self.schedules, self.intelligence = schedules, intelligence

    async def create_schedule(self, watchlist_id: str, owner_id: str, request: CreateSchedule,
                              now: datetime) -> Schedule:
        if request.cadence == "weekly" and request.day_of_week is None:
            raise ValueError("Weekly schedules require a day of week.")
        return await self.schedules.create_schedule(
            watchlist_id, owner_id, request.cadence, request.timezone, request.run_at,
            request.day_of_week if request.cadence == "weekly" else None, now)

    async def list_schedules(self, watchlist_id: str, owner_id: str) -> list[Schedule]:
        return await self.schedules.list_schedules(watchlist_id, owner_id)

    async def update_schedule(self, schedule_id: str, watchlist_id: str, owner_id: str,
                              request: UpdateSchedule, now: datetime) -> Schedule:
        return await self.schedules.update_schedule(
            schedule_id, watchlist_id, owner_id, now, cadence=request.cadence,
            timezone_name=request.timezone, run_at=request.run_at, day_of_week=request.day_of_week,
            active=request.active)

    async def tick(self, now: datetime) -> list[dict]:
        """Fire every due schedule once; returns per-schedule firing reports for observability."""
        reports: list[dict] = []
        for schedule in await self.schedules.claim_due(now):
            reports.append(await self._fire(schedule, now))
        return reports

    async def _fire(self, schedule: Schedule, now: datetime) -> dict:
        await self.schedules.advance(str(schedule.id), schedule.owner_id, now)
        try:
            watchlist = await self.intelligence.get(str(schedule.watchlist_id), schedule.owner_id)
        except Exception as exc:
            return await self._blocked(schedule, now, f"Watchlist unavailable: {type(exc).__name__}.")
        revision = watchlist.current_revision
        if watchlist.status != "active" or revision.approval_status != "approved":
            return await self._blocked(schedule, now, "Current revision is not approved; no run started.")
        occurrence = now.strftime("%Y%m%d")
        try:
            run = await self.intelligence.start(
                str(schedule.watchlist_id),
                StartRunRequest(revision_id=revision.id,
                                workflow_version_id=revision.config.workflow_version_id or ""),
                schedule.owner_id, f"ci-sched:{schedule.id}:{occurrence}")
        except ConflictError as exc:
            if exc.code in ("watchlist_run_active", "idempotency_key_reused"):
                return await self._reported(schedule, now, "schedule_skipped",
                                            "Skipped: a run is already active for this watchlist.", None)
            return await self._blocked(schedule, now, f"Start refused ({exc.code}).")
        except Exception as exc:
            return await self._blocked(schedule, now, f"Start failed: {type(exc).__name__}.")
        await self.schedules.notify(schedule.owner_id, "schedule_fired",
                                    f"Scheduled run started for {watchlist.name}.",
                                    f"Occurrence {occurrence}; run {run.run_id}.",
                                    str(schedule.watchlist_id), run.run_id)
        return {"schedule_id": str(schedule.id), "outcome": "fired", "run_id": run.run_id}

    async def _blocked(self, schedule: Schedule, now: datetime, reason: str) -> dict:
        await self.schedules.notify(schedule.owner_id, "schedule_blocked",
                                    "Scheduled run did not start.", reason, str(schedule.watchlist_id))
        return {"schedule_id": str(schedule.id), "outcome": "blocked", "reason": reason}

    async def _reported(self, schedule: Schedule, now: datetime, kind: str, title: str,
                        run_id: str | None) -> dict:
        await self.schedules.notify(schedule.owner_id, kind, title, "", str(schedule.watchlist_id), run_id)
        return {"schedule_id": str(schedule.id), "outcome": "skipped", "reason": title}

    @staticmethod
    def preview_next(cadence: str, timezone_name: str, run_at: str, day_of_week: int | None,
                     now: datetime):
        return next_occurrence(now, cadence, timezone_name, run_at, day_of_week)

    async def list_notifications(self, owner_id: str, limit: int,
                                 unread_only: bool) -> tuple[list, int, int]:
        return (await self.schedules.list_notifications(owner_id, limit, unread_only),
                await self.schedules.count_unread(owner_id),
                await self.schedules.count_notifications(owner_id))

    async def mark_notification_read(self, notification_id: str, owner_id: str, now: datetime):
        return await self.schedules.mark_read(notification_id, owner_id, now)
