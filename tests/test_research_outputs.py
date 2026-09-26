import os
import sys
import tempfile
import unittest
from test_support import isolated_workspace, use_test_adapters
from pathlib import Path

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.tools.base import ToolRegistry
from app.infrastructure.artifacts.storage import LocalArtifactStorage
from app.infrastructure.postgres.results_repository import PostgresResearchRepository
from app.modules.results.models import ArtifactRecord, EvidenceRecord, ResultRecord
from app.main import app
from app.modules.runs.models import RunDocument
from app.infrastructure.postgres.run_repository import PostgresRunRepository


class TestResearchOutputAdapters(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        use_test_adapters(self)
        self.enterContext(isolated_workspace())

    async def test_result_evidence_and_artifact_round_trip(self) -> None:
        repository = PostgresResearchRepository()
        result = await repository.save_result(
            ResultRecord(run_id="run-output-1", result_type="summary", content={"ok": True})
        )
        await repository.save_evidence(
            EvidenceRecord(run_id="run-output-1", source_url="https://example.com/docs")
        )
        await repository.save_artifact(
            ArtifactRecord(
                user_id="user-output-1",
                run_id="run-output-1",
                name="report.md",
                storage_uri="runs/user-output-1/run-output-1/report.md",
            )
        )

        self.assertEqual((await repository.list_results("run-output-1"))[0].id, result.id)
        self.assertEqual(len(await repository.list_evidence("run-output-1")), 1)
        self.assertEqual(len(await repository.list_artifacts("run-output-1")), 1)

    async def test_local_storage_rejects_escape_and_ingests_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir) / "artifacts"
            source = Path(temporary_dir) / "report.md"
            source.write_text("# report", encoding="utf-8")
            storage = LocalArtifactStorage(str(root))
            artifact = storage.ingest_file(str(source), "user-1", "run-1")

            self.assertIsNotNone(artifact)
            self.assertEqual(storage.resolve(artifact.storage_uri).read_text(encoding="utf-8"), "# report")
            with self.assertRaises(ValueError):
                storage.resolve("../../outside.txt")

    def test_chart_tool_generates_spec_and_svg(self) -> None:
        chart = ToolRegistry.get_tool("chart_generator")
        output = chart.invoke(
            {
                "title": "Framework comparison",
                "labels": ["A", "B"],
                "values": [3, 5],
                "chart_type": "bar",
                "filename": "phase7_chart",
            }
        )

        self.assertIn("Chart generated successfully", output)
        self.assertIn("phase7_chart.svg", output)
        self.assertTrue(Path("workspace_data/charts/phase7_chart.svg").is_file())
        self.assertTrue(Path("workspace_data/charts/phase7_chart.json").is_file())

    async def test_output_api_scopes_reads_to_an_existing_owned_run(self) -> None:
        run_repository = PostgresRunRepository()
        await run_repository.save(
            RunDocument(
                run_id="api-output-run",
                flow_id="api-output-flow",
                user_id="default_user",
                status="completed",
            )
        )
        repository = PostgresResearchRepository()
        await repository.save_result(
            ResultRecord(run_id="api-output-run", result_type="summary", content="ready")
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        )
        try:
            response = await client.get("/api/v1/runs/api-output-run/results")
        finally:
            await client.aclose()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["content"], "ready")


if __name__ == "__main__":
    unittest.main()
