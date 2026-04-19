from __future__ import annotations

import random
import re
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from loguru import logger

from src.models import Listing, WatchlistEntry

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext


class SessionExpiredError(Exception):
    """Raised when FB redirects to the login page during a scrape."""


@dataclass
class BrowserSession:
    """Holds the full Playwright resource chain so all three can be torn down."""

    context: Any
    browser: Any
    playwright: Any

    def close(self) -> None:
        for resource, method in (
            (self.context, "close"),
            (self.browser, "close"),
            (self.playwright, "stop"),
        ):
            try:
                getattr(resource, method)()
            except Exception as exc:
                logger.warning(f"Error closing {method}: {exc}")


def build_browser_session(auth_state_path: Path) -> BrowserSession:
    import os  # noqa: PLC0415
    from playwright.sync_api import sync_playwright  # noqa: PLC0415

    if not auth_state_path.exists():
        raise FileNotFoundError(f"auth_state.json not found at {auth_state_path}")
    playwright = sync_playwright().start()
    headless = os.getenv("HEADLESS", "1") == "1"
    browser = playwright.chromium.launch(headless=headless)
    context = browser.new_context(
        storage_state=str(auth_state_path),
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 900},
    )
    return BrowserSession(context=context, browser=browser, playwright=playwright)


def scrape_keyword(
    context: BrowserContext,
    entry: WatchlistEntry,
    location: str,
    delay_range: tuple[int, int] = (10, 30),
) -> list[Listing]:
    delay = random.uniform(*delay_range)
    logger.info(f"Waiting {delay:.1f}s before scraping '{entry.keyword}'")
    time.sleep(delay)

    # Drop state/country suffix ("San Francisco, CA" → "San Francisco") then slugify
    city_part = location.split(",", 1)[0]
    city_slug = re.sub(r"[^a-z0-9]", "", city_part.lower())
    encoded_keyword = quote_plus(entry.keyword)
    max_price_param = (
        "" if entry.max_price == float("inf") else f"&maxPrice={int(entry.max_price)}"
    )
    url = (
        f"https://www.facebook.com/marketplace/{city_slug}/search"
        f"?query={encoded_keyword}{max_price_param}"
        f"&sortBy=creation_time_descend"
    )

    logger.info(f"Scraping: {url}")
    page = context.new_page()
    try:
        page.goto(url, timeout=30_000, wait_until="domcontentloaded")

        if "login" in page.url:
            raise SessionExpiredError("FB session expired — login page detected")

        try:
            page.wait_for_selector(
                'a[href*="/marketplace/item/"]', timeout=15_000
            )
        except Exception:
            logger.warning(
                f"No listing cards appeared for '{entry.keyword}' within 15s"
            )

        # Give FB's lazy-loaded card contents (price, location) time to render.
        time.sleep(5)

        html = page.content()
    finally:
        page.close()

    return _parse_search_results(html, entry)


_PRICE_PATTERN = re.compile(r"(?:US|CA|AU|NZ)?\$[\s\xa0]*[\d,]+", re.IGNORECASE)
# "NCMorrisville", "CARaleigh" — state abbr glued to city, a telltale sign
# that FB dumped its location string as title.
_STATE_CITY_GLUE = re.compile(r"^[A-Z]{2}[A-Z][a-z]")
_CITY_STATE = re.compile(r"^[A-Z][a-zA-Z .'-]+,\s*[A-Z]{2}$")
_CARD_CHROME_PREFIXES = (
    "seller details",
    "message seller",
    "shipping available",
    "local pickup",
    "pickup only",
    "buy now",
    "save",
    "share",
    "hide",
    "learn more",
    "see more",
    "see less",
    "report",
)
_DETAIL_CHROME_PREFIXES = (
    "seller details",
    "message seller",
    "buy now",
    "learn more",
    "see more",
    "see less",
    "purchase protection",
    "buyer protection",
    "shipping available",
    "pickup only",
    "local pickup",
    "about this seller",
)


