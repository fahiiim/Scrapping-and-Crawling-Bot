"""Command-line entry point."""

from __future__ import annotations

import argparse
import logging
from pathlib import Path

from .config import Settings
from .exporter import clean_existing_csv, read_csv, write_csv
from .scraper import RuntimeStats, VevorScraper


def _non_negative(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="VEVOR public product scraper")
    commands = parser.add_subparsers(dest="command", required=True)
    scrape = commands.add_parser("scrape", help="crawl, scrape, and write CSV")
    scrape.add_argument(
        "--max-products",
        type=_non_negative,
        default=None,
        help="stop after this many product URLs (0 = unlimited)",
    )
    scrape.add_argument(
        "--max-pages",
        type=_non_negative,
        default=None,
        help="stop discovery after this many listing pages (0 = unlimited)",
    )
    commands.add_parser("export", help="clean and deduplicate the current CSV")
    commands.add_parser("stats", help="show stats available from the current CSV")
    return parser


def print_report(stats: RuntimeStats, rows: int) -> None:
    print(f"Categories found: {stats.categories_found}")
    print(f"Discovered URLs: {stats.discovered_urls}")
    print(f"Product URLs found: {stats.product_urls_found}")
    print(f"Products scraped: {stats.scraped_products}")
    print(f"Failed products: {stats.failed_products}")
    print(f"Failures: {stats.failures}")
    print(f"Skipped: {stats.skipped}")
    print(f"Total output rows: {rows}")


def scrape_command(settings: Settings) -> int:
    products, stats = VevorScraper(settings).run()
    if stats.interrupted and not products:
        rows = len(read_csv(settings.output_path))
    else:
        rows = write_csv(products, settings.output_path)
    print_report(stats, rows)
    if stats.interrupted:
        if products:
            print("Interrupted: partial in-memory results saved")
        else:
            print("Interrupted: existing CSV preserved")
        return 130
    return 1 if stats.failures and rows == 0 else 0


def export_command(output_path: Path) -> int:
    rows = clean_existing_csv(output_path)
    print(f"Total output rows: {rows}")
    return 0


def stats_command(output_path: Path) -> int:
    rows = read_csv(output_path)
    print("Discovered URLs: unavailable (CSV-only storage)")
    print(f"Scraped products: {len(rows)}")
    print("Failed products: unavailable (CSV-only storage)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    try:
        if args.command == "scrape":
            settings = Settings.from_env(
                max_crawl_pages=args.max_pages,
                max_products=args.max_products,
            )
            return scrape_command(settings)
        settings = Settings.from_env()
        if args.command == "export":
            return export_command(settings.output_path)
        return stats_command(settings.output_path)
    except KeyboardInterrupt:
        logging.error("Interrupted")
        return 130
    except (OSError, RuntimeError, ValueError) as exc:
        logging.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
