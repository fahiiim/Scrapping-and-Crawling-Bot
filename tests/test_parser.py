"""Parser tests using representative static HTML."""

import csv
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.crawler import RobotsPolicy, is_listing_url, normalize_url
from app.exporter import write_csv
from app.main import scrape_command
from app.parser import FIELDS, parse_product
from app.scraper import RuntimeStats


PRODUCT = {
    "@context": "https://schema.org",
    "@type": "Product",
    "name": "VEVOR Cordless Drill",
    "sku": "DRILL-120V",
    "description": "A compact drill for workshop use.",
    "offers": {
        "@type": "Offer",
        "price": "89.99",
        "availability": "https://schema.org/InStock",
    },
    "aggregateRating": {"ratingValue": "4.7", "reviewCount": "23"},
    "additionalProperty": [
        {"@type": "PropertyValue", "name": "Material", "value": "Steel"},
        {"@type": "PropertyValue", "name": "Voltage", "value": "120V"},
        {"@type": "PropertyValue", "name": "Power", "value": "1500W"},
    ],
}
BREADCRUMBS = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": "Tools"},
        {"@type": "ListItem", "position": 2, "name": "Power Tools"},
        {"@type": "ListItem", "position": 3, "name": "Drills"},
    ],
}
BASE_URL = "https://www.vevor.com/"


def fixture(product: dict = PRODUCT) -> str:
    return f"""
    <html><head>
      <link rel="canonical" href="https://www.vevor.com/drills-c_1/drill-p_123?track=x">
      <script type="application/ld+json">{json.dumps(product)}</script>
      <script type="application/ld+json">{json.dumps(BREADCRUMBS)}</script>
    </head><body><h1>Fallback title</h1></body></html>
    """


class ParserTests(unittest.TestCase):
    def setUp(self) -> None:
        self.result = parse_product(fixture(), "https://www.vevor.com/fallback-p_123")

    def test_name_extraction(self) -> None:
        self.assertEqual(self.result["Name"], "VEVOR Cordless Drill")

    def test_price_extraction(self) -> None:
        self.assertEqual(self.result["Price"], "89.99")

    def test_sku_extraction(self) -> None:
        self.assertEqual(self.result["SKU"], "DRILL-120V")

    def test_category_extraction(self) -> None:
        self.assertEqual(self.result["Category"], "Tools > Power Tools > Drills")

    def test_specs_parsing(self) -> None:
        self.assertEqual(
            self.result["Specifications"],
            "Material: Steel; Voltage: 120V; Power: 1500W",
        )

    def test_rating_parsing(self) -> None:
        self.assertEqual(self.result["Rating"], "4.7")

    def test_status_detection(self) -> None:
        sold_out = dict(PRODUCT)
        sold_out["offers"] = {
            "price": "89.99",
            "availability": "https://schema.org/OutOfStock",
        }
        result = parse_product(fixture(sold_out), "https://www.vevor.com/fallback-p_123")
        self.assertEqual(self.result["Status"], "active")
        self.assertEqual(result["Status"], "out_of_stock")

    def test_offers_list(self) -> None:
        product = dict(PRODUCT)
        product["offers"] = [{"price": "71.25", "availability": "https://schema.org/InStock"}]
        result = parse_product(fixture(product), "https://www.vevor.com/fallback-p_123")
        self.assertEqual(result["Price"], "71.25")
        self.assertEqual(result["Status"], "active")

    def test_live_html_fallbacks(self) -> None:
        html = """
        <html><head><link rel="canonical" href="/drills-c_1/drill-p_123"></head><body>
          <h1 class="DM_goodTitle_wp">Fallback Drill</h1>
          <span class="js-copy" data-code="FALLBACK-1"></span>
          <div id="js-DM_goodPrice">$ <span data-int>35</span><span data-decimal>90</span></div>
          <nav class="gPath"><a class="gPath_link">Tools</a><a class="gPath_link">Drills</a></nav>
          <div id="js-DM_at-goodDescContent"><style>.noise { color: red }</style>
            <div id="js-DM_at-goodDescRich"><p>Main workshop description.</p></div>
          </div>
          <div id="js-DM_productSpec"><div class="DM_PS-item">
            <span class="DM_PS-label">Material</span><span class="DM_PS-value">Steel</span>
          </div></div>
          <span class="detailAttr_out_of_stock">Out of Stock</span>
          <span class="DM_gsw-SEO_stock">In Stock</span>
          <span class="js-DM_gt-rate">4.6</span>
        </body></html>
        """
        result = parse_product(html, "https://www.vevor.com/fallback-p_123")
        self.assertEqual(result["Name"], "Fallback Drill")
        self.assertEqual(result["SKU"], "FALLBACK-1")
        self.assertEqual(result["Price"], "35.90")
        self.assertEqual(result["Description"], "Main workshop description.")
        self.assertEqual(result["Specifications"], "Material: Steel")
        self.assertEqual(result["Rating"], "4.6")
        self.assertEqual(result["Status"], "active")

    def test_embedded_stock_fallback(self) -> None:
        html = """
        <html><body><h1>Unavailable Drill</h1>
        <script>window.PRODUCT_DATA = {"stock":0,"title":"Unavailable Drill"};</script>
        </body></html>
        """
        result = parse_product(html, "https://www.vevor.com/drills-c_1/drill-p_123")
        self.assertEqual(result["Status"], "out_of_stock")

    def test_description_and_canonical_url(self) -> None:
        self.assertEqual(self.result["Description"], "A compact drill for workshop use.")
        self.assertEqual(
            self.result["URL"], "https://www.vevor.com/drills-c_1/drill-p_123"
        )


