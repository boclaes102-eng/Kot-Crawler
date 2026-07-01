"""
Scraper for immoweb.be

When Playwright loads an Immoweb search-results URL, Chromium detects
the JSON content-type and wraps the raw API response in:
  <html><body><pre>{ JSON }</pre></body></html>

So we parse the <pre> tag content as JSON and read results[].
We filter client-side on Leuven postal codes because the server
sometimes ignores the municipality parameter for headless browsers.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import debug_log
from models import KotListing
from scrapers.base import launch_browser, new_browser_context, yes_no

# Leuven + merged sub-municipalities (Heverlee, Kessel-Lo, ...)
LEUVEN_POSTCODES = {
    "3000", "3001", "3010", "3012", "3018",
    "3020", "3040", "3051", "3052", "3053", "3054",
}
LEUVEN_LOCALITIES = {
    "leuven", "louvain", "heverlee", "kessel-lo", "kessel lo",
    "wilsele", "wijgmaal", "korbeek-lo", "korbeek lo",
    "sint-joris-weert", "blanden", "vaalbeek", "oud-heverlee",
}

SEARCH_CANDIDATES = [
    (
        "https://www.immoweb.be/en/search-results/apartment/for-rent"
        "?countries=BE&postalCodes=3000,3001,3010,3012,3018&orderBy=newest"
    ),
    (
        "https://www.immoweb.be/en/search-results/student-room/for-rent"
        "?countries=BE&postalCodes=3000,3001,3010,3012,3018&orderBy=newest"
    ),
]

# A kot/studio is almost never bigger/pricier than this; listings above
# BOTH thresholds are family houses and get skipped.
MAX_KOT_SIZE_M2 = 100
MAX_KOT_PRICE = 1400

log = debug_log.get("immoweb")


def _is_leuven(ad: dict) -> bool:
    loc = (ad.get("property") or {}).get("location") or {}
    postcode = str(loc.get("postalCode") or "")
    locality = str(loc.get("locality") or "").lower()
    return postcode in LEUVEN_POSTCODES or locality in LEUVEN_LOCALITIES


def _is_kot_sized(ad: dict) -> bool:
    """Skip listings that are clearly large family homes.

    Keeps anything small, and keeps listings whose size/price is unknown.
    Only skips when the listing is BOTH too big AND too expensive.
    """
    prop = ad.get("property") or {}
    txn = ad.get("transaction") or {}
    rental = txn.get("rental") or {}

    size = prop.get("netHabitableSurface") or prop.get("livingSurface") or prop.get("totalSurface")
    price = rental.get("monthlyRentalPrice") or txn.get("price")

    too_big = size is not None and float(size) > MAX_KOT_SIZE_M2
    too_expensive = price is not None and float(price) > MAX_KOT_PRICE
    return not (too_big and too_expensive)


def _extract_json_from_pre(html: str) -> dict | None:
    m = re.search(r"<pre[^>]*>(.*?)</pre>", html, re.S | re.I)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError as e:
        log.debug(f"<pre> JSON parse failed: {e}")
        return None


def _parse_ad(ad: dict) -> KotListing | None:
    flags = ad.get("flags") or {}
    if flags.get("isSoldOrRented") or flags.get("isRented"):
        return None

    listing = KotListing(source="immoweb.be",
                         scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"))

    prop = ad.get("property") or {}
    loc = prop.get("location") or {}

    # Immoweb JSON has no url field — build it from the confirmed pattern
    # /en/classified/{type}/for-rent/{locality}/{postalCode}/{id}
    _type = (prop.get("type") or "apartment").lower().replace("_", "-")
    _locality = (loc.get("locality") or "leuven").lower().replace(" ", "-")
    _postcode = loc.get("postalCode") or "3000"
    _id = ad.get("id", "")
    listing.url = (
        ad.get("url")
        or ad.get("permalink")
        or f"https://www.immoweb.be/en/classified/{_type}/for-rent/{_locality}/{_postcode}/{_id}"
    )

    street = str(loc.get("street") or "")
    number = str(loc.get("number") or "")
    zip_ = str(loc.get("postalCode") or "")
    city = str(loc.get("locality") or "")

    addr_parts = [f"{street} {number}".strip(), f"{zip_} {city}".strip()]
    listing.address = ", ".join(p for p in addr_parts if p)
    listing.neighborhood = loc.get("district") or loc.get("locality") or ""

    txn = ad.get("transaction") or {}
    rental = txn.get("rental") or {}

    price_val = rental.get("monthlyRentalPrice") or txn.get("price") or ad.get("price")
    listing.price_eur_month = str(int(float(price_val))) if price_val else ""
    debug_log.log_field(log, "price", str(price_val), listing.price_eur_month)

    size_val = (prop.get("netHabitableSurface")
                or prop.get("livingSurface")
                or prop.get("totalSurface"))
    listing.size_m2 = str(int(float(size_val))) if size_val else ""

    sub_type = prop.get("subtype") or prop.get("type") or ""
    listing.listing_type = sub_type.replace("_", " ").capitalize() if sub_type else ""
    listing.title = (prop.get("title") or ad.get("title")
                     or " — ".join(p for p in (listing.listing_type, listing.address) if p))

    furn = prop.get("furnished")
    if furn is None:
        furn = prop.get("isFurnished")
    listing.furnished = yes_no(bool(furn)) if furn is not None else ""

    bath = prop.get("bathroomCount") or prop.get("showerRoomCount")
    if bath is not None:
        listing.private_bathroom = yes_no(int(bath) > 0)

    kitchen = prop.get("kitchen") or {}
    k_type = kitchen.get("type", "") if isinstance(kitchen, dict) else str(kitchen)
    if k_type:
        listing.private_kitchen = "Yes"

    # NOTE: monthlyRentalCharges > 0 means costs are charged ON TOP of
    # the rent — it says nothing about what is included, so we leave
    # utilities_included empty (unknown) for immoweb.

    avail = txn.get("availabilityDate") or txn.get("availableFrom")
    listing.available_from = str(avail)[:10] if avail else ""

    listing.compute_price_per_m2()
    return listing


def _scrape_url(page, search_url: str, test_mode: bool,
                page_num: int = 1) -> tuple[list[KotListing], bool, int]:
    """Load one search URL, extract listings.

    Returns (listings, url_worked, total_items).
    """
    log.info(f"  [immoweb.be] Trying: {search_url}")
    print(f"  [immoweb.be] Loading: {search_url[:80]}...")

    try:
        resp = page.goto(search_url, wait_until="domcontentloaded", timeout=30_000)
        status = resp.status if resp else 0
        debug_log.log_request(log, search_url, status, "")
        page.wait_for_timeout(3000)
    except Exception as e:
        log.error(f"  [immoweb.be] Page load failed: {e}")
        return [], False, 0

    html = page.content()
    log.debug(f"  Page HTML length: {len(html)} chars")

    data = _extract_json_from_pre(html)

    if data is None:
        log.debug("No <pre> JSON found — trying __NEXT_DATA__")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                nxt = json.loads(m.group(1))
                for path in (["props", "pageProps", "classifiedList"],
                             ["props", "pageProps", "results"]):
                    node = nxt
                    try:
                        for key in path:
                            node = node[key]
                        if isinstance(node, list):
                            data = {"results": node}
                            break
                    except (KeyError, TypeError):
                        continue
            except json.JSONDecodeError:
                pass

    if data is None:
        log.warning("  [immoweb.be] No JSON data found in page")
        debug_log.log_html_sample(log, search_url, html, chars=3000)
        return [], False, 0

    log.debug(f"JSON top-level keys: {list(data.keys())[:10]}")
    total_items = data.get("totalItems", 0)
    log.info(f"  [immoweb.be] totalItems={total_items}")

    ads = data.get("results") or data.get("classifiedList") or []
    log.info(f"  [immoweb.be] Raw ads this page: {len(ads)}")

    if not ads:
        return [], True, total_items

    for i, ad in enumerate(ads[:3]):
        loc = (ad.get("property") or {}).get("location") or {}
        log.debug(f"  Sample ad[{i}] location: postcode={loc.get('postalCode')} "
                  f"locality={loc.get('locality')} type={ad.get('property', {}).get('type')}")

    leuven_ads = [a for a in ads if _is_leuven(a) and _is_kot_sized(a)]
    log.info(f"  [immoweb.be] After Leuven+size filter: {len(leuven_ads)} / {len(ads)} ads")
    print(f"  [immoweb.be] Page {page_num}: {len(leuven_ads)} / {len(ads)} ads after filter")

    results: list[KotListing] = []
    for ad in leuven_ads:
        try:
            listing = _parse_ad(ad)
            if listing:
                results.append(listing)
                print(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
                log.info(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
        except Exception as e:
            log.warning(f"    Ad parse error: {e}", exc_info=True)

        if test_mode and len(results) >= 3:
            break

    return results, True, total_items


def _scrape_with_playwright(test_mode: bool) -> list[KotListing]:
    from playwright.sync_api import sync_playwright

    all_results: list[KotListing] = []

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        context = new_browser_context(browser)
        page = context.new_page()

        for search_url in SEARCH_CANDIDATES:
            results, url_worked, total_items = _scrape_url(page, search_url, test_mode, page_num=1)

            if not url_worked:
                continue
            if not results:
                log.info(f"  [immoweb.be] Skipping URL (0 usable results): {search_url[:70]}")
                continue

            all_results.extend(results)
            if test_mode:
                break

            per_page = 30
            try:
                max_pages = min(int(total_items) // per_page + 2, 100)
            except (TypeError, ValueError):
                max_pages = 30
            log.info(f"  [immoweb.be] Paginating up to {max_pages} pages (totalItems={total_items})")

            empty_streak = 0
            for pg in range(2, max_pages + 1):
                next_url = search_url + f"&page={pg}"
                page_results, worked, _ = _scrape_url(page, next_url, test_mode, page_num=pg)
                if not worked:
                    break
                if not page_results:
                    empty_streak += 1
                    if empty_streak >= 3:  # 3 consecutive empty pages -> stop
                        log.info("  [immoweb.be] 3 empty pages in a row — stopping pagination")
                        break
                    continue
                empty_streak = 0
                all_results.extend(page_results)

            break  # found a working URL with results — done

        browser.close()

    return all_results


def scrape(test_mode: bool = False) -> list[KotListing]:
    log.info("  [immoweb.be] Starting (Playwright/Chromium)...")
    print("  [immoweb.be] Starting (Playwright/Chromium)...")
    try:
        results = _scrape_with_playwright(test_mode)
        log.info(f"  [immoweb.be] Done — {len(results)} available listings.")
        print(f"  [immoweb.be] Done — {len(results)} available listings.")
        return results
    except ImportError:
        msg = "Playwright not installed. Run: pip install playwright && playwright install chromium"
        log.error(f"  [immoweb.be] {msg}")
        print(f"  [immoweb.be] {msg}")
        return []
    except Exception as e:
        log.error(f"  [immoweb.be] Unexpected error: {e}", exc_info=True)
        print(f"  [immoweb.be] Unexpected error: {e}")
        return []
