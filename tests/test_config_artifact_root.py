"""The artifact root must be absolute so the API and worker share one directory."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.core.config import settings


class TestArtifactRoot(unittest.TestCase):
    def test_artifact_root_is_absolute(self) -> None:
        # Relative roots resolve against the process working directory, so the API
        # and worker would write to different directories. It must be absolute.
        self.assertTrue(os.path.isabs(settings.ARTIFACT_ROOT))
        self.assertTrue(settings.ARTIFACT_ROOT.endswith("workspace_data"))


if __name__ == "__main__":
    unittest.main()