def _parse_search_results(html: str, entry: WatchlistEntry) -> list[Listing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[Listing] = []
    seen_ids: set[str] = set()

    for a_tag in soup.find_all("a", href=re.compile(r"/marketplace/item/(\d+)")):
        href = a_tag.get("href", "")
        match = re.search(r"/marketplace/item/(\d+)", href)
        if not match:
            continue
        listing_id = match.group(1)
        if listing_id in seen_ids:
            continue
        seen_ids.add(listing_id)

        listing_url = f"https://www.facebook.com/marketplace/item/{listing_id}/"

        # FB's card DOM keeps price/title as siblings of the anchor, not
        # children. Walk up until we find a container that has a "$" token,
        # then collect text from there.
        card = a_tag
        texts: list[str] = []
        for _ in range(6):
            if card.parent is None:
                break
            card = card.parent
            candidate_texts = [t.strip() for t in card.stripped_strings if t.strip()]
            if any(
                _PRICE_PATTERN.search(t) or t.lower() == "free"
                for t in candidate_texts
            ):
                texts = candidate_texts
                break

        if not texts:
            texts = [t.strip() for t in a_tag.stripped_strings if t.strip()]

        if not texts:
            logger.debug(f"No card text found near {listing_url}")
            continue

        price_raw = ""
        for t in texts:
            if t.lower() == "free":
                price_raw = t
                break
            if _PRICE_PATTERN.search(t):
                price_raw = t
                break
        price = _parse_price(price_raw)

        if price > entry.max_price:
            continue

        # Preferred path: FB search cards carry aria-label="$15 · Title · City, ST".
        # Trust it only when all three "·"-separated segments are present AND
        # the title segment is non-empty. Partial labels ("$15 · City, ST")
        # mean the card hasn't hydrated — skip and let the next poll retry
        # rather than guessing title from junk text.
        raw_label = a_tag.get("aria-label", "").strip()
        if raw_label:
            title, location = _parse_aria_label(raw_label)
            if title is None:
                logger.debug(
                    f"Skipping listing {listing_id}: partial/invalid aria-label "
                    f"'{raw_label[:80]}'"
                )
                continue
        else:
            # Heuristic fallback only when the card has no aria-label at all.
            # Partial labels usually mean FB hasn't hydrated the title yet, and
            # guessing from leftover text is exactly how locations become titles.
            title, location = _heuristic_title_location(texts, price_raw)

        if not title:
            logger.debug(
                f"Skipping listing {listing_id}: could not extract title "
                f"(aria-label='{a_tag.get('aria-label', '')[:80]}', texts={texts[:4]})"
            )
            continue

        logger.debug(
            f"Parsed listing {listing_id}: price=${price} title='{title[:60]}' location='{location}'"
        )
        listings.append(
            Listing(
                listing_id=listing_id,
                title=title,
                price=price,
                description="",
                condition="",
                location=location,
                listing_url=listing_url,
            )
        )

    logger.info(f"Found {len(listings)} listings for '{entry.keyword}'")
    if not listings:
        debug_path = Path("/tmp/fb_search_debug.html")
        debug_path.write_text(html[:500_000])
        logger.warning(
            f"No listings parsed for '{entry.keyword}'. "
            f"Saved page HTML snippet to {debug_path}"
        )
    return listings


def fetch_listing_detail(
    context: BrowserContext,
    listing: Listing,
) -> Listing:
    try:
        page = context.new_page()
        try:
            page.goto(listing.listing_url, timeout=20_000, wait_until="domcontentloaded")
            try:
                page.wait_for_selector('[dir="auto"]', timeout=10_000)
            except Exception:
                pass
            # Scroll to force FB's Relay to hydrate description block.
            try:
                page.evaluate("window.scrollBy(0, 600)")
            except Exception:
                pass
            time.sleep(5)
            final_url = page.url
            html = page.content()
            logger.debug(
                f"Detail page landed on: {final_url} (requested: {listing.listing_url})"
            )
            # Dump first detail page HTML to /tmp so we can inspect the real DOM.
            dump_path = Path("/tmp/fb_detail_debug.html")
            if not dump_path.exists():
                dump_path.write_text(html)
                logger.debug(
                    f"Saved detail HTML ({len(html)} bytes) to {dump_path}"
                )
        finally:
            page.close()

        soup = BeautifulSoup(html, "html.parser")

        description = _extract_description(soup)

        condition_pattern = re.compile(
            r"\b(new|used\s*[-–]\s*\w+|refurbished|for\s+parts)\b", re.IGNORECASE
        )
        page_text = soup.get_text(" ", strip=True)
        cond_match = condition_pattern.search(page_text)
        condition = cond_match.group(0) if cond_match else ""

        logger.debug(
            f"Detail for {listing.listing_id}: "
            f"description='{description[:120]}' condition='{condition}'"
        )
        return replace(listing, description=description, condition=condition)

    except Exception as exc:
        logger.warning(f"Could not fetch detail for {listing.listing_url}: {exc}")
        return listing


def _parse_aria_label(label: str) -> tuple[str | None, str]:
    """
    Parse FB search card aria-label.

    FB has used at least two formats in search results:
    - "$15 · Title · Morrisville, NC"
    - "Title, US$ 429, NCDurham, listing 966721032480305"

    Returns (title, location). Returns (None, "") when the label is missing
    or partial — caller should treat that as "card not yet hydrated" and
    fall through (or skip the listing).
    """
    if not label:
        return None, ""

    if "\u00b7" in label:
        return _parse_dot_separated_aria_label(label)

    return _parse_comma_separated_aria_label(label)


def _parse_dot_separated_aria_label(label: str) -> tuple[str | None, str]:
    parts = [p.strip() for p in label.split("\u00b7")]
    if len(parts) < 3:
        return None, ""

    first = parts[0]
    if not (_PRICE_PATTERN.search(first) or first.lower() == "free"):
        return None, ""

    title = parts[1]
    if not title or len(title) < 2:
        return None, ""

    location = parts[-1]
    return title, location


def _parse_comma_separated_aria_label(label: str) -> tuple[str | None, str]:
    parts = [p.strip() for p in label.split(",")]
    if len(parts) < 3:
        return None, ""

    price_index = -1
    for idx, part in enumerate(parts):
        if _PRICE_PATTERN.search(part) or part.lower() == "free":
            price_index = idx
            break

    if price_index <= 0:
        return None, ""

    title_parts = [part for part in parts[:price_index] if part]
    if not title_parts:
        return None, ""
    title = ", ".join(title_parts)
    if len(title) < 2:
        return None, ""

    location_parts: list[str] = []
    for part in parts[price_index + 1 :]:
        normalized = part.strip()
        if not normalized:
            continue
        if normalized.lower().startswith("listing "):
            break
        location_parts.append(normalized)

    location = ", ".join(location_parts[:2])
    return title, location


def _heuristic_title_location(
    texts: list[str], price_raw: str
) -> tuple[str | None, str]:
    """
    Fallback parsing when aria-label is absent. Strict gates so we return
    None (skip listing) rather than misclassify location as title.
    """
    if len(texts) < 3:
        return None, ""

    location = ""
    for t in reversed(texts):
        if t == price_raw or _PRICE_PATTERN.search(t):
            continue
        if _looks_like_location(t):
            location = t
            break

    title = ""
    for cand in texts:
        if cand == price_raw or _PRICE_PATTERN.search(cand):
            continue
        if cand == location or len(cand) < 8:
            continue
        if _looks_like_location(cand) or _is_card_chrome_text(cand):
            continue
        title = cand
        break

    if not title:
        return None, ""

    return title, location


def _looks_like_location(text: str) -> bool:
    return (
        bool(_STATE_CITY_GLUE.match(text))
        or bool(_CITY_STATE.match(text))
        or "miles away" in text.lower()
    )


def _is_card_chrome_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in _CARD_CHROME_PREFIXES
    )


