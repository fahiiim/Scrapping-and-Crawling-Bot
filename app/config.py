"""Runtime configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")


def _int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


def _float_env(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value < 0:
        raise ValueError(f"{name} must be zero or greater")
    return value


@dataclass(frozen=True)
class Settings:
    base_url: str = "https://www.vevor.com/"
    output_path: Path = PROJECT_ROOT / "data" / "vevor_products.csv"
    user_agent: str = "VEVORPublicCatalogScraper/1.0 (+https://www.vevor.com/)"
    delay: float = 3.0
    timeout: float = 30.0
    retries: int = 3
    backoff: float = 2.0
    max_crawl_pages: int = 0
    max_products: int = 0

    @classmethod
    def from_env(
        cls,
        *,
        max_crawl_pages: int | None = None,
        max_products: int | None = None,
    ) -> "Settings":
        return cls(
            user_agent=os.getenv("VEVOR_USER_AGENT", cls.user_agent),
            delay=_float_env("VEVOR_DELAY", cls.delay),
            timeout=_float_env("VEVOR_TIMEOUT", cls.timeout),
            retries=_int_env("VEVOR_RETRIES", cls.retries),
            backoff=_float_env("VEVOR_BACKOFF", cls.backoff),
            max_crawl_pages=(
                _int_env("VEVOR_MAX_CRAWL_PAGES", cls.max_crawl_pages)
                if max_crawl_pages is None
                else max_crawl_pages
            ),
            max_products=(
                _int_env("VEVOR_MAX_PRODUCTS", cls.max_products)
                if max_products is None
                else max_products
            ),
        )
