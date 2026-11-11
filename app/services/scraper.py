import asyncio
import json
import logging
import re
import time
from urllib.parse import quote_plus
import random
from bs4 import BeautifulSoup, Tag
from playwright.async_api import BrowserContext, async_playwright, TimeoutError as PlaywrightTimeout
from playwright_stealth import Stealth
from app.schemas import PlatformResult, SearchCompareResponse

logger = logging.getLogger(__name__)

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
]
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-blink-features=AutomationControlled",
    "--disable-infobars",
    "--disable-background-networking",
    "--disable-background-timer-throttling",
    "--disable-backgrounding-occluded-windows",
    "--disable-breakpad",
    "--disable-component-extensions-with-background-pages",
    "--disable-extensions",
    "--disable-features=Translate,BackForwardCache,AcceptCHFrame,MediaRouter,OptimizationHints",
    "--disable-ipc-flooding-protection",
    "--disable-renderer-backgrounding",
    "--enable-features=NetworkService,NetworkServiceInProcess",
    "--force-color-profile=srgb",
    "--metrics-recording-only",
    "--mute-audio",
    "--no-default-browser-check",
    "--no-first-run",
    "--window-size=1920,1080",
]

# ---------------------------------------------------------------------------
# Amazon selectors
# ---------------------------------------------------------------------------
AMAZON_BASE = "https://www.amazon.in"
AMAZON_CARD = "div[data-component-type='s-search-result']"
AMAZON_TITLE = ["h2 span", "h2", "span.a-text-normal"]
AMAZON_PRICE = ["span.a-price span.a-offscreen", "span.a-price", "span.a-color-price"]
AMAZON_LINK = ["h2 a", "a.a-link-normal[href*='/dp/']"]
AMAZON_IMAGE = ["img.s-image", "img"]

# ---------------------------------------------------------------------------
# Flipkart selectors (with class-agnostic fallbacks)
# ---------------------------------------------------------------------------
FLIPKART_BASE = "https://www.flipkart.com"
FLIPKART_CARD = "div[data-id]"
FLIPKART_TITLE = ["div.KzDlHZ", "a.wjcEIp", "a.IRpwTa", "a.WKTcLC", "div._4rR01T"]
FLIPKART_PRICE = ["div.Nx9bqj", "div._30jeq3"]
FLIPKART_LINK = ["a[href*='/p/']"]
FLIPKART_IMAGE = ["img"]

MAX_CARDS_TO_SCORE = 3
MIN_MATCH_SCORE = 0.5

# Words indicating accessories/parts unless the user explicitly searched for them
ACCESSORY_TERMS = (
    "compatible with",
    "for iphone",
    "for samsung",
    "back cover",
    "phone case",
    "flip cover",
    "screen guard",
    "screen protector",
    "tempered glass",
    "oled screen",
    "touch digitizer",
    "replacement screen",
    "display combo",
    "camera lens protector",
    "charging cable",
    "adapter",
    "skin wrap",
)

BLOCK_MARKERS = (
    "captcha",
    "enter the characters you see below",
    "automated access",
    "are you a human",
    "unusual traffic",
)

CACHE_TTL_SECONDS = 600
_cache: dict[str, tuple[float, SearchCompareResponse]] = {}

_PRICE_PATTERN = re.compile(r"\d[\d.,]*\d|\d")
_RUPEE_PATTERN = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)")


def _clean_price(raw_text: str | None) -> float | None:
    if not raw_text:
        return None

    match = _PRICE_PATTERN.search(raw_text)
    if not match:
        return None

    number = match.group()
    last_dot = number.rfind(".")
    last_comma = number.rfind(",")

    if last_dot != -1 and last_comma != -1:
        if last_comma > last_dot:
            number = number.replace(".", "").replace(",", ".")
        else:
            number = number.replace(",", "")
    elif last_comma != -1:
        digits_after = len(number) - last_comma - 1
        if digits_after == 3:
            number = number.replace(",", "")
        else:
            number = number.replace(",", ".")
    elif number.count(".") > 1:
        number = number.replace(".", "")

    try:
        value = float(number)
    except ValueError:
        return None

    return value if value > 0 else None


