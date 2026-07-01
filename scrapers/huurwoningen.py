"""
Scraper for huurwoningen.be — rental rooms in Leuven.

Detail URLs follow the pattern /huurwoning/{slug}.  The previous
version of this scraper found ~100 detail pages per run but returned 0
results: the availability check matched the word "verhuurder"
(= landlord, in the site's navigation on every page) against the
keyword "verhuurd" (= rented).  The check now uses word boundaries and
only looks at the listing content, not the page header.
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

BASE = "https://huurwoningen.be"

SEARCH_CANDIDATES = [
    "https://huurwoningen.be/studentenwoning-huren/leuven",
    "https://huurwoningen.be/kamer-huren/leuven",
    "https://huurwoningen.be/studio-huren/leuven",
    "https://huurwoningen.be/appartement-huren/leuven",
    "https://huurwoningen.be/huurwoningen/leuven",
]

# Real listing links look like /huurwoning/<slug>; links such as
# /verhuurder/huurwoning/abonnementen also contain "/huurwoning/" but
# are subscription pages — anchor the pattern to the start of the path.
DETAIL_HREF_RE = re.compile(r"^(?:https?://[^/]+)?/huurwoning/[a-z0-9-]+/?$", re.I)

log = debug_log.get("huurwoningen")


def _find_cards(soup) -> list:
    cards = (
        soup.find_all("article", class_=re.compile(r"listing|property|result|huurwoning|card", re.I))
        or soup.find_all("div", class_=re.compile(r"listing-card|property-card|huurwoning|result-item", re.I))
        or soup.find_all("li", class_=re.compile(r"listing|property|result|huurwoning", re.I))
        or [a.parent for a in soup.find_all("a", href=DETAIL_HREF_RE)]
    )
    return cards


def _find_working_url(session) -> tuple[str | None, object | None]:
    for url in SEARCH_CANDIDATES:
        polite_delay(1.5, 2.5)
        try:
            resp = session.get(url, timeout=20, allow_redirects=True)
            debug_log.log_request(log, resp.url, resp.status_code,
                                  resp.headers.get("content-type", ""))
            if resp.status_code >= 400:
                log.debug(f"Skipping {url} — HTTP {resp.status_code}")
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            cards = _find_cards(soup)
            debug_log.log_cards_found(log, "article/div/li + /huurwoning/ links", len(cards))

            if cards:
                log.info(f"  [huurwoningen.be] Working URL: {resp.url}  ({len(cards)} cards)")
                return resp.url, soup

            log.debug(f"No cards at {resp.url}")
            debug_log.log_html_sample(log, resp.url, resp.text, chars=3000)
        except Exception as e:
            log.warning(f"  [huurwoningen.be] {url} error: {e}")

    return None, None


def _extract_ld(html: str) -> dict | None:
    """Look for schema.org JSON-LD data on the detail page."""
    for m in re.finditer(r'<script type="application/ld\+json">(.*?)</script>',
                         html, re.S):
        try:
            d = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        items = d if isinstance(d, list) else [d]
        for it in items:
            if isinstance(it, dict) and it.get("@type") in (
                    "Accommodation", "Apartment", "House", "Product", "Offer",
                    "Residence", "SingleFamilyResidence", "Room"):
                return it
    return None


def _parse_detail(session, url: str) -> KotListing | None:
    polite_delay()
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        debug_log.log_request(log, url, resp.status_code,
                              resp.headers.get("content-type", ""))
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Detail failed {url}: {e}")
        return None

    html = resp.text
    soup = BeautifulSoup(html, "lxml")

    # Scope availability + content checks to the main content, not the
    # site chrome (nav/footer mention "verhuurders" on every page).
    main = soup.find("main") or soup.find("article") or soup
    main_text = main.get_text(" ")
    title = get_text(soup.find("h1") or soup.find("h2"))

    if is_unavailable(title) or is_unavailable(main_text[:1000]):
        log.debug(f"Marked unavailable — skip: {url}")
        return None

    full_text = soup.get_text(" ")
    if "leuven" not in full_text.lower() and "3000" not in full_text:
        log.debug(f"Not Leuven — skip: {url}")
        return None

    listing = KotListing(source="huurwoningen.be", url=url, title=title,
                         scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"))

    ld = _extract_ld(html)
    if ld:
        log.debug(f"JSON-LD found: type={ld.get('@type')}")
        addr = ld.get("address") or {}
        if isinstance(addr, dict):
            street = addr.get("streetAddress") or ""
            locality = addr.get("addressLocality") or ""
            listing.address = ", ".join(p for p in (street, locality) if p)
            listing.neighborhood = locality
        offers = ld.get("offers") or {}
        if isinstance(offers, dict):
            listing.price_eur_month = parse_euro_amount(str(offers.get("price") or ""))

    if not listing.address:
        addr_tag = main.find(class_=re.compile(r"listing-detail-summary__location|adres|address|locatie", re.I))
        if addr_tag:
            listing.address = get_text(addr_tag)
            parts = listing.address.replace("\n", ",").split(",")
            if len(parts) >= 2:
                listing.neighborhood = parts[-1].strip()
    debug_log.log_field(log, "address", listing.address[:80], listing.address)

    if not listing.price_eur_month:
        price_tag = main.find(class_=re.compile(r"price|prijs|rent", re.I))
        if price_tag:
            listing.price_eur_month = parse_euro_amount(price_tag.get_text())
        else:
            m = re.search(r"€\s*([\d.,]+)", main_text)
            if m:
                listing.price_eur_month = parse_euro_amount(m.group(1))
    debug_log.log_field(log, "price", "", listing.price_eur_month)

    m2 = re.search(r"(\d+)\s*m[²2]", main_text, re.I)
    if m2:
        listing.size_m2 = m2.group(1)

    low = main_text.lower()
    if "studio" in low:
        listing.listing_type = "Studio"
    elif "appartement" in low:
        listing.listing_type = "Appartement"
    elif "kamer" in low or "room" in low:
        listing.listing_type = "Kamer"

    listing.furnished          = yes_if_mentioned(low, ["gemeubeld", "gemeubileerd", "furnished"])
    listing.private_bathroom   = yes_if_mentioned(low, ["eigen badkamer", "eigen douche", "eigen sanitair"])
    listing.shared_bathroom    = yes_if_mentioned(low, ["gedeelde badkamer", "gemeenschappelijke badkamer"])
    listing.private_kitchen    = yes_if_mentioned(low, ["eigen keuken", "kitchenette"])
    listing.shared_kitchen     = yes_if_mentioned(low, ["gedeelde keuken", "gemeenschappelijke keuken"])
    listing.internet_included  = yes_if_mentioned(low, ["internet inbegrepen", "inclusief internet",
                                                        "incl. internet", "wifi inbegrepen"])
    listing.utilities_included = yes_if_mentioned(low, ["kosten inbegrepen", "all-in", "all in",
                                                        "inclusief kosten", "verwarming inbegrepen"])
    listing.washing_machine    = yes_if_mentioned(low, ["wasmachine"])
    listing.elevator           = yes_if_mentioned(low, ["lift ", "lift,", "lift."])
    listing.pets_allowed       = yes_if_mentioned(low, ["huisdieren toegelaten", "huisdieren welkom"])

    avail = re.search(r"beschikbaar\s+(?:vanaf|per)?\s*[:\-]?\s*([\d]{1,2}[-/][\d]{1,2}[-/][\d]{2,4}|[\w]+ \d{4})",
                      main_text, re.I)
    if avail:
        listing.available_from = avail.group(1).strip()

    listing.compute_price_per_m2()
    return listing


def scrape(test_mode: bool = False) -> list[KotListing]:
    session = make_session()
    results: list[KotListing] = []

    log.info("  [huurwoningen.be] Starting search...")
    print("  [huurwoningen.be] Starting search...")

    search_url, first_soup = _find_working_url(session)
    if search_url is None:
        log.warning("  [huurwoningen.be] No working URL found — check debug.log")
        print("  [huurwoningen.be] No working URL found — check debug.log")
        return []

    soup = first_soup
    page = 1
    seen_urls: set[str] = set()

    while True:
        cards = _find_cards(soup)
        log.info(f"  [huurwoningen.be] Page {page}: {len(cards)} cards")
        print(f"  [huurwoningen.be] Page {page}: {len(cards)} cards")

        for card in cards:
            link_tag = card.find("a", href=DETAIL_HREF_RE) if hasattr(card, "find") else None
            if not link_tag:
                continue
            detail_url = urljoin(BASE, link_tag["href"])
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            card_text = card.get_text(" ") if hasattr(card, "get_text") else ""
            if is_unavailable(card_text):
                log.debug(f"Card marked unavailable — skip: {detail_url}")
                continue

            try:
                listing = _parse_detail(session, detail_url)
                if listing is not None:
                    results.append(listing)
                    print(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
                    log.info(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
            except Exception as e:
                log.warning(f"    [SKIP] {detail_url}: {e}", exc_info=True)

            if test_mode and len(results) >= 3:
                log.info(f"  [huurwoningen.be] Done (test) — {len(results)} listings.")
                print(f"  [huurwoningen.be] Done (test) — {len(results)} listings.")
                return results

        next_tag = soup.find("a", string=re.compile(r"volgende|next|›|»", re.I)) \
            or soup.find("a", rel="next")
        if not next_tag or not next_tag.get("href"):
            break
        next_url = urljoin(search_url, next_tag["href"])
        polite_delay()
        try:
            resp = session.get(next_url, timeout=15, allow_redirects=True)
            debug_log.log_request(log, next_url, resp.status_code, "")
            soup = BeautifulSoup(resp.text, "lxml")
            page += 1
        except Exception as e:
            log.warning(f"Next page failed: {e}")
            break

    log.info(f"  [huurwoningen.be] Done — {len(results)} available listings.")
    print(f"  [huurwoningen.be] Done — {len(results)} available listings.")
    return results