def _extract_description(soup: BeautifulSoup) -> str:
    """
    Extract the seller description from a detail page.

    Strategy: anchor on the price element (always present in the main
    content column), walk up to its nearest large ancestor, then inspect
    only the early plausible dir=auto blocks inside that subtree. This
    naturally excludes sidebar ads/related-item promos in sibling subtrees
    and avoids letting later protection/legal copy win by length alone.

    Returns "" when no plausible description exists — callers should treat
    empty descriptions as real ("(none provided)" in the LLM prompt),
    never fall back to "longest dir=auto anywhere in the DOM".
    """
    price_elements: list[Tag] = []
    for el in soup.find_all(["span", "div"]):
        text = el.get_text(" ", strip=True)
        if not text or len(text) > 40:
            continue
        if _PRICE_PATTERN.search(text) or text.lower() == "free":
            price_elements.append(el)

    if not price_elements:
        return ""
    best_description = ""
    best_score = (-1, -1)
    seen_subtrees: set[int] = set()

    for price_el in price_elements:
        subtree = _find_description_subtree(price_el)
        if subtree is None:
            continue
        subtree_id = id(subtree)
        if subtree_id in seen_subtrees:
            continue
        seen_subtrees.add(subtree_id)

        exclusions = _build_description_exclusions(soup, subtree)
        candidates = _collect_description_candidates(subtree, exclusions)
        if not candidates:
            continue

        description = _select_description_candidate(candidates)
        score = (_score_description_subtree(subtree), len(description))
        if score > best_score:
            best_score = score
            best_description = description

    return best_description


