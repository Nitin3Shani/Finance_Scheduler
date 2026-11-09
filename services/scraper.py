import asyncio
import logging
import re
import time
from urllib.parse import quote_plus

from bs4 import BeautifulSoup, Tag
from playwright.async_api import BrowserContext, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeout

from app.schemas import PlatformResult, SearchCompareResponse

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)

BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]

# ---------------------------------------------------------------------------
# Amazon selectors: several per field, tried in order, so one HTML change
# doesn't break everything. Edit these lists when Amazon changes its markup.
# ---------------------------------------------------------------------------
AMAZON_BASE = "https://www.amazon.in"
AMAZON_CARD = "div[data-component-type='s-search-result']"
AMAZON_TITLE = ["h2 span", "h2", "span.a-text-normal"]
AMAZON_PRICE = ["span.a-price span.a-offscreen", "span.a-price", "span.a-color-price"]
AMAZON_LINK = ["h2 a", "a.a-link-normal[href*='/dp/']"]
AMAZON_IMAGE = ["img.s-image", "img"]

# ---------------------------------------------------------------------------
# Flipkart selectors (class names are obfuscated and change often)
# ---------------------------------------------------------------------------
FLIPKART_BASE = "https://www.flipkart.com"
FLIPKART_CARD = "div[data-id]"
FLIPKART_TITLE = ["div.KzDlHZ", "a.wjcEIp", "a.IRpwTa", "a.WKTcLC"]
FLIPKART_PRICE = ["div.Nx9bqj", "div._30jeq3"]
FLIPKART_LINK = ["a[href*='/p/']"]
FLIPKART_IMAGE = ["img"]

# Matching and limits
MAX_CARDS_TO_SCORE = 10   # only compare the top results
MIN_MATCH_SCORE = 0.5     # at least half the query words must appear in the title

# Text that shows up on CAPTCHA / bot-check pages
BLOCK_MARKERS = (
    "captcha",
    "enter the characters you see below",
    "automated access",
    "are you a human",
    "unusual traffic",
)

# Simple in-memory cache so the same query isn't scraped repeatedly
CACHE_TTL_SECONDS = 600
_cache: dict[str, tuple[float, SearchCompareResponse]] = {}

_PRICE_PATTERN = re.compile(r"\d[\d.,]*\d|\d")
_RUPEE_PATTERN = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")



# Helpers

def _clean_price(raw_text: str | None) -> float | None:

    if not raw_text:
        return None

    # Only the FIRST number-like chunk, so "₹1,299 - ₹1,499" gives 1,299
    match = _PRICE_PATTERN.search(raw_text)
    if not match:
        return None

    number = match.group()
    last_dot = number.rfind(".")
    last_comma = number.rfind(",")

    if last_dot != -1 and last_comma != -1:
        # Both present: whichever comes last is the decimal separator
        if last_comma > last_dot:      # European: 1.299,00
            number = number.replace(".", "").replace(",", ".")
        else:                          # US / India: 1,299.00
            number = number.replace(",", "")
    elif last_comma != -1:
        # Only commas: "1,299" is thousands, "12,50" is decimal
        digits_after = len(number) - last_comma - 1
        if digits_after == 3:
            number = number.replace(",", "")
        else:
            number = number.replace(",", ".")
    elif number.count(".") > 1:
        # Several dots: "1.299.000" uses dots as thousands separators
        number = number.replace(".", "")

    try:
        value = float(number)
    except ValueError:
        return None

    return value if value > 0 else None


def _first(card: Tag, selectors: list[str]) -> Tag | None:
    """Return the first element matched by any selector, tried in order."""
    for selector in selectors:
        element = card.select_one(selector)
        if element:
            return element
    return None


def _is_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in BLOCK_MARKERS)


def _match_score(query: str, title: str) -> float:
    """Fraction of query words that appear in the title (0.0 to 1.0)."""
    query_words = set(re.findall(r"\w+", query.lower()))
    if not query_words:
        return 0.0
    title_words = set(re.findall(r"\w+", title.lower()))
    return len(query_words & title_words) / len(query_words)


def _absolute_url(base: str, href: str) -> str | None:
    """Build a clean absolute URL without tracking query strings."""
    if not href:
        return None
    if href.startswith("/"):
        return f"{base}{href.split('?')[0]}"
    return href


