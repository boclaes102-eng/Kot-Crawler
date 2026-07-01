"""
Scraper for zimmo.be — student rooms in Leuven.  BEST EFFORT.

Zimmo is fully JS-rendered and has bot detection, so everything —
search AND detail pages — goes through Playwright (the old version
fetched detail pages with plain requests and got empty shells back).

Detail links look like /nl/<slug>/te-huur/.../<code>/ — we collect all
anchors whose href contains "/te-huur/" and has a listing code segment.
If zimmo changes markup again, debug.log will contain the link list and
an HTML sample to fix the selectors from.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import debug_log
from models import KotListing
from scrapers.base import (get_text, is_unavailable, launch_browser,
                           new_browser_context, parse_euro_amount,
                           yes_if_mentioned)

BASE = "https://www.zimmo.be"

SEARCH_CANDIDATES = [
    "https://www.zimmo.be/nl/leuven/te-huur/studentenkamer/",
    "https://www.zimmo.be/nl/leuven/te-huur/studio/",
    "https://www.zimmo.be/nl/leuven/te-huur/",
]

# Listing detail hrefs contain /te-huur/ plus a short uppercase code
# segment, e.g. /nl/leuven-3000/te-huur/studio/K2QJW/
DETAIL_HREF_RE = re.compile(r"/te-huur/.*/[A-Z0-9]{4,8}/?$")

log = debug_log.get("zimmo")


def _collect_detail_links(soup) -> list[str]:
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"].split("?")[0]
        if not DETAIL_HREF_RE.search(href):
            continue
        if href in seen:
            continue
        seen.add(href)
        links.append(href)
    return links


def _extract_ld(html: str) -> dict | None:
    for m in re.finditer(r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                         html, re.S):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and (it.get("offers") or it.get("address")):
                return it
    return None


def _parse_detail_html(html: str, url: str) -> KotListing | None:
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("main") or soup
    main_text = main.get_text(" ")
    title = get_text(soup.find("h1") or soup.find("h2"))

    if is_unavailable(title) or is_unavailable(main_text[:800]):
        log.debug(f"Marked unavailable — skip: {url}")
        return None

    listing = KotListing(source="zimmo.be", url=url, title=title,
                         scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"))

    ld = _extract_ld(html)
    if ld:
        log.debug(f"JSON-LD: type={ld.get('@type')}")
        addr = ld.get("address") or {}
        if isinstance(addr, dict):
            street = addr.get("streetAddress") or ""
            locality = addr.get("addressLocality") or ""
            listing.address = ", ".join(p for p in (street, locality) if p)
            listing.neighborhood = locality
        offers = ld.get("offers") or {}
        if isinstance(offers, dict) and offers.get("price"):
            listing.price_eur_month = parse_euro_amount(str(offers["price"]))

    if not listing.price_eur_month:
        m = re.search(r"€\s*([\d.,]+)", main_text)
        if m:
            listing.price_eur_month = parse_euro_amount(m.group(1))
    debug_log.log_field(log, "price", "", listing.price_eur_month)

    m2 = re.search(r"(\d+)\s*m[²2]", main_text, re.I)
    if m2:
        listing.size_m2 = m2.group(1)

    low = main_text.lower()
    if "studentenkamer" in low or "kamer" in low:
        listing.listing_type = "Kamer"
    if "studio" in low:
        listing.listing_type = "Studio"
    elif "appartement" in low and not listing.listing_type:
        listing.listing_type = "Appartement"

    listing.furnished          = yes_if_mentioned(low, ["gemeubeld", "gemeubileerd", "bemeubeld"])
    listing.private_bathroom   = yes_if_mentioned(low, ["eigen badkamer", "eigen douche", "eigen sanitair"])
    listing.shared_bathroom    = yes_if_mentioned(low, ["gedeelde badkamer", "gemeenschappelijke badkamer"])
    listing.private_kitchen    = yes_if_mentioned(low, ["eigen keuken", "kitchenette"])
    listing.shared_kitchen     = yes_if_mentioned(low, ["gedeelde keuken", "gemeenschappelijke keuken"])
    listing.internet_included  = yes_if_mentioned(low, ["internet inbegrepen", "inclusief internet",
                                                        "wifi inbegrepen"])
    listing.utilities_included = yes_if_mentioned(low, ["kosten inbegrepen", "all-in", "all in",
                                                        "verwarming inbegrepen"])
    listing.washing_machine    = yes_if_mentioned(low, ["wasmachine"])
    listing.elevator           = yes_if_mentioned(low, ["lift ", "lift,", "lift."])
    listing.pets_allowed       = yes_if_mentioned(low, ["huisdieren toegelaten", "huisdieren welkom"])

    avail = re.search(r"beschikbaar\s+(?:vanaf|per)?\s*[:\-]?\s*([\d]{1,2}[-/][\d]{1,2}[-/][\d]{2,4})",
                      main_text, re.I)
    if avail:
        listing.available_from = avail.group(1).strip()

    listing.compute_price_per_m2()
    return listing


def _scrape_with_playwright(test_mode: bool) -> list[KotListing]:
    from playwright.sync_api import sync_playwright

    results: list[KotListing] = []

    with sync_playwright() as pw:
        browser = launch_browser(pw)
        context = new_browser_context(browser)
        page = context.new_page()

        for search_url in SEARCH_CANDIDATES:
            log.info(f"  [zimmo.be] Trying: {search_url}")
            print(f"  [zimmo.be] Trying: {search_url}")
            try:
                resp = page.goto(search_url, wait_until="networkidle", timeout=30_000)
                debug_log.log_request(log, search_url, resp.status if resp else 0, "")
                page.wait_for_timeout(2000)
            except Exception as e:
                log.warning(f"  [zimmo.be] Load failed: {e}")
                continue

            html = page.content()
            soup = BeautifulSoup(html, "lxml")
            links = _collect_detail_links(soup)
            log.info(f"  [zimmo.be] Detail links found: {len(links)}")
            print(f"  [zimmo.be] Detail links found: {len(links)}")

            if not links:
                debug_log.log_html_sample(log, search_url, html, chars=2500)
                all_links = [a.get("href", "") for a in soup.find_all("a", href=True)]
                log.debug(f"All page links: {all_links[:50]}")
                continue

            for href in links:
                detail_url = urljoin(BASE, href)
                try:
                    resp = page.goto(detail_url, wait_until="domcontentloaded", timeout=25_000)
                    debug_log.log_request(log, detail_url, resp.status if resp else 0, "")
                    page.wait_for_timeout(1500)
                    listing = _parse_detail_html(page.content(), detail_url)
                    if listing is not None:
                        results.append(listing)
                        print(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
                        log.info(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
                except Exception as e:
                    log.warning(f"    [SKIP] {detail_url}: {e}")

                if test_mode and len(results) >= 3:
                    browser.close()
                    return results

            if results:
                break  # this search URL worked — no need to try broader ones

        browser.close()
    return results


def scrape(test_mode: bool = False) -> list[KotListing]:
    log.info("  [zimmo.be] Starting search (Playwright)...")
    print("  [zimmo.be] Starting search (Playwright)...")
    try:
        results = _scrape_with_playwright(test_mode)
        log.info(f"  [zimmo.be] Done — {len(results)} available listings.")
        print(f"  [zimmo.be] Done — {len(results)} available listings.")
        return results
    except ImportError:
        log.error("  [zimmo.be] Playwright not installed")
        print("  [zimmo.be] Playwright not installed — skipping")
        return []
    except Exception as e:
        log.error(f"  [zimmo.be] Error: {e}", exc_info=True)
        return []
