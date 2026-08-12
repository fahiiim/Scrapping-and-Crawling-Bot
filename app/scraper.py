"""Crawl and product-scrape orchestration."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from .config import Settings
from .crawler import CrawlResult, Crawler, SafeHttpClient
from .parser import parse_product


LOGGER = logging.getLogger(__name__)


@dataclass
class RuntimeStats:
    categories_found: int = 0
    discovered_urls: int = 0
    product_urls_found: int = 0
    scraped_products: int = 0
    failed_products: int = 0
    failed_pages: int = 0
    skipped: int = 0
    interrupted: bool = False

    @property
    def failures(self) -> int:
        return self.failed_pages + self.failed_products


class VevorScraper:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def run(self) -> tuple[list[dict[str, str]], RuntimeStats]:
        client = SafeHttpClient(self.settings)
        crawler = Crawler(client, self.settings)
        products: list[dict[str, str]] = []
        seen: set[str] = set()
        stats = RuntimeStats()

        try:
            try:
                crawl = crawler.discover()
            except KeyboardInterrupt:
                crawl = crawler.snapshot()
                stats.interrupted = True

            self._apply_crawl_stats(stats, crawl)
            if not stats.interrupted:
                for index, url in enumerate(crawl.product_urls, start=1):
                    LOGGER.info("Scraping product %s/%s", index, len(crawl.product_urls))
                    try:
                        html = client.get_html(url)
                        if html is None:
                            stats.failed_products += 1
                            continue
                        product = parse_product(html, url)
                        if not product["Name"] or not product["URL"]:
                            stats.failed_products += 1
                            LOGGER.error("Required product data missing for %s", url)
                            continue
                        key = product["URL"] or product["SKU"]
                        if key in seen:
                            stats.skipped += 1
                            continue
                        seen.add(key)
                        products.append(product)
                        stats.scraped_products += 1
                    except KeyboardInterrupt:
                        stats.interrupted = True
                        break
                    except Exception:
                        stats.failed_products += 1
                        LOGGER.exception("Product scrape failed for %s", url)
        finally:
            client.close()
        return products, stats

    @staticmethod
    def _apply_crawl_stats(stats: RuntimeStats, crawl: CrawlResult) -> None:
        stats.categories_found = len(crawl.categories)
        stats.discovered_urls = crawl.discovered_urls
        stats.product_urls_found = len(crawl.product_urls)
        stats.failed_pages = crawl.failed_pages
        stats.skipped = crawl.skipped
