"""
Scraper for 2dehands.be — student rooms (koten) in Leuven.

Search: /q/kot+leuven/ — static HTML.  Detail pages embed a schema.org
JSON-LD block (Product/Accommodation) with the title, description,
price and availability, plus a "Kenmerken" attribute list in the HTML.
We use those instead of guessing at CSS classes.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import debug_log
from models import KotListing
from scrapers.base import (get_text, is_unavailable, make_session,
                           parse_euro_amount, polite_delay, yes_if_mentioned)

BASE = "https://www.2dehands.be"
SEARCH_URL = "https://www.2dehands.be/q/kot+leuven/"

log = debug_log.get("2dehands")

# Only follow supply categories — skip "looking for" / wanted ads
SUPPLY_CATEGORIES = {
    "appartementen-en-studio-s-te-huur",
    "kamers-te-huur",
    "studentenkamers-te-huur",
    "woningen-te-huur",
    "gemeubelde-appartementen-te-huur",
}


def _get_immo_links(soup) -> list[str]:
    """Unique /v/immo/ hrefs that are supply listings (not wanted ads)."""
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/v/immo/" not in href or href in seen:
            continue
        parts = href.split("/")
        category = parts[3] if len(parts) > 3 else ""
        if category and category not in SUPPLY_CATEGORIES:
            log.debug(f"Skipping non-supply category: {category}")
            continue
        seen.add(href)
        links.append(href)
    log.debug(f"Immo supply links found: {len(links)}")
    return links


def _extract_ld_product(html: str) -> dict | None:
    """Find the schema.org Product/Accommodation JSON-LD block."""
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                         html, re.S):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(d, dict) and "offers" in d:
            return d
    return None


def _extract_attributes(soup) -> dict[str, str]:
    """Parse the 'Kenmerken' list (Attributes-module-label/value pairs)."""
    attrs = {}
    for item in soup.find_all(class_=re.compile(r"Attributes-module-item")):
        label = item.find(class_=re.compile(r"Attributes-module-label"))
        value = item.find(class_=re.compile(r"Attributes-module-value"))
        if label and value:
            attrs[get_text(label).lower()] = get_text(value)
    if attrs:
        log.debug(f"Kenmerken: {attrs}")
    return attrs


def _parse_detail(session, url: str) -> KotListing | None:
    polite_delay()
    try:
        resp = session.get(url, timeout=15)
        debug_log.log_request(log, url, resp.status_code,
                              resp.headers.get("content-type", ""))
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Detail failed {url}: {e}")
        return None

    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    ld = _extract_ld_product(html)
    if ld is None:
        log.warning(f"No JSON-LD product data on {url}")
        debug_log.log_html_sample(log, url, html, chars=2000)

    title = (ld or {}).get("name") or get_text(soup.find("h1"))
    description = (ld or {}).get("description") or ""

    # Availability: JSON-LD says InStock/OutOfStock; also check the title
    # for explicit "verhuurd" wording.
    offers = (ld or {}).get("offers") or {}
    availability = str(offers.get("availability") or "")
    if "OutOfStock" in availability or is_unavailable(title):
        log.debug(f"Not available — skip: {url}")
        return None

    # City comes from the analytics config blob ("cityName":"Leuven")
    city_m = re.search(r'"cityName":"([^"]+)"', html)
    city = city_m.group(1) if city_m else ""
    # Keep only Leuven listings — the text search is broad.
    searchable = f"{title} {description} {city}".lower()
    if city.lower() != "leuven" and "leuven" not in searchable and "3000" not in searchable:
        log.debug(f"Not Leuven (city={city!r}) — skip: {url}")
        return None

    listing = KotListing(source="2dehands.be", url=url, title=title,
                         scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"))

    listing.price_eur_month = parse_euro_amount(str(offers.get("price") or ""))
    if not listing.price_eur_month:
        # Fallback: hydration state has priceCents
        m = re.search(r'"priceCents":\s*(\d+)', html)
        if m:
            listing.price_eur_month = f"{int(m.group(1)) / 100:g}"
    debug_log.log_field(log, "price", str(offers.get("price")), listing.price_eur_month)

    listing.neighborhood = city
    if city:
        listing.address = city  # street addresses are rarely published here

    attrs = _extract_attributes(soup)
    # floorSize / Woonoppervlakte, e.g. "Minder dan 20 m²" or "25 m²"
    floor = attrs.get("woonoppervlakte") or str((ld or {}).get("floorSize") or "")
    m2 = re.search(r"(\d+)\s*m", floor)
    if m2:
        listing.size_m2 = m2.group(1)
    else:
        m2 = re.search(r"(\d+)\s*m[²2]", description, re.I)
        if m2:
            listing.size_m2 = m2.group(1)
    debug_log.log_field(log, "size", floor[:40], listing.size_m2)

    low = f"{title} {description}".lower()
    if "studio" in low:
        listing.listing_type = "Studio"
    elif "appartement" in low or "apartment" in low:
        listing.listing_type = "Appartement"
    elif "kamer" in low or "kot" in low or "room" in low:
        listing.listing_type = "Kamer"

    # Free text can confirm a feature but never deny one -> Yes or empty.
    listing.furnished          = yes_if_mentioned(low, ["gemeubeld", "gemeubileerd", "bemeubeld", "furnished"])
    listing.private_bathroom   = yes_if_mentioned(low, ["eigen badkamer", "eigen douche", "eigen sanitair",
                                                        "private badkamer", "own bathroom"])
    listing.shared_bathroom    = yes_if_mentioned(low, ["gedeelde badkamer", "gemeenschappelijke badkamer",
                                                        "shared bathroom"])
    listing.private_kitchen    = yes_if_mentioned(low, ["eigen keuken", "kitchenette", "private keuken"])
    listing.shared_kitchen     = yes_if_mentioned(low, ["gedeelde keuken", "gemeenschappelijke keuken",
                                                        "shared kitchen"])
    listing.internet_included  = yes_if_mentioned(low, ["internet inbegrepen", "incl. internet",
                                                        "inclusief internet", "wifi inbegrepen",
                                                        "internet included"])
    listing.utilities_included = yes_if_mentioned(low, ["all-in", "all in", "kosten inbegrepen",
                                                        "inclusief kosten", "lasten inbegrepen",
                                                        "charges incluses"])
    listing.washing_machine    = yes_if_mentioned(low, ["wasmachine", "washing machine"])
    listing.elevator           = yes_if_mentioned(low, ["lift ", "lift,", "lift.", "elevator"])
    listing.pets_allowed       = yes_if_mentioned(low, ["huisdieren toegelaten", "huisdieren welkom",
                                                        "pets allowed"])

    avail = re.search(r"beschikbaar\s+(?:vanaf|per)\s*[:\-]?\s*([\w\d/\-\. ]{3,20})",
                      description, re.I)
    if avail:
        listing.available_from = avail.group(1).strip()

    listing.compute_price_per_m2()
    return listing


def scrape(test_mode: bool = False) -> list[KotListing]:
    session = make_session()
    results: list[KotListing] = []
    page = 1

    log.info("  [2dehands.be] Starting search...")
    print("  [2dehands.be] Starting search...")

    while True:
        url = SEARCH_URL if page == 1 else SEARCH_URL + f"p/{page}/"
        polite_delay()
        try:
            resp = session.get(url, timeout=15)
            debug_log.log_request(log, resp.url, resp.status_code,
                                  resp.headers.get("content-type", ""))
            resp.raise_for_status()
        except Exception as e:
            log.error(f"  [2dehands.be] Page {page} failed: {e}")
            print(f"  [2dehands.be] Page {page} failed: {e}")
            break

        soup = BeautifulSoup(resp.text, "lxml")
        immo_hrefs = _get_immo_links(soup)

        if not immo_hrefs:
            if page == 1:
                log.warning("  [2dehands.be] No immo links on page 1 — check debug.log")
                debug_log.log_html_sample(log, url, resp.text, chars=3000)
            break

        log.info(f"  [2dehands.be] Page {page}: {len(immo_hrefs)} immo links")
        print(f"  [2dehands.be] Page {page}: {len(immo_hrefs)} immo links")

        for href in immo_hrefs:
            detail_url = urljoin(BASE, href)
            try:
                listing = _parse_detail(session, detail_url)
                if listing is not None:
                    results.append(listing)
                    print(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
                    log.info(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
            except Exception as e:
                log.warning(f"    [SKIP] {detail_url}: {e}", exc_info=True)

            if test_mode and len(results) >= 3:
                log.info(f"  [2dehands.be] Done (test) — {len(results)} listings.")
                print(f"  [2dehands.be] Done (test) — {len(results)} listings.")
                return results

        next_page_link = soup.find("a", href=re.compile(rf"/q/kot\+leuven/p/{page + 1}/"))
        if not next_page_link:
            break
        page += 1

    log.info(f"  [2dehands.be] Done — {len(results)} available listings.")
    print(f"  [2dehands.be] Done — {len(results)} available listings.")
    return results
