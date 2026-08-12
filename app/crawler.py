"""Polite in-memory URL discovery."""

from __future__ import annotations

import logging
import re
import time
from collections import deque
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

from .config import Settings


LOGGER = logging.getLogger(__name__)
PRODUCT_RE = re.compile(r"-p_\d+(?:\.html)?/?$", re.IGNORECASE)
CATEGORY_RE = re.compile(r"-c_\d+/?$", re.IGNORECASE)
SEARCH_RE = re.compile(r"^/s/(?:[^/]+/)*[^/]+/?$", re.IGNORECASE)
BLOCKED_PATH_PARTS = {
    "account",
    "api",
    "blog",
    "cart",
    "checkout",
    "collections",
    "customer",
    "help",
    "legal",
    "login",
    "order",
    "privacy",
    "products",
    "reviews",
    "terms",
    "user",
    "wp-admin",
}
RETRY_STATUSES = {408, 425, 429, 500, 502, 503, 504}


@dataclass(frozen=True)
class _RobotsRule:
    allow: bool
    pattern: re.Pattern[str]
    specificity: int


class RobotsPolicy:
    """Small robots.txt policy with wildcard and end-anchor support."""

    def __init__(self, text: str, user_agent: str) -> None:
        groups = self._parse_groups(text)
        matches: list[tuple[int, list[tuple[bool, str]], float | None]] = []
        agent_text = user_agent.casefold()
        for agents, rules, delay in groups:
            specificity = max(
                (
                    0
                    if agent == "*"
                    else len(agent)
                    if agent.casefold() in agent_text
                    else -1
                    for agent in agents
                ),
                default=-1,
            )
            if specificity >= 0:
                matches.append((specificity, rules, delay))

        best = max((match[0] for match in matches), default=-1)
        selected = [match for match in matches if match[0] == best]
        self.rules = tuple(
            self._compile_rule(allow, pattern)
            for _, rules, _ in selected
            for allow, pattern in rules
            if pattern
        )
        self.crawl_delay = next(
            (delay for _, _, delay in selected if delay is not None), None
        )

    @staticmethod
    def _parse_groups(
        text: str,
    ) -> list[tuple[list[str], list[tuple[bool, str]], float | None]]:
        groups: list[tuple[list[str], list[tuple[bool, str]], float | None]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        delay: float | None = None
        directives_started = False

        def finish() -> None:
            nonlocal agents, rules, delay, directives_started
            if agents:
                groups.append((agents, rules, delay))
            agents, rules, delay, directives_started = [], [], None, False

        for raw_line in text.splitlines():
            line = raw_line.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, value = (part.strip() for part in line.split(":", 1))
            field = field.casefold()
            if field == "user-agent":
                if directives_started:
                    finish()
                agents.append(value.casefold())
                continue
            if not agents:
                continue
            directives_started = True
            if field in {"allow", "disallow"}:
                if value:
                    rules.append((field == "allow", value))
            elif field == "crawl-delay":
                try:
                    parsed = float(value)
                    if parsed >= 0:
                        delay = parsed
                except ValueError:
                    continue
        finish()
        return groups

    @staticmethod
    def _compile_rule(allow: bool, pattern: str) -> _RobotsRule:
        end_anchored = pattern.endswith("$")
        body = pattern[:-1] if end_anchored else pattern
        expression = re.escape(body).replace(r"\*", ".*")
        if end_anchored:
            expression += "$"
        specificity = len(body.replace("*", ""))
        return _RobotsRule(allow, re.compile(f"^{expression}"), specificity)

    def allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        target = parts.path or "/"
        if parts.query:
            target += f"?{parts.query}"
        matches = [rule for rule in self.rules if rule.pattern.search(target)]
        if not matches:
            return True
        winner = max(matches, key=lambda rule: (rule.specificity, rule.allow))
        return winner.allow


def normalize_url(url: str, base_url: str) -> str | None:
    """Return one canonical crawl URL or None for an external/invalid URL."""
    absolute = urljoin(base_url, unescape(url.strip()))
    parts = urlsplit(absolute)
    base = urlsplit(base_url)
    if parts.scheme.lower() not in {"http", "https"}:
        return None
    hostname = (parts.hostname or "").lower()
    base_host = (base.hostname or "").lower()
    root_host = base_host.removeprefix("www.")
    if hostname not in {root_host, f"www.{root_host}"}:
        return None

    path = re.sub(r"/{2,}", "/", parts.path or "/")
    path = path.rstrip("/") or "/"
    lowered_segments = {segment.lower() for segment in path.split("/") if segment}
    if lowered_segments & BLOCKED_PATH_PARTS:
        return None

    if PRODUCT_RE.search(path):
        query = ""
    else:
        page_values = [
            (key.lower(), value)
            for key, value in parse_qsl(parts.query, keep_blank_values=False)
            if key.lower() == "page" and value.isdigit() and int(value) > 1
        ]
        query = urlencode(sorted(set(page_values)))

    return urlunsplit(("https", base_host, path, query, ""))


def is_product_url(url: str) -> bool:
    return bool(PRODUCT_RE.search(urlsplit(url).path))


def is_listing_url(url: str, base_url: str) -> bool:
    parts = urlsplit(url)
    return url == normalize_url(base_url, base_url) or bool(
        CATEGORY_RE.search(parts.path) or SEARCH_RE.search(parts.path)
    )


@dataclass(frozen=True)
class CrawlResult:
    product_urls: list[str]
    categories: set[str]
    visited_pages: set[str]
    discovered_pages: set[str]
    failed_pages: int
    skipped: int

    @property
    def discovered_urls(self) -> int:
        return len(self.product_urls) + len(self.discovered_pages)


class SafeHttpClient:
    """HTTP client with robots checks, throttling, retries, and timeouts."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": settings.user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.8",
            }
        )
        self._last_request_at = 0.0
        self.delay = settings.delay
        self._robots = self._load_robots()
        robots_delay = self._robots.crawl_delay
        self.delay = max(settings.delay, float(robots_delay or 0))

    def _throttle(self) -> None:
        remaining = self.delay - (time.monotonic() - self._last_request_at)
        if remaining > 0:
            time.sleep(remaining)

    def _request(self, url: str, *, check_robots: bool) -> requests.Response | None:
        if check_robots and not self.allowed(url):
            LOGGER.warning("robots.txt disallows %s", url)
            return None

        for attempt in range(self.settings.retries + 1):
            self._throttle()
            try:
                response = self.session.get(
                    url,
                    timeout=self.settings.timeout,
                    allow_redirects=True,
                )
                self._last_request_at = time.monotonic()
            except requests.RequestException as exc:
                self._last_request_at = time.monotonic()
                if attempt >= self.settings.retries:
                    LOGGER.error("Request failed for %s: %s", url, exc)
                    return None
                wait = min(60.0, self.settings.backoff * (2**attempt))
                LOGGER.warning("Request error for %s; retrying in %.1fs", url, wait)
                time.sleep(wait)
                continue

            if response.status_code == 200:
                final = normalize_url(response.url, self.settings.base_url)
                if check_robots and (not final or not self.allowed(response.url)):
                    LOGGER.warning("Redirect target is not crawlable: %s", response.url)
                    return None
                return response

            if response.status_code not in RETRY_STATUSES or attempt >= self.settings.retries:
                LOGGER.error("HTTP %s for %s", response.status_code, url)
                return None

            wait = self._retry_wait(response, attempt)
            LOGGER.warning(
                "HTTP %s for %s; retrying in %.1fs",
                response.status_code,
                url,
                wait,
            )
            time.sleep(wait)
        return None

    def _retry_wait(self, response: requests.Response, attempt: int) -> float:
        value = response.headers.get("Retry-After", "")
        if value.isdigit():
            return min(60.0, float(value))
        if value:
            try:
                seconds = parsedate_to_datetime(value).timestamp() - time.time()
                return min(60.0, max(0.0, seconds))
            except (TypeError, ValueError, OverflowError):
                pass
        return min(60.0, self.settings.backoff * (2**attempt))

    def _load_robots(self) -> RobotsPolicy:
        robots_url = urljoin(self.settings.base_url, "/robots.txt")
        response = self._request(robots_url, check_robots=False)
        if response is None:
            raise RuntimeError("Could not load robots.txt; crawl stopped safely")
        return RobotsPolicy(response.text, self.settings.user_agent)

    def allowed(self, url: str) -> bool:
        return self._robots.allowed(url)

    def get_html(self, url: str) -> str | None:
        response = self._request(url, check_robots=True)
        if response is None:
            return None
        content_type = response.headers.get("Content-Type", "").lower()
        if "html" not in content_type:
            LOGGER.error("Non-HTML response for %s", url)
            return None
        text = response.text
        sample = text[:100_000].lower()
        if any(
            marker in sample
            for marker in (
                "verify you are human",
                "captcha challenge",
                "cf-chl-captcha",
            )
        ):
            LOGGER.error("Human verification encountered at %s; skipping", url)
            return None
        return text

    def close(self) -> None:
        self.session.close()


class Crawler:
    def __init__(self, client: SafeHttpClient, settings: Settings) -> None:
        self.client = client
        self.settings = settings
        start = normalize_url(settings.base_url, settings.base_url)
        if not start:
            raise ValueError("Invalid VEVOR_BASE_URL")
        self.queue: deque[str] = deque([start])
        self.queued: set[str] = {start}
        self.visited_pages: set[str] = set()
        self.product_urls: list[str] = []
        self._product_set: set[str] = set()
        self.categories: set[str] = set()
        self.failed_pages = 0
        self.skipped = 0

    def discover(self) -> CrawlResult:
        while self.queue:
            if self._limit_reached():
                break
            url = self.queue.popleft()
            if url in self.visited_pages:
                continue
            if (
                self.settings.max_crawl_pages
                and len(self.visited_pages) >= self.settings.max_crawl_pages
            ):
                break

            self.visited_pages.add(url)
            LOGGER.info("Discovering %s", url)
            html = self.client.get_html(url)
            if html is None:
                self.failed_pages += 1
                continue
            self._collect_links(html, url)
        return self.snapshot()

    def _collect_links(self, html: str, page_url: str) -> None:
        soup = BeautifulSoup(html, "html.parser")
        links = [str(anchor["href"]) for anchor in soup.find_all("a", href=True)]
        links.extend(
            str(link["href"])
            for link in soup.find_all("link", href=True)
            if any(
                relation in {"next", "prev"}
                for relation in (link.get("rel") or [])
            )
        )
        for href in links:
            url = normalize_url(href, page_url)
            if not url:
                continue
            if is_product_url(url):
                if url in self._product_set:
                    continue
                if not self.client.allowed(url):
                    self.skipped += 1
                    continue
                self._product_set.add(url)
                self.product_urls.append(url)
                if self._limit_reached():
                    return
            elif is_listing_url(url, self.settings.base_url):
                category_url = urlunsplit((*urlsplit(url)[:3], "", ""))
                if CATEGORY_RE.search(urlsplit(category_url).path):
                    self.categories.add(category_url)
                if url not in self.queued:
                    self.queued.add(url)
                    self.queue.append(url)

    def _limit_reached(self) -> bool:
        return bool(
            self.settings.max_products
            and len(self.product_urls) >= self.settings.max_products
        )

    def snapshot(self) -> CrawlResult:
        return CrawlResult(
            product_urls=list(self.product_urls),
            categories=set(self.categories),
            visited_pages=set(self.visited_pages),
            discovered_pages=set(self.queued),
            failed_pages=self.failed_pages,
            skipped=self.skipped,
        )
