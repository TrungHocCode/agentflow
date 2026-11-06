"""Follow-up tasks added while a run executes must actually execute, through the real graph."""

import os
import sys
import unittest
from types import SimpleNamespace
from typing import Any, AsyncGenerator, Dict
from unittest.mock import patch
from uuid import uuid4

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))
sys.path.insert(0, os.path.dirname(__file__))

from app.core.config import settings
from app.execution.adapters import LangGraphExecutionAdapter
from app.execution.run_budget import RunBudgetExceeded, RunNoLongerActive
from app.execution.state import Task
from app.modules.runs.models import RunDocument
from app.modules.runs.service import MAX_EXECUTION_PASSES, RunService
from test_modular_monolith import FakeRunRepository, InMemoryRunEventPublisher


def step(task_id: int, description: str, dependencies: list[int] | None = None) -> Task:
    return Task(id=task_id, node="worker", status="pending", description=description,
                dependencies=dependencies or [])


def run_service(port, observer) -> tuple[RunService, FakeRunRepository]:
    repository = FakeRunRepository()
    service = RunService(run_repository=repository, workflow_repository=None, execution_port=port,
                         event_publisher=InMemoryRunEventPublisher(), task_observers=[observer])
    return service, repository


async def queued_run(repository: FakeRunRepository, plan: list[Task], **values: Any) -> RunDocument:
    document = RunDocument(run_id=str(uuid4()), flow_id="workflow", status="queued", mode="executing", plan=plan,
                           **values)
    await repository.save(document)
    return document


