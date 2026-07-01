"""
Scraper for immoweb.be

When Playwright loads an Immoweb search URL, Chromium detects the JSON
content-type and wraps the raw API response in:
  <html><body><pre>{ JSON }</pre></body></html>

So we parse the <pre> tag content as JSON and look in results[].
We also filter client-side on Leuven postal codes because the server
sometimes ignores the municipality parameter for headless browsers.
"""
from __future__ import annotations

import json
import re
from datetime import datetime

import debug_log
from models import KotListing
from scrapers.base import is_unavailable, polite_delay

# Leuven + all officially merged sub-municipalities (Heverlee, Kessel-Lo, etc.)
LEUVEN_POSTCODES = {
    "3000", "3001", "3010", "3012", "3018",
    "3020", "3040", "3051", "3052", "3053", "3054",
}
LEUVEN_LOCALITIES = {
    "leuven", "louvain", "heverlee", "kessel-lo", "kessel lo",
    "wilsele", "wijgmaal", "korbeek-lo", "korbeek lo",
    "sint-joris-weert", "blanden", "vaalbeek", "oud-heverlee",
}

# APARTMENT + postalCodes = 450 Leuven results, all correct.
# HOUSE + postalCodes = 57 Leuven results but only houses.
# municipality= param is ignored by the API without postalCodes.
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

log = debug_log.get("immoweb")


def _is_leuven(ad: dict) -> bool:
    loc = (ad.get("property") or {}).get("location") or {}
    postcode = str(loc.get("postalCode") or "")
    locality = str(loc.get("locality") or "").lower()
    return postcode in LEUVEN_POSTCODES or locality in LEUVEN_LOCALITIES


def _is_kot_sized(ad: dict) -> bool:
    """
    Exclude large family houses.  A kot/studio is almost never > 100 m² or > €1200/month.
    Returns True (keep) for anything small, or when size/price is unknown.
    """
    prop  = ad.get("property") or {}
    txn   = ad.get("transaction") or {}
    rental = txn.get("rental") or {}

    size  = prop.get("netHabitableSurface") or prop.get("livingSurface") or prop.get("totalSurface")
    price = rental.get("monthlyRentalPrice") or txn.get("price")

    too_big       = size  is not None and float(size)  > 100
    too_expensive = price is not None and float(price) > 1400

    if too_big and too_expensive:
        return False  # definitely a family house, skip
    return True


def _yes_no(val) -> str:
    if val is True or val == 1:
        return "Ja"
    if val is False or val == 0:
        return "Nee"
    if isinstance(val, str) and val.lower() in ("yes", "ja", "oui", "true"):
        return "Ja"
    if isinstance(val, str) and val.lower() in ("no", "nee", "non", "false"):
        return "Nee"
    return "missing"


def _extract_json_from_pre(html: str) -> dict | None:
    """
    Chromium wraps JSON responses in <html><body><pre>...</pre></body></html>.
    Pull that out and parse it.
    """
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

    listing = KotListing(
        source="immoweb.be",
        scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"),
    )

    # Define prop and loc first — URL construction needs them
    prop = ad.get("property") or {}
    loc  = prop.get("location") or {}

    # Build URL from id + location — Immoweb JSON has no url field.
    # Confirmed pattern: /en/classified/{type}/for-rent/{locality}/{postalCode}/{id}
    _type     = (prop.get("type") or "house").lower().replace("_", "-")
    _locality = (loc.get("locality") or "leuven").lower().replace(" ", "-")
    _postcode = loc.get("postalCode") or "3000"
    _id       = ad.get("id", "")
    listing.url = (
        ad.get("url")
        or ad.get("permalink")
        or f"https://www.immoweb.be/en/classified/{_type}/for-rent/{_locality}/{_postcode}/{_id}"
    )

    street = str(loc.get("street") or "")
    number = str(loc.get("number") or "")
    zip_   = str(loc.get("postalCode") or "")
    city   = str(loc.get("locality") or "")

    addr_parts = [f"{street} {number}".strip(), f"{zip_} {city}".strip()]
    listing.address = ", ".join(p for p in addr_parts if p) or "missing"
    listing.neighborhood = loc.get("district") or loc.get("locality") or "missing"

    txn    = ad.get("transaction") or {}
    rental = txn.get("rental") or {}

    price_val = rental.get("monthlyRentalPrice") or txn.get("price") or ad.get("price")
    listing.price_eur_month = str(int(float(price_val))) if price_val else "missing"

    charges = rental.get("monthlyRentalCharges") or rental.get("charges")
    listing.utilities_included = "Ja" if charges and float(charges) > 0 else "Nee"

    size_val = (
        prop.get("netHabitableSurface")
        or prop.get("livingSurface")
        or prop.get("totalSurface")
    )
    listing.size_m2 = str(int(float(size_val))) if size_val else "missing"

    sub_type = prop.get("subtype") or prop.get("type") or ""
    listing.listing_type = sub_type.replace("_", " ").capitalize() if sub_type else "missing"
    listing.title = (
        prop.get("title")
        or ad.get("title")
        or f"{listing.listing_type} — {listing.address}"
    )

    listing.furnished = _yes_no(prop.get("furnished") or prop.get("isFurnished"))

    bath = prop.get("bathroomCount") or prop.get("showerRoomCount") or 0
    listing.private_bathroom = "Ja" if int(bath) > 0 else "Nee"
    listing.shared_bathroom  = "Nee" if int(bath) > 0 else "missing"

    kitchen = prop.get("kitchen") or {}
    k_type = kitchen.get("type", "") if isinstance(kitchen, dict) else str(kitchen)
    listing.private_kitchen = "Ja" if k_type else "missing"

    avail = txn.get("availabilityDate") or txn.get("availableFrom")
    listing.available_from = avail[:10] if avail else "missing"

    listing.compute_price_per_m2()
    return listing


