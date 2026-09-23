import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.state import SupervisorOutput, Task, update_plan


class TestSupervisorContract(unittest.TestCase):
    def test_plan_proposal_must_contain_at_least_one_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least one task"):
            SupervisorOutput(
                decision="propose_plan",
                assistant_message="I have a plan.",
                plan=[],
            )

    def test_plan_dependencies_must_be_acyclic(self) -> None:
        with self.assertRaisesRegex(ValueError, "cycles"):
            SupervisorOutput(
                decision="propose_plan",
                assistant_message="I have a plan.",
                plan=[
                    Task(
                        id=1,
                        node="source_researcher",
                        status="pending",
                        description="Find sources",
                        dependencies=[2],
                    ),
                    Task(
                        id=2,
                        node="report_agent",
                        status="pending",
                        description="Write report",
                        dependencies=[1],
                    ),
                ],
            )

    def test_clarification_response_cannot_contain_a_plan(self) -> None:
        with self.assertRaisesRegex(ValueError, "Only a plan proposal"):
            SupervisorOutput(
                decision="clarify",
                assistant_message="Which research topic do you mean?",
                plan=[
                    Task(
                        id=1,
                        node="source_researcher",
                        status="pending",
                        description="Find sources",
                    )
                ],
            )

    def test_plan_replacement_can_remove_stale_tasks(self) -> None:
        previous = [
            Task(id=1, node="source_researcher", status="pending", description="Find sources"),
            Task(id=2, node="report_agent", status="pending", description="Write report"),
        ]
        replacement = Task(
            id=3,
            node="source_researcher",
            status="pending",
            description="Research the clarified topic",
        )

        updated = update_plan(previous, {"__replace__": True, "tasks": [replacement]})

        self.assertEqual(updated, [replacement])


if __name__ == "__main__":
    unittest.main()