def _first(card: Tag, selectors: list[str]) -> Tag | None:
    for selector in selectors:
        element = card.select_one(selector)
        if element:
            return element
    return None


def _is_blocked(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in BLOCK_MARKERS)


def _is_unwanted_accessory(query: str, title: str) -> bool:
    """Reject covers, tempered glass, and spare screens unless user searched for them."""
    q_lower = query.lower()
    t_lower = title.lower()
    for term in ACCESSORY_TERMS:
        if term in t_lower and term not in q_lower:
            return True
    # Also catch generic "cover" / "case" / "glass" when user didn't ask for one
    for word in ("cover", "case", "tempered", "digitizer", "protector"):
        if re.search(rf"\b{word}\b", t_lower) and word not in q_lower:
            return True
    return False


def _match_score(query: str, title: str) -> float:
    query_words = set(re.findall(r"\w+", query.lower()))
    if not query_words:
        return 0.0
    title_words = set(re.findall(r"\w+", title.lower()))
    overlap = len(query_words & title_words) / len(query_words)

    # Bonus if the title actually starts with the brand/product rather than "For ..."
    if title.lower().startswith(("compatible", "for ", "pack of")):
        overlap -= 0.4
    return overlap


def _absolute_url(base: str, href: str) -> str | None:
    if not href:
        return None
    if href.startswith("/"):
        return f"{base}{href.split('?')[0]}"
    return href.split("?")[0]


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


def _pick_best(query: str, candidates: list[PlatformResult]) -> PlatformResult | None:
    best, best_score = None, 0.0
    for r in candidates:
        score = _match_score(query, r.title or "")
        if score > best_score:
            best, best_score = r, score
    return best if best_score >= MIN_MATCH_SCORE else None


async def _new_stealth_context(browser) -> BrowserContext:
    # Select a single string from the list
    ua = random.choice(USER_AGENTS) if isinstance(USER_AGENTS, (list, tuple)) else str(USER_AGENTS)

    context = await browser.new_context(
        user_agent=ua,  # Must be a single string!
        viewport={"width": 1366, "height": 768},
        locale="en-IN",
        timezone_id="Asia/Kolkata",
        extra_http_headers={
            "Accept-Language": "en-IN,en-GB;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        }
    )
    
    await context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
    )
    
    # Keep CSS allowed so Flipkart/Amazon hydration & layouts work reliably
    await context.route(
        "**/*.{png,jpg,jpeg,gif,webp,svg,woff,woff2,mp4}",
        lambda route: route.abort(),
    )
    return context

# ---------------------------------------------------------------------------
# Amazon
# ---------------------------------------------------------------------------
def _amazon_is_sponsored(card: Tag) -> bool:
    if card.select_one("span.puis-label-popover-default"):
        return True
    return any(s.get_text(strip=True).lower() == "sponsored" for s in card.select("span"))


def _is_unavailable(card: Tag) -> bool:
    text = card.get_text(" ", strip=True).lower()
    return "currently unavailable" in text or "out of stock" in text or "sold out" in text


async def scrape_amazon_search(context: BrowserContext, query: str) -> PlatformResult | None:
    page = await context.new_page()
    try:
        await Stealth().apply_stealth_async(page)
        url = f"{AMAZON_BASE}/s?k={quote_plus(query)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector(AMAZON_CARD, state="attached", timeout=8000)
        except PlaywrightTimeout:
            pass
        await page.wait_for_timeout(random.randint(500, 1200))

        html = await page.content()
        if _is_blocked(html):
            logger.warning("Amazon blocked the search for query=%r", query)
            return None

        soup = BeautifulSoup(html, "html.parser")
        candidates: list[PlatformResult] = []
        for card in soup.select(AMAZON_CARD):
            if len(candidates) >= MAX_CARDS_TO_SCORE:
                break
            if _amazon_is_sponsored(card) or _is_unavailable(card):
                continue

            title_el = _first(card, AMAZON_TITLE)
            title = title_el.get_text(" ", strip=True) if title_el else None
            if not title or _is_unwanted_accessory(query, title):
                continue

            # a-text-price is the struck-through MRP, so skip it
            price_el = card.select_one("span.a-price:not(.a-text-price) span.a-offscreen") or _first(card, AMAZON_PRICE)
            price = _clean_price(price_el.get_text() if price_el else None)
            if price is None:
                continue

            asin = card.get("data-asin")
            link_el = _first(card, AMAZON_LINK)
            product_url = (
                f"{AMAZON_BASE}/dp/{asin}" if asin
                else _absolute_url(AMAZON_BASE, link_el.get("href", "")) if link_el else None
            )
            img_el = _first(card, AMAZON_IMAGE)
            candidates.append(
                PlatformResult(
                    platform="amazon",
                    title=title,
                    price=price,
                    image_url=img_el.get("src") if img_el else None,
                    product_url=product_url,
                    in_stock=True,
                )
            )
        return _pick_best(query, candidates)
    except Exception:
        logger.exception("Amazon search failed for query=%r", query)
        return None
    finally:
        await page.close()

