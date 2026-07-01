"""
Scraper for 2dehands.be — student rooms (koten) in Leuven.
Search: /q/kot+leuven/ — static HTML, 44 results, 2 pages.
Only follows /v/immo/ links so non-immo search hits are skipped.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import debug_log
from models import KotListing
from scrapers.base import extract_number, get_text, is_unavailable, make_session, polite_delay

BASE       = "https://www.2dehands.be"
SEARCH_URL = "https://www.2dehands.be/q/kot+leuven/"

log = debug_log.get("2dehands")


def _yes_no(text: str, keywords: list[str]) -> str:
    return "Ja" if any(k in text.lower() for k in keywords) else "Nee"


# Only follow supply categories — skip "looking for" / wanted ads
SUPPLY_CATEGORIES = {
    "appartementen-en-studio-s-te-huur",
    "kamers-te-huur",
    "studentenkamers-te-huur",
    "woningen-te-huur",
    "gemeubelde-appartementen-te-huur",
}


def _get_immo_links(soup) -> list[str]:
    """Return unique /v/immo/ hrefs that are supply listings (not wanted ads)."""
    seen, links = set(), []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/v/immo/" not in href or href in seen:
            continue
        # Skip wanted/looking-for categories
        parts = href.split("/")
        category = parts[3] if len(parts) > 3 else ""
        if category and category not in SUPPLY_CATEGORIES:
            log.debug(f"Skipping non-supply category: {category}")
            continue
        seen.add(href)
        links.append(href)
    log.debug(f"Immo supply links found: {len(links)}")
    return links


def _parse_detail(session, url: str) -> KotListing | None:
    polite_delay()
    try:
        resp = session.get(url, timeout=15)
        debug_log.log_request(log, url, resp.status_code, resp.headers.get("content-type", ""))
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Detail failed {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    full_text = soup.get_text(" ")

    # Only keep Leuven listings (search is broad)
    if "leuven" not in full_text.lower() and "3000" not in full_text:
        log.debug(f"Not Leuven — skip: {url}")
        return None

    if is_unavailable(full_text[:800]):
        return None

    listing = KotListing(source="2dehands.be", url=url,
                         scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"))

    listing.title = get_text(soup.find("h1"))

    # Price: on 2dehands detail pages the price is in an <h5> that contains "€"
    # and is short (not navigation text). Check <h5> tags first, then broader search.
    price_found = False
    for tag in soup.find_all("h5"):
        t = tag.get_text(strip=True)
        # Must be short, contain € and a digit, and not be a heading/title
        if "€" in t and re.search(r"\d", t) and len(t) < 40:
            listing.price_eur_month = extract_number(t)
            debug_log.log_field(log, "price", t[:60], listing.price_eur_month)
            price_found = True
            break
    if not price_found:
        # Fallback: search page text for "€ NNN / maand" pattern
        m = re.search(r"€\s*([\d.,]+)\s*(?:/|per)\s*maand", full_text, re.I)
        if m:
            listing.price_eur_month = extract_number(m.group(0))
            debug_log.log_field(log, "price (fallback)", m.group(0)[:40], listing.price_eur_month)

    # Address / neighbourhood — look for postcode 3000-3099 or "Leuven"
    addr_match = re.search(
        r"([\w\s\-]+,?\s*(?:3[0-9]{3})\s*[\w\s\-]+|leuven[\w\s,\-]*)",
        full_text, re.I
    )
    if addr_match:
        listing.address = addr_match.group(0).strip()[:80]
        listing.neighborhood = "Leuven"

    m2 = re.search(r"(\d+)\s*m[²2]", full_text, re.I)
    if m2:
        listing.size_m2 = m2.group(1)

    low = full_text.lower()
    if "studio" in low:
        listing.listing_type = "Studio"
    elif "appartement" in low or "apartment" in low:
        listing.listing_type = "Appartement"
    elif "kamer" in low or "room" in low or "kot" in low:
        listing.listing_type = "Kamer"

    listing.furnished          = _yes_no(low, ["gemeubeld", "furnished", "meubels"])
    listing.private_bathroom   = _yes_no(low, ["eigen badkamer", "eigen douche", "private bathroom"])
    listing.shared_bathroom    = _yes_no(low, ["gedeelde badkamer", "shared bathroom"])
    listing.private_kitchen    = _yes_no(low, ["eigen keuken", "private kitchen", "kitchenette"])
    listing.shared_kitchen     = _yes_no(low, ["gedeelde keuken", "shared kitchen"])
    listing.internet_included  = _yes_no(low, ["internet", "wifi", "wi-fi"])
    listing.utilities_included = _yes_no(low, ["kosten inbegrepen", "all-in", "charges incluses",
                                                "verwarming inbegrepen"])
    listing.washing_machine    = _yes_no(low, ["wasmachine", "washing machine"])
    listing.elevator           = _yes_no(low, ["lift", "elevator"])
    listing.pets_allowed       = _yes_no(low, ["huisdieren", "pets allowed"])

    avail = re.search(r"beschikbaar\s+vanaf\s*[:\-]?\s*([\w\s\d/\-\.]+)", full_text, re.I)
    if avail:
        listing.available_from = avail.group(1).strip()[:30]

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
                log.warning(f"    [SKIP] {detail_url}: {e}")

            if test_mode and len(results) >= 3:
                log.info(f"  [2dehands.be] Done (test) — {len(results)} listings.")
                print(f"  [2dehands.be] Done (test) — {len(results)} listings.")
                return results

        # Check for next page by looking for page link beyond current
        next_page_link = soup.find("a", href=re.compile(rf"/q/kot\+leuven/p/{page + 1}/"))
        if not next_page_link:
            break
        page += 1

    log.info(f"  [2dehands.be] Done — {len(results)} available listings.")
    print(f"  [2dehands.be] Done — {len(results)} available listings.")
    return results
