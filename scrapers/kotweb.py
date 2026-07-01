"""
Scraper for zimmo.be — student rooms in Leuven.
WebFetch can't access it (bot detection at HTTP layer) but requests with
a full browser user-agent and referrer typically works fine.
"""
from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

import debug_log
from models import KotListing
from scrapers.base import extract_number, get_text, is_unavailable, make_session, polite_delay

BASE = "https://www.zimmo.be"

# Zimmo listings are JS-rendered — requests only gets a shell.
# We use Playwright here (same as immoweb) and parse the page after JS runs.
# The studentenkamer/ URL redirects to te-huur/ on the server, but Playwright
# waits for the JS to populate the actual listing cards.
SEARCH_CANDIDATES = [
    "https://www.zimmo.be/nl/leuven/te-huur/studentenkamer/",
    "https://www.zimmo.be/nl/leuven/te-huur/studio/",
    "https://www.zimmo.be/nl/leuven/te-huur/",
]

log = debug_log.get("zimmo")


def _yes_no(text: str, keywords: list[str]) -> str:
    return "Ja" if any(k in text.lower() for k in keywords) else "Nee"


def _find_cards(soup) -> list:
    return (
        soup.find_all("article", class_=re.compile(r"property|listing|result|item|card", re.I))
        or soup.find_all("li",   class_=re.compile(r"property|listing|result|item", re.I))
        or soup.find_all("div",  class_=re.compile(r"property-card|listing-card|result-item", re.I))
        or soup.find_all("div",  attrs={"data-property-id": True})
        or soup.find_all("div",  attrs={"data-id": True})
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
            debug_log.log_cards_found(log, "article/li/div with property|listing|card", len(cards))

            if cards:
                log.info(f"  [zimmo.be] Working URL: {resp.url}  ({len(cards)} cards)")
                return resp.url, soup

            log.debug(f"No cards at {resp.url}")
            debug_log.log_html_sample(log, resp.url, resp.text, chars=3000)
            links = [a.get("href", "") for a in soup.find_all("a", href=True)]
            log.debug(f"Page links: {links[:40]}")

        except Exception as e:
            log.warning(f"  [zimmo.be] {url} error: {e}")

    return None, None


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

    if is_unavailable(full_text[:600]):
        return None

    listing = KotListing(source="zimmo.be", url=url,
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
    elif "kamer" in low or "studentenkamer" in low:
        listing.listing_type = "Kamer"

    listing.furnished          = _yes_no(low, ["gemeubeld", "furnished", "meubels"])
    listing.private_bathroom   = _yes_no(low, ["eigen badkamer", "eigen douche"])
    listing.shared_bathroom    = _yes_no(low, ["gedeelde badkamer", "shared bathroom"])
    listing.private_kitchen    = _yes_no(low, ["eigen keuken", "kitchenette"])
    listing.shared_kitchen     = _yes_no(low, ["gedeelde keuken", "shared kitchen"])
    listing.internet_included  = _yes_no(low, ["internet", "wifi"])
    listing.utilities_included = _yes_no(low, ["kosten inbegrepen", "all-in", "verwarming inbegrepen"])
    listing.washing_machine    = _yes_no(low, ["wasmachine"])
    listing.elevator           = _yes_no(low, ["lift", "elevator"])
    listing.pets_allowed       = _yes_no(low, ["huisdieren", "pets allowed"])

    avail = re.search(r"beschikbaar\s+vanaf\s*[:\-]?\s*([\w\s\d/\-\.]+)", full_text, re.I)
    if avail:
        listing.available_from = avail.group(1).strip()[:30]

    listing.compute_price_per_m2()
    return listing


def _scrape_with_playwright(test_mode: bool) -> list[KotListing]:
    from playwright.sync_api import sync_playwright
    session = make_session()
    results: list[KotListing] = []

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
            cards = _find_cards(soup)
            debug_log.log_cards_found(log, "zimmo cards", len(cards))

            if not cards:
                log.debug(f"No cards at {search_url}")
                debug_log.log_html_sample(log, search_url, html, chars=2000)
                all_links = [a.get("href", "") for a in soup.find_all("a", href=True)]
                log.debug(f"Page links: {all_links[:40]}")
                continue

            log.info(f"  [zimmo.be] Found {len(cards)} cards at {search_url}")
            print(f"  [zimmo.be] Found {len(cards)} cards")

            for card in cards:
                if is_unavailable(card.get_text()):
                    continue
                link_tag = card.find("a", href=True)
                if not link_tag:
                    continue
                detail_url = urljoin(BASE, link_tag["href"])
                # Skip promo/info pages (not property listings)
                if "/pagina/" in detail_url or "/verkopen/" in detail_url:
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
                    browser.close()
                    return results

            # Pagination via next button
            next_btn = page.query_selector("a[rel='next'], a.pagination-next, a[aria-label='Next']")
            if not next_btn:
                break
            try:
                next_btn.click()
                page.wait_for_load_state("networkidle", timeout=15_000)
                page.wait_for_timeout(1500)
            except Exception:
                break

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
        return []
    except Exception as e:
        log.error(f"  [zimmo.be] Error: {e}", exc_info=True)
        return []