class TestFollowupsThroughTheRealGraph(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # The legacy path runs tasks without a model, so the real graph can run in isolation.
        patcher = patch.object(settings, "ENVIRONMENT", "test")
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def observer(followups: dict[int, list[Task]], seen: list[int]) -> SimpleNamespace:
        async def on_task_completed(run, task, status):
            seen.append(task.id)
            return followups.get(task.id, [])
        return SimpleNamespace(on_task_completed=on_task_completed)

    async def test_followups_run_and_the_run_completes(self) -> None:
        seen: list[int] = []
        observer = self.observer({1: [step(2, "Verify the change"), step(3, "Assess it", [2])]}, seen)
        service, repository = run_service(LangGraphExecutionAdapter(), observer)
        document = await queued_run(repository, [step(1, "Collect")])

        finished = await service.execute_queued_run(document.run_id)

        self.assertEqual(finished.status, "completed", finished.error_message)
        self.assertEqual([(task.id, task.status) for task in finished.plan],
                         [(1, "done"), (2, "done"), (3, "done")])
        self.assertEqual(seen, [1, 2, 3])
        self.assertEqual(sorted(result["task_id"] for result in finished.result_storage), [1, 2, 3])

    async def test_followups_of_followups_also_run(self) -> None:
        observer = self.observer({1: [step(2, "Round one")], 2: [step(3, "Round two")]}, [])
        service, repository = run_service(LangGraphExecutionAdapter(), observer)
        document = await queued_run(repository, [step(1, "Collect")])

        finished = await service.execute_queued_run(document.run_id)

        self.assertEqual(finished.status, "completed", finished.error_message)
        self.assertEqual([task.status for task in finished.plan], ["done", "done", "done"])

    async def test_a_run_without_followups_is_unaffected(self) -> None:
        service, repository = run_service(LangGraphExecutionAdapter(), self.observer({}, []))
        document = await queued_run(repository, [step(1, "Collect"), step(2, "Report", [1])])

        finished = await service.execute_queued_run(document.run_id)

        self.assertEqual(finished.status, "completed", finished.error_message)
        self.assertEqual(len(finished.result_storage), 2)


class TestRoundFailuresDoNotDiscardARun(unittest.IsolatedAsyncioTestCase):
    class Port:
        def __init__(self, failing: list[Task]) -> None:
            self.failing = failing

        def execute_run(self, run_id: str, state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
            return self._stream()

        async def _stream(self) -> AsyncGenerator[Dict[str, Any], None]:
            for task in self.failing:
                yield {"worker_node": {"plan": [task], "current_task": task, "result_storage": []}}

    @staticmethod
    def finished(task_id: int, status: str, round_task: bool, error: str | None = None) -> Task:
        config = {"investigation_round_id": "r1"} if round_task else {}
        return Task(id=task_id, node="evidence_verifier", status=status, description="x", config=config, error=error)

    async def run(self, tasks: list[Task]) -> RunDocument:
        async def on_task_completed(run, task, status):
            return []

        service, repository = run_service(self.Port(tasks), SimpleNamespace(on_task_completed=on_task_completed))
        document = await queued_run(repository, [step(task.id, "x") for task in tasks])
        return await service.execute_queued_run(document.run_id)

    async def test_a_failed_round_step_still_completes_the_run_and_reports_the_gap(self) -> None:
        finished = await self.run([self.finished(1, "done", False),
                                   self.finished(2, "failed", True, "Ran out of iterations."),
                                   self.finished(3, "skipped", True, "Skipped due to failed dependency.")])

        self.assertEqual(finished.status, "completed")
        self.assertEqual(finished.metadata["partial_task_ids"], [2, 3])
        self.assertIn("Ran out of iterations.", finished.metadata["execution_warnings"])

    async def test_a_failed_collection_step_still_fails_the_run(self) -> None:
        finished = await self.run([self.finished(1, "failed", False, "Could not collect.")])
        self.assertEqual(finished.status, "failed")


class TestStreamFailureDuringFollowups(unittest.IsolatedAsyncioTestCase):
    class Port:
        def __init__(self, error: Exception) -> None:
            self.error, self.passes = error, 0

        def execute_run(self, run_id: str, state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
            self.passes += 1
            return self._first(state) if self.passes == 1 else self._later()

        async def _first(self, state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
            done = state["plan"][0].model_copy(update={"status": "done"})
            yield {"worker_node": {"plan": [done], "current_task": done, "result_storage": [
                {"task_id": done.id, "status": "done", "result": "ok"}]}}

        async def _later(self) -> AsyncGenerator[Dict[str, Any], None]:
            raise self.error
            yield {}

    async def run_with(self, error: Exception) -> RunDocument:
        async def on_task_completed(run, task, status):
            if task.id != 1:
                return []
            return [Task(id=2, node="evidence_verifier", status="pending", description="Verify",
                         config={"investigation_round_id": "r1"})]

        service, repository = run_service(self.Port(error), SimpleNamespace(on_task_completed=on_task_completed))
        document = await queued_run(repository, [step(1, "Collect")])
        return await service.execute_queued_run(document.run_id)

    async def test_running_out_of_time_keeps_the_finished_collection(self) -> None:
        finished = await self.run_with(RunBudgetExceeded("Run wall-clock budget exhausted."))

        self.assertEqual(finished.status, "completed")
        self.assertEqual([(task.id, task.status) for task in finished.plan], [(1, "done"), (2, "interrupted")])
        self.assertEqual(finished.metadata["partial_task_ids"], [2])

    async def test_cancellation_is_not_swallowed(self) -> None:
        finished = await self.run_with(RunNoLongerActive("Cancelled."))
        self.assertEqual(finished.status, "failed")
        self.assertEqual(finished.error_code, "run_cancelled")


class RecordingPort:
    """Records every pass's input; finishes each pending task it is handed."""

    def __init__(self) -> None:
        self.inputs: list[Dict[str, Any]] = []

    def execute_run(self, run_id: str, state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        self.inputs.append(state)
        return self._stream(state)

    async def _stream(self, state: Dict[str, Any]) -> AsyncGenerator[Dict[str, Any], None]:
        for task in state["plan"]:
            if task.status != "pending":
                continue
            done = task.model_copy(update={"status": "done"})
            yield {"worker_node": {"plan": [done], "current_task": done, "result_storage": [
                {"task_id": task.id, "status": "done", "result": "ok"}]}}


class TestFollowupPasses(unittest.IsolatedAsyncioTestCase):
    async def test_a_later_pass_gets_only_new_work_and_keeps_run_context(self) -> None:
        async def on_task_completed(run, task, status):
            return [step(2, "Follow up")] if task.id == 1 else []

        port = RecordingPort()
        service, repository = run_service(port, SimpleNamespace(on_task_completed=on_task_completed))
        document = await queued_run(repository, [step(1, "Collect")], input_data={"user_prompt": "Track pricing"},
                                    resolved_model_config={"worker_model": "m"})

        finished = await service.execute_queued_run(document.run_id)

        self.assertEqual(finished.status, "completed", finished.error_message)
        self.assertEqual(len(port.inputs), 2)
        second = port.inputs[1]
        self.assertEqual([task.id for task in second["plan"]], [2])
        self.assertEqual(second["mode"], "executing")
        # Re-sending these would duplicate them through the graph's append reducers.
        self.assertNotIn("result_storage", second)
        self.assertNotIn("messages", second)
        self.assertEqual(second["metadata"]["input_data"], {"user_prompt": "Track pricing"})
        self.assertEqual(second["metadata"]["resolved_model_config"], {"worker_model": "m"})

    async def test_endless_followups_stop_at_the_pass_limit(self) -> None:
        counter = {"next": 1}

        async def on_task_completed(run, task, status):
            counter["next"] += 1
            return [step(counter["next"], "Again")]

        port = RecordingPort()
        service, repository = run_service(port, SimpleNamespace(on_task_completed=on_task_completed))
        document = await queued_run(repository, [step(1, "Collect")])

        finished = await service.execute_queued_run(document.run_id)

        self.assertEqual(len(port.inputs), MAX_EXECUTION_PASSES)
        self.assertEqual(finished.status, "interrupted")


if __name__ == "__main__":
    unittest.main()