# ---------------------------------------------------------------------------
# Flipkart
# ---------------------------------------------------------------------------
def _extract_flipkart_title(card: Tag, link_el: Tag) -> str | None:
    """Extract product title even when Flipkart rotates its CSS class names."""
    title_el = _first(card, FLIPKART_TITLE)
    if title_el and title_el.get_text(strip=True):
        return title_el.get_text(strip=True)

    if link_el.get("title"):
        return link_el.get("title").strip()

    img_el = card.select_one("img[alt]")
    if img_el and img_el.get("alt") and len(img_el.get("alt").strip()) > 5:
        return img_el.get("alt").strip()

    # Fallback: longest anchor text inside the card
    for a in card.select("a[href*='/p/']"):
        txt = a.get_text(" ", strip=True)
        if len(txt) > 8 and not txt.startswith("₹"):
            return txt
    return None


_AD_LABELS = {"ad", "sponsored"}
_PURE_PRICE = re.compile(r"₹\s*[\d,]+(?:\.\d+)?")


def _flipkart_is_ad(card: Tag) -> bool:
    """Sponsored results carry a small 'Ad' badge (a leaf element whose whole text is 'Ad')."""
    for el in card.find_all(["div", "span"]):
        if el.find(True) is None and el.get_text(strip=True).lower() in _AD_LABELS:
            return True
    return False


def _flipkart_find_cards(soup: BeautifulSoup) -> list[Tag]:
    """Outermost data-id cards; falls back to climbing from product links if classes/attrs change."""
    cards = [c for c in soup.select(FLIPKART_CARD) if c.find_parent(attrs={"data-id": True}) is None]
    if cards:
        return cards

    found: list[Tag] = []
    seen: set[int] = set()
    for link in soup.select(FLIPKART_LINK[0]):
        node = link
        for _ in range(6):
            if node.parent is None:
                break
            node = node.parent
            if "₹" in node.get_text(" ", strip=True):
                break
        if "₹" in node.get_text(" ", strip=True) and id(node) not in seen:
            seen.add(id(node))
            found.append(node)
    return found


def _flipkart_card_price(card: Tag) -> float | None:
    """Selling price only: not the MRP, EMI ('₹x/month'), exchange or bank-offer amounts."""
    for selector in FLIPKART_PRICE:
        el = card.select_one(selector)
        if el and _PURE_PRICE.fullmatch(el.get_text(strip=True)):
            return _clean_price(el.get_text())

    # Class-agnostic fallback: first text node that is *only* a price and isn't struck through.
    # The selling price comes before the MRP in the DOM.
    for node in card.find_all(string=_PURE_PRICE):
        if not _PURE_PRICE.fullmatch(node.strip()):
            continue
        parent = node.parent
        if parent.name in ("del", "s", "strike") or parent.find_parent(["del", "s", "strike"]):
            continue
        return _clean_price(node)
    return None


def _flipkart_card_image(card: Tag) -> str | None:
    for img in card.select("img"):
        src = img.get("src") or ""
        if src.startswith("http"):
            return src
    return None