def _cache_get(key: str) -> SearchCompareResponse | None:
    entry = _cache.get(key)
    if not entry:
        return None
    stored_at, response = entry
    if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
        _cache.pop(key, None)
        return None
    return response


def _cache_set(key: str, response: SearchCompareResponse) -> None:
    _cache[key] = (time.monotonic(), response)


async def _new_stealth_context(browser) -> BrowserContext:
    context = await browser.new_context(
        user_agent=USER_AGENT,
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
    )
    # Block heavy assets so scrapes complete in 2-4 seconds.
    # If a price selector suddenly returns nothing, try removing "css" here first.
    await context.route(
        "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,mp4,css}",
        lambda route: route.abort(),
    )
    return context


# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------
def _amazon_is_sponsored(card: Tag) -> bool:
    if card.select_one("span.puis-label-popover-default"):
        return True
    # Fallback that doesn't depend on class names: a span whose text is exactly "Sponsored"
    return any(s.get_text(strip=True).lower() == "sponsored" for s in card.select("span"))


def _is_unavailable(card: Tag) -> bool:
    text = card.get_text(" ", strip=True).lower()
    return "currently unavailable" in text or "out of stock" in text or "sold out" in text


async def scrape_amazon_search(context: BrowserContext, query: str) -> PlatformResult | None:
    page = await context.new_page()
    try:
        url = f"{AMAZON_BASE}/s?k={quote_plus(query)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)

        try:
            await page.wait_for_selector(AMAZON_CARD, timeout=8000)
        except PlaywrightTimeout:
            # No cards: find out whether we were blocked or there were no results
            if _is_blocked(await page.content()):
                logger.warning("amazon blocked the request (captcha) for query=%r", query)
            else:
                logger.info("amazon returned no result cards for query=%r", query)
            return None

        soup = BeautifulSoup(await page.content(), "html.parser")
        cards = soup.select(AMAZON_CARD)

        best_score = 0.0
        best_result: PlatformResult | None = None
        scored = 0

        for card in cards:
            if scored >= MAX_CARDS_TO_SCORE:
                break
            if _amazon_is_sponsored(card):
                continue

            title_el = _first(card, AMAZON_TITLE)
            price_el = _first(card, AMAZON_PRICE)
            price = _clean_price(price_el.get_text() if price_el else None)
            if not title_el or price is None:
                continue

            title = title_el.get_text(strip=True)
            scored += 1

            score = _match_score(query, title)
            # Strictly greater, so on a tie the earlier (higher ranked) card wins
            if score <= best_score:
                continue

            link_el = _first(card, AMAZON_LINK)
            img_el = _first(card, AMAZON_IMAGE)
            href = link_el.get("href", "") if link_el else ""

            best_score = score
            best_result = PlatformResult(
                platform="amazon",
                title=title,
                price=price,
                image_url=img_el.get("src") if img_el else None,
                product_url=_absolute_url(AMAZON_BASE, href),
                in_stock=not _is_unavailable(card),
            )

        if best_result is None or best_score < MIN_MATCH_SCORE:
            logger.info(
                "amazon: no good match for query=%r (best score=%.2f, cards=%d)",
                query, best_score, len(cards),
            )
            return None

        return best_result

    except Exception:
        logger.exception("amazon scrape failed for query=%r", query)
        return None
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Flipkart
# ---------------------------------------------------------------------------
async def scrape_flipkart_search(context: BrowserContext, query: str) -> PlatformResult | None:
    page = await context.new_page()
    try:
        url = f"{FLIPKART_BASE}/search?q={quote_plus(query)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=20000)

        try:
            await page.wait_for_selector(FLIPKART_CARD, timeout=8000)
        except PlaywrightTimeout:
            if _is_blocked(await page.content()):
                logger.warning("flipkart blocked the request for query=%r", query)
            else:
                logger.info("flipkart returned no result cards for query=%r", query)
            return None

        soup = BeautifulSoup(await page.content(), "html.parser")
        cards = soup.select(FLIPKART_CARD)

        best_score = 0.0
        best_result: PlatformResult | None = None
        scored = 0

        for card in cards:
            if scored >= MAX_CARDS_TO_SCORE:
                break

            link_el = _first(card, FLIPKART_LINK)
            if not link_el:
                continue

            # Flipkart uses several card layouts (list view vs grid view)
            title_el = _first(card, FLIPKART_TITLE)
            title = title_el.get_text(strip=True) if title_el else link_el.get("title")

            price_el = _first(card, FLIPKART_PRICE)
            price = _clean_price(price_el.get_text() if price_el else None)
            if price is None:
                # Class-name-free fallback: first rupee amount in the card text
                rupee = _RUPEE_PATTERN.search(card.get_text(" ", strip=True))
                price = _clean_price(rupee.group(1)) if rupee else None

            if not title or price is None:
                continue

            scored += 1
            score = _match_score(query, title)
            if score <= best_score:
                continue

            img_el = _first(card, FLIPKART_IMAGE)
            href = link_el.get("href", "")

            best_score = score
            best_result = PlatformResult(
                platform="flipkart",
                title=title,
                price=price,
                image_url=img_el.get("src") if img_el else None,
                product_url=_absolute_url(FLIPKART_BASE, href),
                in_stock=not _is_unavailable(card),
            )

        if best_result is None or best_score < MIN_MATCH_SCORE:
            logger.info(
                "flipkart: no good match for query=%r (best score=%.2f, cards=%d)",
                query, best_score, len(cards),
            )
            return None

        return best_result

    except Exception:
        logger.exception("flipkart scrape failed for query=%r", query)
        return None
    finally:
        await page.close()


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------
async def compare_platforms(query: str) -> SearchCompareResponse:
    cache_key = query.strip().lower()
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.info("cache hit for query=%r", query)
        return cached

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            context = await _new_stealth_context(browser)
            try:
                amazon_res, flipkart_res = await asyncio.gather(
                    scrape_amazon_search(context, query),
                    scrape_flipkart_search(context, query),
                )
            finally:
                await context.close()
        finally:
            await browser.close()

    cheaper: str | None = None
    if amazon_res and flipkart_res and amazon_res.price and flipkart_res.price:
        if amazon_res.price < flipkart_res.price:
            cheaper = "amazon"
        elif flipkart_res.price < amazon_res.price:
            cheaper = "flipkart"
        else:
            cheaper = "equal"
    elif amazon_res and amazon_res.price:
        cheaper = "amazon"
    elif flipkart_res and flipkart_res.price:
        cheaper = "flipkart"

    response = SearchCompareResponse(
        query=query,
        amazon=amazon_res,
        flipkart=flipkart_res,
        cheaper_platform=cheaper,
    )

    # Cache only when something was found, so failures are retried next time
    if amazon_res or flipkart_res:
        _cache_set(cache_key, response)

    return response


