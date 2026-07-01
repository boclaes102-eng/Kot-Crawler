"""
Scraper for huurwoningen.be — rental rooms in Leuven.
The site uses HTTP (not HTTPS) and a no-www redirect chain.
Detail URLs follow the pattern: /huurwoning/{slug}
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import debug_log
from models import KotListing
from scrapers.base import extract_number, get_text, is_unavailable, make_session, polite_delay

BASE = "https://huurwoningen.be"

# Confirmed URL pattern from homepage nav: /[type]-huren/[city] and /huurwoningen/[city]
SEARCH_CANDIDATES = [
    "https://huurwoningen.be/studentenwoning-huren/leuven",
    "https://huurwoningen.be/kamer-huren/leuven",
    "https://huurwoningen.be/appartement-huren/leuven",
    "https://huurwoningen.be/studio-huren/leuven",
    "https://huurwoningen.be/huurwoningen/leuven",
    "https://huurwoningen.be/huurwoningen/leuven/kamer",
]

log = debug_log.get("huurwoningen")


def _yes_no(text: str, keywords: list[str]) -> str:
    return "Ja" if any(k in text.lower() for k in keywords) else "Nee"


def _find_cards(soup) -> list:
    return (
        soup.find_all("article", class_=re.compile(r"listing|property|result|huurwoning|card", re.I))
        or soup.find_all("div",  class_=re.compile(r"listing-card|property-card|huurwoning|result-item", re.I))
        or soup.find_all("li",   class_=re.compile(r"listing|property|result|huurwoning", re.I))
        or [a.parent for a in soup.find_all("a", href=re.compile(r"/huurwoning/"))]
    )


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
            links = [a.get("href", "") for a in soup.find_all("a", href=True)]
            log.debug(f"Page links: {links[:40]}")

        except Exception as e:
            log.warning(f"  [huurwoningen.be] {url} error: {e}")

    return None, None


def _parse_detail(session, url: str) -> KotListing | None:
    polite_delay()
    try:
        resp = session.get(url, timeout=15, allow_redirects=True)
        debug_log.log_request(log, url, resp.status_code, resp.headers.get("content-type", ""))
        resp.raise_for_status()
    except Exception as e:
        log.warning(f"Detail failed {url}: {e}")
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    full_text = soup.get_text(" ")

    if is_unavailable(full_text[:600]):
        return None

    # Only keep Leuven listings
    if "leuven" not in full_text.lower() and "3000" not in full_text:
        return None

    listing = KotListing(source="huurwoningen.be", url=url,
                         scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"))

    listing.title = get_text(soup.find("h1") or soup.find("h2"))

    addr_tag = (
        soup.find(class_=re.compile(r"adres|address|locatie|location|straat", re.I))
        or soup.find("span", class_=re.compile(r"adres|address", re.I))
    )
    if addr_tag:
        listing.address = get_text(addr_tag)
        parts = listing.address.replace("\n", ",").split(",")
        if len(parts) >= 2:
            listing.neighborhood = parts[-1].strip()
    debug_log.log_field(log, "address", str(addr_tag)[:80] if addr_tag else "None", listing.address)

    price_tag = soup.find(class_=re.compile(r"prijs|price|huur", re.I))
    if price_tag:
        listing.price_eur_month = extract_number(price_tag.get_text())
    else:
        m = re.search(r"€\s*([\d.,]+)", full_text)
        if m:
            listing.price_eur_month = m.group(1).replace(",", ".")
    debug_log.log_field(log, "price", str(price_tag)[:80] if price_tag else "None", listing.price_eur_month)

    m2 = re.search(r"(\d+)\s*m[²2]", full_text, re.I)
    if m2:
        listing.size_m2 = m2.group(1)

    low = full_text.lower()
    if "studio" in low:
        listing.listing_type = "Studio"
    elif "appartement" in low:
        listing.listing_type = "Appartement"
    elif "kamer" in low or "room" in low:
        listing.listing_type = "Kamer"

    listing.furnished          = _yes_no(low, ["gemeubeld", "furnished", "meubels"])
    listing.private_bathroom   = _yes_no(low, ["eigen badkamer", "eigen douche"])
    listing.shared_bathroom    = _yes_no(low, ["gedeelde badkamer", "shared bathroom"])
    listing.private_kitchen    = _yes_no(low, ["eigen keuken", "kitchenette"])
    listing.shared_kitchen     = _yes_no(low, ["gedeelde keuken", "shared kitchen"])
    listing.internet_included  = _yes_no(low, ["internet", "wifi"])
    listing.utilities_included = _yes_no(low, ["kosten inbegrepen", "all-in", "verwarming inbegrepen"])
    listing.washing_machine    = _yes_no(low, ["wasmachine"])
    listing.elevator           = _yes_no(low, ["lift"])
    listing.pets_allowed       = _yes_no(low, ["huisdieren"])

    avail = re.search(r"beschikbaar\s+vanaf\s*[:\-]?\s*([\w\s\d/\-\.]+)", full_text, re.I)
    if avail:
        listing.available_from = avail.group(1).strip()[:30]

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

    while True:
        cards = _find_cards(soup)
        log.info(f"  [huurwoningen.be] Page {page}: {len(cards)} cards")
        print(f"  [huurwoningen.be] Page {page}: {len(cards)} cards")

        seen_urls: set[str] = set()
        for card in cards:
            link_tag = card.find("a", href=True) if hasattr(card, "find") else None
            if not link_tag:
                continue
            href = link_tag["href"]
            if "/huurwoning/" not in href:
                continue
            detail_url = urljoin(BASE, href)
            if detail_url in seen_urls:
                continue
            seen_urls.add(detail_url)

            if is_unavailable(card.get_text() if hasattr(card, "get_text") else ""):
                continue

            try:
                listing = _parse_detail(session, detail_url)
                if listing is not None:
                    results.append(listing)
                    print(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
                    log.info(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
            except Exception as e:
                log.warning(f"    [SKIP] {detail_url}: {e}")

            if test_mode and len(results) >= 3:
                return results

        next_tag = soup.find("a", string=re.compile(r"volgende|next|›|»", re.I))
        if not next_tag:
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
