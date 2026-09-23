import json
import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.modules.runs.models import RunCreateRequest
from app.modules.runs.service import RunService


class RunArtifactDiscoveryTests(unittest.TestCase):
    def test_parses_managed_artifact_paths_from_generator_tool_json(self) -> None:
        tool_result = json.dumps({
            "ok": True,
            "status": "success",
            "data": {
                "file_path": "workspace_data/reports/research.md",
                "svg_path": "workspace_data/charts/research.svg",
                "spec_path": "workspace_data/charts/research.json",
            },
        })

        paths = RunService._find_artifact_paths(tool_result)

        self.assertEqual(paths, [
            "workspace_data/reports/research.md",
            "workspace_data/charts/research.svg",
            "workspace_data/charts/research.json",
        ])

    def test_parses_legacy_generated_path_labels(self) -> None:
        paths = RunService._find_artifact_paths(
            "Report generated. File Path: workspace_data/reports/result.md\n"
            "Chart Spec Path: workspace_data/charts/plot.json"
        )

        self.assertEqual(paths, [
            "workspace_data/reports/result.md",
            "workspace_data/charts/plot.json",
        ])

    def test_run_create_request_accepts_conversation_link(self) -> None:
        request = RunCreateRequest(conversation_id="conversation-1")

        self.assertEqual(request.conversation_id, "conversation-1")
