"""MyProduct contract validation."""

import unittest

from pydantic import ValidationError

from app.modules.competitive_intelligence.models import MyProduct


class TestMyProductContract(unittest.TestCase):
    def test_defaults_and_valid_website(self) -> None:
        product = MyProduct(name="Cafe POS", website="https://example.com/pos")
        self.assertEqual((product.market, product.known_competitors, product.is_default), ("global", [], False))

    def test_rejects_bad_input(self) -> None:
        for bad in ({"website": "http://localhost/"}, {"website": "ftp://example.com"}, {"market": "us"},
                    {"known_competitors": [f"Rival {i}" for i in range(21)]}, {"name": ""}):
            with self.subTest(bad=bad), self.assertRaises(ValidationError):
                MyProduct(**{"name": "Cafe POS", **bad})


if __name__ == "__main__":
    unittest.main()