def _scrape_url(page, search_url: str, test_mode: bool,
                page_num: int = 1) -> tuple[list[KotListing], bool, int]:
    """
    Load one search URL with Playwright, extract listings.
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

    # Primary: Chromium JSON-viewer wraps API response in <pre>
    data = _extract_json_from_pre(html)

    if data is None:
        log.debug("No <pre> JSON found — trying __NEXT_DATA__")
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html, re.S)
        if m:
            try:
                data = json.loads(m.group(1))
                log.debug("Found __NEXT_DATA__")
                for path in [
                    ["props", "pageProps", "classifiedList"],
                    ["props", "pageProps", "results"],
                ]:
                    node = data
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

    # Log what top-level keys we got
    log.debug(f"JSON top-level keys: {list(data.keys())[:10]}")
    if "criteria" in data:
        log.debug(f"Immoweb criteria: {data['criteria']}")

    total_items = data.get("totalItems", "?")
    log.info(f"  [immoweb.be] totalItems={total_items}")
    print(f"  [immoweb.be] totalItems={total_items}")

    ads = data.get("results") or data.get("classifiedList") or []
    log.info(f"  [immoweb.be] Raw ads this page: {len(ads)}")
    print(f"  [immoweb.be] Raw ads this page: {len(ads)}")

    if not ads:
        log.warning("  [immoweb.be] results[] is empty for this URL")
        return [], False, 0

    # Log first 3 ad locations so we can see if municipality filter works
    for i, ad in enumerate(ads[:3]):
        loc = (ad.get("property") or {}).get("location") or {}
        log.debug(f"  Sample ad[{i}] location: postcode={loc.get('postalCode')} "
                  f"locality={loc.get('locality')} type={ad.get('property', {}).get('type')}")

    # Filter: only Leuven + not a large family house
    leuven_ads = [a for a in ads if _is_leuven(a) and _is_kot_sized(a)]
    log.info(f"  [immoweb.be] After Leuven+size filter: {len(leuven_ads)} / {len(ads)} ads")
    print(f"  [immoweb.be] After Leuven+size filter: {len(leuven_ads)} / {len(ads)} ads")

    if not leuven_ads and page_num == 1:
        log.warning("  [immoweb.be] 0 Leuven/kot-sized results on page 1")
        return [], True, total_items  # URL responded but wrong geography

    results: list[KotListing] = []
    for ad in leuven_ads:
        try:
            listing = _parse_ad(ad)
            if listing:
                results.append(listing)
                print(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
                log.info(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
        except Exception as e:
            log.warning(f"    Ad parse error: {e}")

        if test_mode and len(results) >= 3:
            break

    return results, True, total_items


def _scrape_with_playwright(test_mode: bool) -> list[KotListing]:
    from playwright.sync_api import sync_playwright

    all_results: list[KotListing] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
        )
        page = context.new_page()

        for search_url in SEARCH_CANDIDATES:
            results, url_worked, total_items = _scrape_url(page, search_url, test_mode, page_num=1)

            if not url_worked:
                continue  # try next candidate URL

            if not results:
                # URL responded but gave 0 Leuven results on page 1 — try next candidate
                log.info(f"  [immoweb.be] Skipping URL (0 Leuven results): {search_url[:70]}")
                continue

            all_results.extend(results)
            if test_mode:
                break

            # Paginate — use totalItems to know how many pages exist
            per_page = 30
            try:
                max_pages = min(int(total_items) // per_page + 2, 100)
            except (TypeError, ValueError):
                max_pages = 30
            log.info(f"  [immoweb.be] Will paginate up to {max_pages} pages (totalItems={total_items})")

            for pg in range(2, max_pages + 1):
                next_url = search_url + f"&page={pg}"
                page_results, worked, _ = _scrape_url(page, next_url, test_mode, page_num=pg)
                if not worked or (not page_results and pg > 3):
                    # Stop if 3 consecutive empty pages or load failure
                    break
                all_results.extend(page_results)

            break  # found a working URL with Leuven results, stop trying others

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
        msg = "Playwright not installed. Run: playwright install chromium"
        log.error(f"  [immoweb.be] {msg}")
        print(f"  [immoweb.be] {msg}")
        return []
    except Exception as e:
        log.error(f"  [immoweb.be] Unexpected error: {e}", exc_info=True)
        print(f"  [immoweb.be] Unexpected error: {e}")
        return []