async def scrape_direct_product_price(url: str, platform: str) -> tuple[float | None, bool]:
    """Re-scrapes a tracked product URL during background price checks.

    Returns (price, in_stock). Failures are logged and return (None, False).
    """
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            context = await _new_stealth_context(browser)
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)

                # The price element is often visually hidden, so wait for it to be
                # attached to the page, not visible. Ignore the timeout and parse anyway.
                price_selector = (
                    "span.a-price span.a-offscreen" if platform == "amazon" else "div.Nx9bqj"
                )
                try:
                    await page.wait_for_selector(price_selector, state="attached", timeout=5000)
                except PlaywrightTimeout:
                    pass

                html = await page.content()
                if _is_blocked(html):
                    logger.warning("%s blocked the direct price check for url=%s", platform, url)
                    return None, False

                soup = BeautifulSoup(html, "html.parser")

                if platform == "amazon":
                    page_text = soup.get_text(" ", strip=True).lower()
                    if soup.select_one("#outOfStock") or "currently unavailable" in page_text:
                        return None, False
                    price_el = _first(soup, AMAZON_PRICE)
                    price = _clean_price(price_el.get_text() if price_el else None)
                    if price is None:
                        logger.info("amazon: no price found for url=%s", url)
                    return price, price is not None

                if platform == "flipkart":
                    page_text = soup.get_text(" ", strip=True).lower()
                    if "sold out" in page_text or "currently unavailable" in page_text:
                        return None, False
                    price_el = soup.select_one("div.Nx9bqj.CxhGGd") or _first(soup, FLIPKART_PRICE)
                    price = _clean_price(price_el.get_text() if price_el else None)
                    if price is None:
                        logger.info("flipkart: no price found for url=%s", url)
                    return price, price is not None

                logger.warning("unknown platform %r for url=%s", platform, url)
                return None, False
            finally:
                await context.close()
        except Exception:
            logger.exception("direct price scrape failed for %s url=%s", platform, url)
            return None, False
        finally:
            await browser.close()