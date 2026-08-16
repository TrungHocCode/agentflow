import os
import sys
import unittest

# Adjust path to import from backend
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import State, Task
from app.execution.nodes.dispatcher import TaskDispatcher


class TestTaskDispatcher(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.dispatcher = TaskDispatcher()

    async def test_dispatch_sequential_tasks(self):
        task1 = Task(id=1, node="Worker1", status="pending", description="Task 1")
        task2 = Task(id=2, node="Worker2", status="pending", description="Task 2", dependencies=[1])

        state: State = {
            "messages": [],
            "plan": [task1, task2],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "executing"
        }

        # First dispatch should pick task1
        updates1 = await self.dispatcher.dispatch(state)
        self.assertIsNotNone(updates1.get("current_task"))
        self.assertEqual(updates1["current_task"].id, 1)
        self.assertEqual(updates1["current_task"].status, "running")

        # Simulate task1 done
        task1_done = task1.model_copy(update={"status": "done"})
        state["plan"] = [task1_done, task2]

        # Second dispatch should pick task2 since dep 1 is done
        updates2 = await self.dispatcher.dispatch(state)
        self.assertIsNotNone(updates2.get("current_task"))
        self.assertEqual(updates2["current_task"].id, 2)
        self.assertEqual(updates2["current_task"].status, "running")

        # Simulate task2 done
        task2_done = task2.model_copy(update={"status": "done"})
        state["plan"] = [task1_done, task2_done]

        # Third dispatch should complete run phase
        updates3 = await self.dispatcher.dispatch(state)
        self.assertIsNone(updates3.get("current_task"))
        self.assertEqual(updates3.get("mode"), "conversation")

    async def test_dispatch_dependency_failure_skips_task(self):
        task1 = Task(id=1, node="Worker1", status="failed", description="Task 1", error="Failed API")
        task2 = Task(id=2, node="Worker2", status="pending", description="Task 2", dependencies=[1])

        state: State = {
            "messages": [],
            "plan": [task1, task2],
            "current_task": None,
            "logs": [],
            "result_storage": [],
            "mode": "executing"
        }

        updates = await self.dispatcher.dispatch(state)
        self.assertIn("plan", updates)
        self.assertEqual(updates["plan"][0].id, 2)
        self.assertEqual(updates["plan"][0].status, "skipped")


if __name__ == "__main__":
    unittest.main()