_UNAVAILABLE_LABELS = {"sold out", "currently unavailable", "out of stock", "notify me"}


def _flipkart_page_unavailable(soup: BeautifulSoup) -> bool:
    """Only trust a stand-alone 'Sold Out' style label, not the words appearing in
    recommended/similar products elsewhere on the page."""
    for el in soup.find_all(["div", "span", "button"]):
        if el.find(True) is None and el.get_text(strip=True).lower() in _UNAVAILABLE_LABELS:
            return True
    return False


def _flipkart_detail_price(soup: BeautifulSoup) -> float | None:
    """Selling price of the main product on a Flipkart product page."""
    # 1. Structured data: stable even when CSS classes rotate.
    for script in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(script.string or "")
        except (ValueError, TypeError):
            continue
        for item in data if isinstance(data, list) else [data]:
            if not isinstance(item, dict) or item.get("@type") != "Product":
                continue
            offers = item.get("offers")
            if isinstance(offers, list):
                offers = offers[0] if offers else None
            if isinstance(offers, dict):
                price = _clean_price(str(offers.get("price", "")))
                if price:
                    return price

    # 2. Known price classes (main product comes first in the DOM).
    for selector in ("div.Nx9bqj.CxhGGd", *FLIPKART_PRICE):
        el = soup.select_one(selector)
        if el and _PURE_PRICE.fullmatch(el.get_text(strip=True)):
            return _clean_price(el.get_text())

    # 3. Class-agnostic: first stand-alone price that isn't struck through (the MRP).
    #    No page-wide "first rupee sign" fallback: that picks up EMI/offer/ad amounts.
    for node in soup.find_all(string=_PURE_PRICE):
        if not _PURE_PRICE.fullmatch(node.strip()):
            continue
        parent = node.parent
        if parent.name in ("del", "s", "strike", "script", "style") or parent.find_parent(["del", "s", "strike"]):
            continue
        return _clean_price(node)
    return None


async def scrape_flipkart_search(context: BrowserContext, query: str) -> PlatformResult | None:
    page = await context.new_page()
    try:
        url = f"{FLIPKART_BASE}/search?q={quote_plus(query)}"
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        try:
            await page.wait_for_selector("a[href*='/p/']", state="attached", timeout=8000)
        except PlaywrightTimeout:
            pass
        await page.evaluate("window.scrollBy(0, 600)")
        await page.wait_for_timeout(800)

        html = await page.content()
        if _is_blocked(html):
            logger.warning("Flipkart blocked the search for query=%r", query)
            return None

        soup = BeautifulSoup(html, "html.parser")
        candidates: list[PlatformResult] = []
        skipped_ads = 0

        for card in _flipkart_find_cards(soup):
            if len(candidates) >= MAX_CARDS_TO_SCORE:
                break
            if _flipkart_is_ad(card):
                skipped_ads += 1
                continue
            if _is_unavailable(card):
                continue

            link_el = card.select_one(FLIPKART_LINK[0])
            if not link_el:
                continue
            title = _extract_flipkart_title(card, link_el)
            if not title or _is_unwanted_accessory(query, title):
                continue

            price = _flipkart_card_price(card)
            if price is None:
                continue

            candidates.append(
                PlatformResult(
                    platform="flipkart",
                    title=title,
                    price=price,
                    image_url=_flipkart_card_image(card),
                    product_url=_absolute_url(FLIPKART_BASE, link_el.get("href", "")),
                    in_stock=True,
                )
            )

        logger.info("flipkart: %d organic candidates, %d ads skipped for query=%r",
                    len(candidates), skipped_ads, query)
        return _pick_best(query, candidates)
    except Exception:
        logger.exception("Flipkart search failed for query=%r", query)
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

    if amazon_res or flipkart_res:
        _cache_set(cache_key, response)

    return response


async def scrape_direct_product_price(url: str, platform: str) -> tuple[float | None, bool]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=BROWSER_ARGS)
        try:
            context = await _new_stealth_context(browser)
            try:
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded", timeout=20000)

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
                    if _flipkart_page_unavailable(soup):
                        return None, False
                    price = _flipkart_detail_price(soup)
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