def _find_description_subtree(price_el: Tag) -> Tag | None:
    # Walk up 4–7 ancestors looking for a container that's "large enough"
    # to be the main content subtree (holds price, title, description).
    ancestor = price_el
    for depth in range(1, 8):
        parent = ancestor.parent
        if not isinstance(parent, Tag):
            break
        ancestor = parent
        if depth < 4:
            continue
        text_blob = ancestor.get_text(" ", strip=True).lower()
        descendant_count = len(list(ancestor.find_all(True, limit=40)))
        has_markers = any(
            kw in text_blob
            for kw in ("listed", "posted", "condition", "details", "seller")
        )
        if descendant_count >= 20 or has_markers or ancestor.find("h1"):
            return ancestor
    return None


def _build_description_exclusions(soup: BeautifulSoup, subtree: Tag) -> set[str]:
    # Exclusions: og:title text and the first <h1>'s text (both are the
    # listing title, which can also appear in a dir=auto span).
    exclusions: set[str] = set()
    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        exclusions.add(og_title["content"].strip())
    h1 = subtree.find("h1") or soup.find("h1")
    if h1:
        exclusions.add(h1.get_text(" ", strip=True))
    return exclusions


def _collect_description_candidates(
    subtree: Tag, exclusions: set[str]
) -> list[tuple[int, str]]:
    candidates: list[tuple[int, str]] = []
    seen_texts: set[str] = set()
    for idx, el in enumerate(subtree.find_all(["span", "div"], attrs={"dir": "auto"})):
        if el.find(["span", "div"], attrs={"dir": "auto"}):
            continue
        text = el.get_text(" ", strip=True)
        if len(text) < 20:
            continue
        if text in exclusions or text in seen_texts:
            continue
        if _PRICE_PATTERN.search(text) and len(text) < 40:
            continue  # price chrome, not description
        if _is_detail_chrome_text(text):
            continue
        seen_texts.add(text)
        candidates.append((idx, text))
    return candidates


def _select_description_candidate(candidates: list[tuple[int, str]]) -> str:
    # Real seller descriptions usually appear early in the price/title subtree.
    # Keep the search local to the first few plausible blocks so later protection
    # copy or promo/legal text in the same column does not win by length alone.
    early_window = candidates[:5]
    return max(early_window, key=lambda item: (len(item[1]), -item[0]))[1]


def _is_detail_chrome_text(text: str) -> bool:
    normalized = re.sub(r"\s+", " ", text.strip().lower())
    return any(
        normalized == prefix or normalized.startswith(f"{prefix} ")
        for prefix in _DETAIL_CHROME_PREFIXES
    )


def _score_description_subtree(subtree: Tag) -> int:
    text_blob = subtree.get_text(" ", strip=True).lower()
    score = 0
    if subtree.find("h1"):
        score += 5
    if any(
        kw in text_blob for kw in ("listed", "posted", "condition", "details", "seller")
    ):
        score += 2
    score += min(len(list(subtree.find_all(True, limit=80))), 80) // 20
    price_count = sum(
        1
        for el in subtree.find_all(["span", "div"])
        if (text := el.get_text(" ", strip=True))
        and len(text) <= 40
        and (_PRICE_PATTERN.search(text) or text.lower() == "free")
    )
    if price_count > 1:
        score -= 4 * (price_count - 1)
    return score


def _parse_price(raw: str) -> float:
    if not raw or raw.lower() == "free":
        return 0.0
    # Grab the first numeric run — works for "$350", "US$ 350", "CA$1,200.50".
    match = re.search(r"([\d,]+(?:\.\d+)?)", raw)
    if not match:
        return 0.0
    cleaned = match.group(1).replace(",", "")
    try:
        return float(cleaned)
    except ValueError:
        return 0.0