class CrawlerTests(unittest.TestCase):
    def test_normalize_url(self) -> None:
        self.assertEqual(
            normalize_url("http://vevor.com/tools-c_1?page=2&utm=x#top", BASE_URL),
            "https://www.vevor.com/tools-c_1?page=2",
        )
        self.assertIsNone(normalize_url("https://example.com/tools-c_1", BASE_URL))
        self.assertIsNone(normalize_url("/products/private-p_123", BASE_URL))

    def test_multi_segment_search_listing(self) -> None:
        url = "https://www.vevor.com/s/tools-power-tools/power-tools-c_10025?page=2"
        self.assertTrue(is_listing_url(url, BASE_URL))

    def test_robots_wildcards_allow_and_delay(self) -> None:
        policy = RobotsPolicy(
            """
            User-agent: *
            Crawl-delay: 3
            Disallow: /*?sort
            Disallow: /products/*
            Allow: /products/public
            Disallow: /*.mp4$
            """,
            "PublicCatalogScraper/1.0",
        )
        self.assertEqual(policy.crawl_delay, 3)
        self.assertFalse(policy.allowed(f"{BASE_URL}tools-c_1?sort=price"))
        self.assertFalse(policy.allowed(f"{BASE_URL}products/private"))
        self.assertTrue(policy.allowed(f"{BASE_URL}products/public"))
        self.assertFalse(policy.allowed(f"{BASE_URL}video.mp4"))


class ExporterAndCliTests(unittest.TestCase):
    def test_exact_header_deduplication_and_invalid_row_cleanup(self) -> None:
        valid = {
            "Name": "Drill",
            "URL": "https://www.vevor.com/drills-c_1/drill-p_123",
            "Status": "active",
        }
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "products.csv"
            count = write_csv(
                [{}, valid, valid, {"Name": "Bad", "Status": "unknown"}],
                output,
            )
            with output.open(encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                self.assertEqual(reader.fieldnames, list(FIELDS))
            self.assertEqual(count, 1)
            self.assertEqual(len(rows), 1)

    def test_early_interrupt_preserves_existing_csv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "products.csv"
            original = {
                "Name": "Existing",
                "URL": "https://www.vevor.com/tools-c_1/existing-p_123",
                "Status": "active",
            }
            write_csv([original], output)
            before = output.read_bytes()
            stats = RuntimeStats(interrupted=True)
            settings = Settings(output_path=output)
            with patch("app.main.VevorScraper.run", return_value=([], stats)):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(scrape_command(settings), 130)
            self.assertEqual(output.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
