import asyncio
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


async def scrape_amazon_search(context: BrowserContext, url: str):
    page = await context.new_page()
    try:
        # Apply stealth patches to mask Playwright environment
        await Stealth().apply_stealth_async(page)
        
        await page.set_extra_http_headers({
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Referer": "https://www.google.com/",
            "Sec-Ch-Ua": '"Chromium";v="122", "Not(A:Brand";v="24", "Google Chrome";v="122"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "cross-site",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1"
        })

        # Navigate using domcontentloaded and wait for DOM stabilization
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        
        # Human-like delay and scrolling to trigger lazy-loaded DOM elements
        await page.wait_for_timeout(random.randint(1000, 2000))
        await page.evaluate("window.scrollBy(0, 400)")
        await page.wait_for_timeout(500)

        content = await page.content()

        # Check for CAPTCHA block
        if "captcha" in content.lower() or "enter the characters you see below" in content.lower():
            logger.warning("Amazon blocked direct price check for url=%s", url)
            return None

        soup = BeautifulSoup(content, "html.parser")
        
        # Select price from various potential DOM containers
        price_el = (
            soup.select_one(".apexPriceToPay span.a-offscreen") or
            soup.select_one("#corePrice_feature_div span.a-offscreen") or
            soup.select_one("#priceblock_ourprice") or
            soup.select_one("#priceblock_dealprice") or
            soup.select_one("span.a-price span.a-offscreen")
        )

        if not price_el:
            return None

        return _clean_price(price_el.get_text())

    except Exception:
        logger.exception("Amazon scraping failed for url=%s", url)
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


async def scrape_flipkart_search(context: BrowserContext, url: str):
    page = await context.new_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        await page.wait_for_timeout(1000)

        soup = BeautifulSoup(await page.content(), "html.parser")

        # 1. Strip out sponsored ads, recommendation carousels, and footer sections
        for tag in soup.select("div[class*='ad'], div[class*='sponsor'], div._3XINgP, div._2kHMtA, div._1eefn7"):
            tag.decompose()

        # 2. Scope extraction directly to the main buying container
        buy_box = (
            soup.select_one("div._25b18c") or          # Primary price container on detail pages
            soup.select_one("div.hl25N") or           # Detail page header wrapper
            soup.select_one("div.C2u-Bx") or           # Price box container
            soup.select_one("div._1Yok0X._2Y-tyZ")     # Main product layout block
        )

        if buy_box:
            # Target the current selling price element inside the buy box
            price_el = (
                buy_box.select_one("div.Nx9bqj") or
                buy_box.select_one("div._30jeq3") or
                buy_box.select_one("div._1vC4OE")
            )
        else:
            price_el = (
                soup.select_one("div._25b18c div.Nx9bqj") or
                soup.select_one("div._25b18c div._30jeq3")
            )

        if not price_el:
            logger.warning("Flipkart price node not found for url=%s", url)
            return None

        cleaned_price = _clean_price(price_el.get_text())

        # Safety check: Ignore prices under ₹30,000 for high-end smartphones like iPhone 17
        if "iphone" in url.lower() and cleaned_price and cleaned_price < 30000:
            logger.warning("Scraped implausible low price (₹%s) for iPhone URL=%s", cleaned_price, url)
            return None

        return cleaned_price

    except Exception:
        logger.exception("Flipkart direct scrape failed for url=%s", url)
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
                    page_text = soup.get_text(" ", strip=True).lower()
                    if "sold out" in page_text or "currently unavailable" in page_text:
                        return None, False
                    price_el = soup.select_one("div.Nx9bqj.CxhGGd") or _first(soup, FLIPKART_PRICE)
                    price = _clean_price(price_el.get_text() if price_el else None)
                    if price is None:
                        rupee = _RUPEE_PATTERN.search(page_text)
                        price = _clean_price(rupee.group(1)) if rupee else None
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