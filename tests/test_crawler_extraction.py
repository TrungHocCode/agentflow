"""Crawler extraction keeps pricing content held in generic containers."""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend")))

from app.execution.tools.news_crawler_tool import _extract_page


class TestCrawlerExtraction(unittest.TestCase):
    def test_prices_in_generic_containers_are_captured(self) -> None:
        html = (
            "<html><head><title>Pricing</title></head><body><main>"
            "<h1>Plans and pricing</h1>"
            "<div class='card'><span class='price'>$10</span> per month, billed yearly, "
            "includes unlimited projects and priority support</div>"
            "<div class='card'><span class='price'>$20</span> per month, billed monthly, "
            "includes admin controls and single sign-on</div>"
            "</main></body></html>"
        )
        status, data, _ = _extract_page("https://example.com/pricing", html)
        self.assertEqual(status, "success")
        self.assertIn("$10", data["text"])
        self.assertIn("$20", data["text"])

    def test_table_cells_are_captured(self) -> None:
        html = (
            "<html><head><title>Plans</title></head><body><main><h1>Compare plans</h1>"
            "<table><tr><th>Plan</th><th>Price</th></tr>"
            "<tr><td>Starter</td><td>$5 per user per month</td></tr>"
            "<tr><td>Business</td><td>$12 per user per month</td></tr></table>"
            "</main></body></html>"
        )
        status, data, _ = _extract_page("https://example.com/pricing", html)
        self.assertEqual(status, "success")
        self.assertIn("$5", data["text"])
        self.assertIn("$12", data["text"])

    def test_json_ld_prices_are_captured(self) -> None:
        html = (
            "<html><head><title>Pricing</title>"
            "<script type='application/ld+json'>"
            '{"@type":"Product","name":"Pro Plan","offers":{"price":"10","priceCurrency":"USD"}}'
            "</script></head><body><main><h1>Pricing</h1>"
            "<p>Choose the plan that fits your team and scale as your needs grow over time.</p>"
            "</main></body></html>"
        )
        status, data, _ = _extract_page("https://example.com/pricing", html)
        self.assertEqual(status, "success")
        self.assertIn("Pro Plan", data["text"])

    def test_next_data_prices_are_captured(self) -> None:
        html = (
            "<html><head><title>Plans</title>"
            "<script id='__NEXT_DATA__' type='application/json'>"
            '{"props":{"pageProps":{"plans":[{"name":"Business","price":"$20"}]}}}'
            "</script></head><body><main><h1>Plans</h1>"
            "<p>Compare every plan and pick the one that matches your team size today.</p>"
            "</main></body></html>"
        )
        status, data, _ = _extract_page("https://example.com/pricing", html)
        self.assertEqual(status, "success")
        self.assertIn("Business", data["text"])
        self.assertIn("$20", data["text"])


if __name__ == "__main__":
    unittest.main()
