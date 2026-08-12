"""VEVOR product parser."""

from __future__ import annotations

import json
import re
from html import unescape
from typing import Any, Iterable
from urllib.parse import urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag


FIELDS = (
    "Name",
    "SKU",
    "Price",
    "Category",
    "Description",
    "Specifications",
    "Rating",
    "URL",
    "Status",
)


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, Tag):
        value = value.get_text(" ", strip=True)
    return re.sub(r"\s+", " ", unescape(str(value))).strip()


def _walk_json(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_json(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_json(child)


def _json_ld(soup: BeautifulSoup) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        objects.extend(_walk_json(data))
    return objects


def _has_type(item: dict[str, Any], wanted: str) -> bool:
    kind = item.get("@type", "")
    if isinstance(kind, list):
        return any(str(value).lower() == wanted.lower() for value in kind)
    return str(kind).lower() == wanted.lower()


def _first_product(objects: list[dict[str, Any]]) -> dict[str, Any]:
    return next(
        (
            item
            for item in objects
            if _has_type(item, "Product") and (item.get("sku") or item.get("offers"))
        ),
        {},
    )


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = soup.find("meta", attrs={"property": name}) or soup.find(
            "meta", attrs={"name": name}
        )
        if tag and tag.get("content"):
            return clean_text(tag["content"])
    return ""


def parse_name(soup: BeautifulSoup, product: dict[str, Any]) -> str:
    value = clean_text(product.get("name"))
    if value:
        return value
    heading = soup.select_one(".DM_gt-title-text, h1.DM_goodTitle_wp, h1")
    return clean_text(heading) or _meta(soup, "og:title")


def parse_sku(soup: BeautifulSoup, product: dict[str, Any]) -> str:
    value = clean_text(product.get("sku") or product.get("mpn"))
    if value:
        return value
    node = soup.find(attrs={"itemprop": "sku"})
    if node:
        return clean_text(node.get("content") or node)
    node = soup.select_one(".js-copy[data-code]")
    if node and node.get("data-code"):
        return clean_text(node["data-code"])
    node = soup.select_one(".detailHeader_sku")
    if node:
        match = re.search(r"\bSKU\s*[:#]?\s*([A-Z0-9_-]+)", clean_text(node), re.I)
        if match:
            return clean_text(match.group(1))
    match = re.search(r"\bSKU\s*[:#]\s*([A-Z0-9_-]+)", soup.get_text(" "), re.I)
    return clean_text(match.group(1)) if match else ""


def _offers(product: dict[str, Any]) -> list[dict[str, Any]]:
    offers = product.get("offers", [])
    if isinstance(offers, dict):
        return [offers]
    return [offer for offer in offers if isinstance(offer, dict)] if isinstance(offers, list) else []


def parse_price(soup: BeautifulSoup, product: dict[str, Any]) -> str:
    for offer in _offers(product):
        value = clean_text(offer.get("price"))
        if value:
            return value
    value = _meta(soup, "product:price:amount", "og:price:amount")
    if value:
        return value
    for selector in (
        "[itemprop='price']",
        "[data-testid*='price']",
        ".DM_right_price_wrap[data-currency]",
        "#js-DM_goodPrice",
        "[class*='goodPrice']",
        "[class*='sale-price']",
        ".price",
    ):
        node = soup.select_one(selector)
        if node:
            integer = node.select_one("[data-int]")
            decimal = node.select_one("[data-decimal]")
            if integer:
                whole = re.sub(r"\D", "", clean_text(integer))
                fraction = re.sub(r"\D", "", clean_text(decimal)) if decimal else ""
                if whole:
                    return f"{whole}.{fraction}" if fraction else whole
            value = clean_text(node.get("content") or node)
            split = re.search(
                r"(?:US\s*)?\$\s*([0-9][0-9,]*)\s+([0-9]{2})(?:\D|$)", value
            )
            if split:
                return f"{split.group(1).replace(',', '')}.{split.group(2)}"
            match = re.search(r"(?:US\s*)?\$?\s*([0-9][0-9,]*(?:\.\d{1,2})?)", value)
            if match:
                return match.group(1).replace(",", "")
    return ""


def parse_category(soup: BeautifulSoup, objects: list[dict[str, Any]]) -> str:
    breadcrumb = next(
        (item for item in objects if _has_type(item, "BreadcrumbList")), {}
    )
    parts: list[tuple[int, str]] = []
    elements = breadcrumb.get("itemListElement", [])
    if isinstance(elements, list):
        for index, element in enumerate(elements):
            if not isinstance(element, dict):
                continue
            name = element.get("name")
            if not name and isinstance(element.get("item"), dict):
                name = element["item"].get("name")
            text = clean_text(name)
            if text and text.lower() not in {"home", "vevor"}:
                try:
                    position = int(element.get("position", index + 1))
                except (TypeError, ValueError):
                    position = index + 1
                parts.append((position, text))
    if parts:
        return " > ".join(value for _, value in sorted(parts))

    direct = [clean_text(node) for node in soup.select(".gPath .gPath_link, .gPath_link")]
    direct = [name for name in direct if name and name.lower() not in {"home", "vevor"}]
    if direct:
        return " > ".join(dict.fromkeys(direct))
    container = soup.select_one("nav[aria-label*='breadcrumb' i], [class*='breadcrumb' i]")
    if not container:
        return ""
    names = [clean_text(node) for node in container.find_all(["a", "span", "li"])]
    clean_names: list[str] = []
    for name in names:
        if name and name.lower() not in {"home", "vevor"} and name not in clean_names:
            clean_names.append(name)
    return " > ".join(clean_names)


def _description_candidates(soup: BeautifulSoup) -> Iterable[str]:
    for selector in ("#js-DM_at-goodDescRich", "#js-DM_at-goodDescContent"):
        for node in soup.select(selector):
            fragment = BeautifulSoup(str(node), "html.parser")
            for unwanted in fragment.select("style, script, noscript, button, svg"):
                unwanted.decompose()
            yield clean_text(fragment)

    feature_nodes = soup.select("#js-DM_FC-content li")
    if feature_nodes:
        yield " ".join(clean_text(node) for node in feature_nodes if clean_text(node))
    for selector in (
        "[itemprop='description']",
        "#product-description",
        "#description",
        "[class*='product-description' i]",
        "[class*='productDescription']",
        "[class*='goods-description' i]",
        "[class*='goodsDescription']",
    ):
        for node in soup.select(selector):
            yield clean_text(node)

    heading = soup.find(
        ["h2", "h3", "h4"],
        string=re.compile(r"^(?:About this item|Description)$", re.I),
    )
    if heading:
        values: list[str] = []
        for node in heading.find_all_next(["li", "p"], limit=12):
            text = clean_text(node)
            if re.fullmatch(r"Product specifications?", text, re.I):
                break
            if text and text not in values:
                values.append(text)
        if values:
            yield " ".join(values)


def parse_description(
    soup: BeautifulSoup, product: dict[str, Any]
) -> str:
    for value in _description_candidates(soup):
        if len(value) >= 20:
            return value
    return clean_text(product.get("description"))


def _spec_pairs_from_node(node: Tag) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for row in node.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) >= 2:
            pairs.append((clean_text(cells[0]), clean_text(cells[1])))
    for term in node.find_all("dt"):
        value = term.find_next_sibling("dd")
        if value:
            pairs.append((clean_text(term), clean_text(value)))
    return pairs


def _additional_properties(product: dict[str, Any]) -> list[tuple[str, str]]:
    values = product.get("additionalProperty", [])
    if isinstance(values, dict):
        values = [values]
    pairs: list[tuple[str, str]] = []
    if isinstance(values, list):
        for item in values:
            if isinstance(item, dict):
                pairs.append((clean_text(item.get("name")), clean_text(item.get("value"))))
    return pairs


def parse_specifications(soup: BeautifulSoup, product: dict[str, Any]) -> str:
    pairs = _additional_properties(product)
    if not pairs:
        for selector in (
            "#js-DM_at-goodsDescAttr",
            "#js-DM_productSpec",
            "#js-DM_PS-content",
            "#specifications",
            "[class*='specification' i]",
            "[class*='product-spec' i]",
            "[class*='parameter' i]",
        ):
            for node in soup.select(selector):
                pairs.extend(_spec_pairs_from_node(node))
                for item in node.select(".DM_PS-item"):
                    label = item.select_one(".DM_PS-label")
                    value = item.select_one(".DM_PS-value")
                    if label and value:
                        pairs.append((clean_text(label), clean_text(value)))
            if pairs:
                break
    if not pairs:
        heading = soup.find(
            ["h2", "h3", "h4"],
            string=re.compile(r"Product specifications?|Specifications?", re.I),
        )
        if heading and isinstance(heading.parent, Tag):
            pairs = _spec_pairs_from_node(heading.parent)

    seen: set[tuple[str, str]] = set()
    cleaned: list[str] = []
    for name, value in pairs:
        name = clean_text(name).rstrip(":")
        value = clean_text(value)
        key = (name.casefold(), value.casefold())
        if name and value and key not in seen:
            seen.add(key)
            cleaned.append(f"{name}: {value}")
    return "; ".join(cleaned)


def parse_rating(soup: BeautifulSoup, product: dict[str, Any]) -> str:
    rating = product.get("aggregateRating", {})
    if isinstance(rating, dict):
        value = clean_text(rating.get("ratingValue"))
        if value:
            return value
    node = soup.find(attrs={"itemprop": "ratingValue"})
    if node:
        return clean_text(node.get("content") or node)
    value = _meta(soup, "product:rating:value")
    if value:
        return value
    node = soup.select_one(".js-DM_gt-rate")
    if node:
        match = re.search(r"\b([0-5](?:\.\d+)?)\b", clean_text(node))
        if match:
            return match.group(1)
    return ""


def parse_status(soup: BeautifulSoup, product: dict[str, Any]) -> str:
    for offer in _offers(product):
        availability = clean_text(offer.get("availability")).lower()
        if any(word in availability for word in ("outofstock", "soldout", "discontinued")):
            return "out_of_stock"
        if any(word in availability for word in ("instock", "limitedavailability", "preorder")):
            return "active"

    availability_nodes = soup.select("[itemprop='availability'], .DM_gsw-SEO_stock")
    text = " ".join(clean_text(node.get("content") or node) for node in availability_nodes)
    if re.search(r"\b(?:out of stock|sold out|unavailable)\b", text, re.I):
        return "out_of_stock"
    if re.search(r"\b(?:in stock|available)\b", text, re.I):
        return "active"

    for script in soup.find_all("script"):
        raw = script.string or script.get_text()
        marker = raw.find("window.PRODUCT_DATA")
        if marker < 0:
            continue
        match = re.search(r'["\']stock["\']\s*:\s*(-?\d+)', raw[marker:])
        if match:
            return "active" if int(match.group(1)) > 0 else "out_of_stock"
    return "active"


def parse_url(soup: BeautifulSoup, page_url: str, product: dict[str, Any]) -> str:
    canonical = soup.find("link", rel=lambda value: value and "canonical" in value)
    value = str(canonical.get("href", "")) if canonical else ""
    if not value:
        for offer in _offers(product):
            value = clean_text(offer.get("url"))
            if value:
                break
    absolute = urljoin(page_url, value or page_url)
    parts = urlsplit(absolute)
    return urlunsplit((parts.scheme or "https", parts.netloc, parts.path.rstrip("/"), "", ""))


def parse_product(html: str, page_url: str) -> dict[str, str]:
    soup = BeautifulSoup(html, "html.parser")
    objects = _json_ld(soup)
    product = _first_product(objects)
    result = {
        "Name": parse_name(soup, product),
        "SKU": parse_sku(soup, product),
        "Price": parse_price(soup, product),
        "Category": parse_category(soup, objects),
        "Description": parse_description(soup, product),
        "Specifications": parse_specifications(soup, product),
        "Rating": parse_rating(soup, product),
        "URL": parse_url(soup, page_url, product),
        "Status": parse_status(soup, product),
    }
    return {field: clean_text(result[field]) for field in FIELDS}
