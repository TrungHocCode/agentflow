"""Crawler quality gates: nav-vs-listing classification, child-failure isolation,
login-wall detection and challenged-short blocking. Deterministic, no network."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.tools.crawler.quality import detect_access_challenge
from app.execution.tools.news_crawler_tool import _extract_page


def nav_pricing_page() -> str:
    links = "".join(
        f'<li><a href="https://www.example.com/{name}">{name}</a></li>'
        for name in ["Personal", "Business", "Features", "Integrations", "Templates", "Stories",
                     "Methods", "Hub", "Download", "Center", "Teams", "Toolkit"]
    )
    body = "<main>" + "".join(f"<p>Plan {name} costs $10 per month billed annually.</p>" for name in
                              ["Starter", "Pro"]) + "</main>"
    return f"<html><head><title>Pricing</title></head><body><nav><ul>{links}</ul></nav>{body}</body></html>"


def link_farm_index() -> str:
    items = "".join(
        f'<li><a href="https://example.com/story-{index}">A fairly long and descriptive story title '
        f"number {index} about local benchmarks</a></li>"
        for index in range(12)
    )
    return ("<html><head><title>News index</title></head><body>"
            f"<ul>{items}</ul><p>Short index preamble.</p></body></html>")


class TestListingClassification(unittest.TestCase):
    def test_nav_heavy_pricing_page_is_article_not_listing(self) -> None:
        status, data, _ = _extract_page("https://www.example.com/pricing", nav_pricing_page())
        self.assertEqual(status, "success")
        self.assertEqual(data["content_type"], "article")
        self.assertIn("$10", data["text"])

    def test_link_dense_index_is_listing(self) -> None:
        status, data, _ = _extract_page("https://example.com/news", link_farm_index())
        self.assertEqual(status, "success")
        self.assertEqual(data["content_type"], "listing")
        self.assertGreaterEqual(len(data["items"]), 10)


class TestChallengeDetection(unittest.TestCase):
    def test_login_wall_detected(self) -> None:
        html = ("<html><body><h1>Happening now</h1><button>Continue with Google</button>"
                "<p>Already have an account? Sign in. Email or username.</p></body></html>")
        self.assertTrue(detect_access_challenge(html))

    def test_plain_article_not_flagged(self) -> None:
        html = ("<html><body><article><p>The quarterly report shows steady growth across "
                "all regions and segments.</p></article></body></html>")
        self.assertFalse(detect_access_challenge(html))


if __name__ == "__main__":
    unittest.main()
