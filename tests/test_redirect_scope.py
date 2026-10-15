"""Same-site redirect tolerance; deterministic fixtures, no network."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.tools.network_policy import same_site, validate_redirect_target
from app.shared.collection_scope import collection_scope


class TestSameSite(unittest.TestCase):
    def test_equal_and_www_variants_match(self) -> None:
        self.assertTrue(same_site("todoist.com", "todoist.com"))
        self.assertTrue(same_site("www.todoist.com", "todoist.com"))
        self.assertTrue(same_site("todoist.com", "www.todoist.com"))
        self.assertTrue(same_site("docs.todoist.com", "todoist.com"))

    def test_lookalikes_and_foreign_hosts_rejected(self) -> None:
        self.assertFalse(same_site("eviltodoist.com", "todoist.com"))
        self.assertFalse(same_site("todoist.evil.com", "todoist.com"))
        self.assertFalse(same_site("todoist.com.evil.com", "todoist.com"))
        self.assertFalse(same_site("clickup.com", "todoist.com"))
        self.assertFalse(same_site("", "todoist.com"))


class TestRedirectTarget(unittest.TestCase):
    def test_same_site_redirect_allowed_without_scope(self) -> None:
        url, error = validate_redirect_target("https://todoist.com/pricing", "/pricing")
        self.assertEqual((url, error), ("https://todoist.com/pricing", None))
        url, error = validate_redirect_target("https://todoist.com/pricing",
                                              "https://www.todoist.com/pricing")
        self.assertEqual(error, None)
        self.assertIn("www.todoist.com", url)

    def test_off_site_redirect_rejected(self) -> None:
        url, error = validate_redirect_target("https://todoist.com/pricing",
                                              "https://evil.invalid/pricing")
        self.assertEqual((url, error), (None, "Redirect leaves the approved site."))

    def test_credentials_ports_and_private_still_blocked(self) -> None:
        url, error = validate_redirect_target("https://todoist.com/pricing",
                                              "https://user:pass@todoist.com/")
        self.assertIn("credentials", error)
        url, error = validate_redirect_target("https://todoist.com/pricing", "http://127.0.0.1/")
        self.assertIsNotNone(error)

    def test_scoped_collection_keeps_working(self) -> None:
        from app.execution.tools.network_policy import validate_external_url
        with collection_scope(["https://todoist.com/pricing"]):
            url, error = validate_external_url("https://todoist.com/pricing")
            self.assertEqual(error, None)
            url, error = validate_external_url("https://www.todoist.com/pricing")
            self.assertEqual(error, "URL is outside the explicitly approved collection scope.")
            redirect, redirect_error = validate_redirect_target("https://todoist.com/pricing",
                                                                "https://www.todoist.com/pricing")
            self.assertEqual(redirect_error, None)
            self.assertIn("www.todoist.com", redirect)


if __name__ == "__main__":
    unittest.main()
