"""Configured-root regressions without live models or development files."""

import json
from pathlib import Path
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, patch

import httpx

from test_support import isolated_workspace, use_test_adapters
from app.api.dependencies import get_current_user_id, get_run_query_service
from app.core.config import settings
from app.execution.tools.base import ToolRegistry
from app.infrastructure.artifacts.storage import LocalArtifactStorage
from app.infrastructure.postgres.results_repository import PostgresResearchRepository
from app.infrastructure.postgres.run_repository import PostgresRunRepository
from app.main import app
from app.modules.runs.models import RunDocument
from app.modules.runs.service import RunService
from app.shared.artifact_paths import generated_directory, generated_file


class TestConfiguredArtifactRoot(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        use_test_adapters(self)
        self.workspace = self.enterContext(isolated_workspace())
        self.root = self.workspace / "custom-artifacts"
        self.enterContext(patch.object(settings, "ARTIFACT_ROOT", str(self.root)))

    async def test_report_collect_and_owned_download_use_same_root(self) -> None:
        output = ToolRegistry.get_tool("markdown_report_generator").invoke({
            "title": "Fixture report", "filename": "fixture.md",
            "sections": [{"header": "Evidence", "content": "Observed price: $10."}],
        })
        data = json.loads(output)["data"]
        source = Path(data["file_path"])
        self.assertEqual(source.parent, self.root / "reports")
        self.assertEqual(data["relative_path"], "reports/fixture.md")
        run = RunDocument(run_id="custom-root-run", flow_id="fixture", user_id="owner")
        runs = PostgresRunRepository()
        await runs.save(run)
        repository = PostgresResearchRepository()
        storage = LocalArtifactStorage()
        service = RunService(runs, None, AsyncMock(), research_repository=repository, artifact_storage=storage)
        await service._persist_research_output(run, "report_agent", {
            "result_storage": [{"task_id": 1, "result": output}],
        })
        outside = self.workspace / "private.md"
        outside.write_text("private", encoding="utf-8")
        await service._persist_research_output(run, "report_agent", {
            "result_storage": [{"task_id": 2, "result": "not an artifact", "artifact_paths": [str(outside)]}],
        })
        artifacts = await repository.list_artifacts(run.run_id)
        self.assertEqual(len(artifacts), 1)
        artifact = artifacts[0]
        self.assertEqual(storage.resolve(artifact.storage_uri).read_bytes(), source.read_bytes())
        self.assertEqual(artifact.size_bytes, source.stat().st_size)
        self.assertFalse((self.workspace / "workspace_data" / "reports" / "fixture.md").exists())
        url = f"/api/v1/runs/{run.run_id}/artifacts/{artifact.id}/download"
        with patch.dict(app.dependency_overrides, {
            get_run_query_service: lambda: service, get_current_user_id: lambda: "owner",
        }):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, source.read_bytes())
                app.dependency_overrides[get_current_user_id] = lambda: "other-owner"
                self.assertEqual((await client.get(url)).status_code, 404)

    def test_chart_producer_uses_custom_root(self) -> None:
        output = ToolRegistry.get_tool("chart_generator").invoke({
            "title": "Prices", "labels": ["A", "B"], "values": [10, 20], "filename": "prices",
        })
        data = json.loads(output)["data"]
        storage = LocalArtifactStorage()
        for key in ("svg_path", "spec_path"):
            self.assertEqual(Path(data[key]).parent, self.root / "charts")
            self.assertTrue(storage.is_generated_file(data[key]))

    def test_collection_rejects_other_roots_and_non_generated_files(self) -> None:
        storage = LocalArtifactStorage()
        outside = self.workspace / "reports" / "outside.md"
        outside.parent.mkdir()
        outside.write_text("private", encoding="utf-8")
        unrelated = self.root / "private.txt"
        unrelated.write_text("private", encoding="utf-8")
        self.assertFalse(storage.is_generated_file(str(outside)))
        self.assertFalse(storage.is_generated_file(str(unrelated)))
        self.assertFalse(storage.is_generated_file(str(self.root / "reports" / "missing.md")))
        with self.assertRaises(ValueError):
            storage.resolve("../outside.md")
        with self.assertRaises(ValueError):
            generated_file("reports", "../../outside.md")

    def test_redirected_output_directory_is_rejected(self) -> None:
        outside = self.workspace / "outside"
        outside.mkdir()
        self.root.mkdir()
        try:
            (self.root / "reports").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Creating symlinks is not permitted on this host")
        with self.assertRaises(ValueError):
            generated_directory("reports")
        source = outside / "private.md"
        source.write_text("private", encoding="utf-8")
        self.assertFalse(LocalArtifactStorage().is_generated_file(str(self.root / "reports" / source.name)))
