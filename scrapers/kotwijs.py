"""
Scraper for kotwijs.be — the official KU Leuven kot database for Leuven.

This is the best source we have: it is THE central database that the
university and the city run for student rooms, and it exposes a clean
JSON API (the same one the React frontend uses):

  POST https://main-api.kotwijs.be/api/tenants/search/getresults
       body: {"sort": {...}, "filter": {...}, "paging": {"page": N, "size": M}}
  GET  https://main-api.kotwijs.be/api/tenants/listingdetail/getdetail?id=<id>

No browser needed, no HTML parsing, no bot detection observed.
Public listing URL: https://www.kotwijs.be/link/listing/<guid>

Enum mappings below were extracted from the site's JS bundle
(assets/index-*.js), verified against live API responses on 2026-07-01.
"""
from __future__ import annotations

import re
from datetime import datetime

import debug_log
from models import KotListing
from scrapers.base import make_session, polite_delay, yes_if_mentioned, yes_no

API = "https://main-api.kotwijs.be/api"
SITE = "https://www.kotwijs.be"
PAGE_SIZE = 100

# Centre of Leuven — the API sorts by distance from this point.
LEUVEN_LAT, LEUVEN_LON = 50.8760, 4.7016

# unitType enum (bundle: e[e.Room=1] ... e[e.House=5])
UNIT_TYPES = {1: "Kamer", 2: "Studio", 3: "Appartement", 4: "Residentie", 5: "Huis"}

# facilities.options enum (bundle: e[e.CommonArea=1] ... e[e.Furnished=12])
OPT_PRIVATE_TOILET = 4
OPT_PRIVATE_BATHROOM = 5
OPT_PRIVATE_KITCHEN = 6
OPT_INTERNET_WIFI = 10
OPT_INTERNET_CABLE = 11
OPT_FURNISHED = 12

# pricing cost enum (bundle: e[e.Included=1] ... e[e.NotIncluded=6])
#   1 = included in rent, 2/3 = included in the fixed monthly costs,
#   4/5 = advance + settlement (you pay actual usage), 6 = not included
COST_COVERED = {1, 2, 3}
COST_NOT_COVERED = {4, 5, 6}

log = debug_log.get("kotwijs")


def _post(session, path: str, body: dict) -> dict | None:
    url = f"{API}/{path}"
    try:
        resp = session.post(url, json=body, timeout=25,
                            headers={"Origin": SITE, "Referer": SITE + "/"})
        debug_log.log_request(log, url, resp.status_code,
                              resp.headers.get("content-type", ""))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"POST {path} failed: {e}")
        return None


def _get(session, path: str) -> dict | None:
    url = f"{API}/{path}"
    try:
        resp = session.get(url, timeout=25,
                           headers={"Origin": SITE, "Referer": SITE + "/"})
        debug_log.log_request(log, url, resp.status_code,
                              resp.headers.get("content-type", ""))
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.error(f"GET {path} failed: {e}")
        return None


def _search_page(session, page: int) -> tuple[list[dict], int]:
    body = {
        "sort": {"latitude": LEUVEN_LAT, "longitude": LEUVEN_LON,
                 "direction": 1, "type": 1},  # 1 = ascending by distance
        "filter": {"freeUnitsInBuilding": None, "types": [],
                   "facilities": [], "availabilityId": None},
        "paging": {"page": page, "size": PAGE_SIZE},
    }
    data = _post(session, "tenants/search/getresults", body)
    if not data or "data" not in data:
        return [], 0
    d = data["data"]
    return d.get("items") or [], int(d.get("totalCount") or 0)


def _cost_covered(enum_val) -> str:
    if enum_val is None:
        return ""
    return "Yes" if enum_val in COST_COVERED else "No"


def _strip_html(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html or "")


def _range_str(rng: dict | None) -> str:
    """Kotwijs gives {from: X, to: Y|null}; use the 'from' value."""
    if not rng:
        return ""
    val = rng.get("from")
    if val is None:
        return ""
    return f"{float(val):g}"


def _parse_detail(item: dict, detail: dict) -> KotListing:
    listing = KotListing(source="kotwijs.be",
                         scraped_on=datetime.now().strftime("%Y-%m-%d %H:%M"))

    guid = detail.get("guid") or ""
    listing.url = f"{SITE}/link/listing/{guid}" if guid else SITE

    loc = detail.get("location") or {}
    street = f"{loc.get('street') or ''} {loc.get('number') or ''}".strip()
    city = loc.get("city") or ""
    listing.address = ", ".join(p for p in (street, city) if p)
    listing.neighborhood = city
    debug_log.log_field(log, "address", str(loc)[:100], listing.address)

    listing.listing_type = UNIT_TYPES.get(detail.get("unitType"), "")

    unit_no = detail.get("unitNumber") or ""
    listing.title = " ".join(p for p in (listing.listing_type, listing.address) if p)
    if unit_no:
        listing.title += f" (unit {unit_no})"

    surface = detail.get("surfaceAreaFrom")
    listing.size_m2 = f"{float(surface):g}" if surface else ""

    pricing = detail.get("pricing") or {}
    listing.price_eur_month = _range_str(pricing.get("totalMonthlyRent")) \
        or _range_str(pricing.get("monthlyRent"))
    debug_log.log_field(log, "price", str(pricing.get("totalMonthlyRent"))[:60],
                        listing.price_eur_month)

    facilities = detail.get("facilities") or {}
    options = set(facilities.get("options") or [])

    listing.furnished = yes_no(OPT_FURNISHED in options)
    listing.private_bathroom = yes_no(OPT_PRIVATE_BATHROOM in options
                                      or bool(item.get("hasPrivateBathroom")))
    listing.private_kitchen = yes_no(OPT_PRIVATE_KITCHEN in options
                                     or bool(item.get("hasPrivateKitchen")))

    common_bath = facilities.get("commonBathrooms")
    listing.shared_bathroom = yes_no(common_bath > 0) if common_bath is not None else ""
    common_kitchen = facilities.get("commonKitchens")
    listing.shared_kitchen = yes_no(common_kitchen > 0) if common_kitchen is not None else ""

    # Internet: the pricing enum says whether it is in the price; the
    # facility options say whether it exists at all.
    inet_cost = pricing.get("internet")
    if inet_cost is not None:
        listing.internet_included = _cost_covered(inet_cost)
    elif options & {OPT_INTERNET_WIFI, OPT_INTERNET_CABLE}:
        listing.internet_included = "Yes"

    # Utilities: water + electricity + heating cost enums.
    util_vals = [pricing.get("water"), pricing.get("electricity"), pricing.get("heating")]
    known = [v for v in util_vals if v is not None]
    if known:
        listing.utilities_included = "Yes" if all(v in COST_COVERED for v in known) else "No"

    avail = detail.get("availability") or {}
    frm = avail.get("from") or item.get("availableFrom") or ""
    listing.available_from = str(frm)[:10]

    # Washing machine / elevator / pets are not structured fields on
    # kotwijs — scan the free-text description; empty means unknown.
    desc_html = ""
    for block in (detail.get("descriptions") or {}).get("items") or []:
        desc_html += (block.get("property") or "") + " " + (block.get("unit") or "")
    desc = _strip_html(desc_html).lower()
    listing.washing_machine = yes_if_mentioned(desc, ["wasmachine", "washing machine", "wasserette"])
    listing.elevator = yes_if_mentioned(desc, ["lift ", "lift,", "lift.", "elevator"])
    listing.pets_allowed = yes_if_mentioned(desc, ["huisdieren toegelaten", "huisdieren welkom",
                                                   "pets allowed"])

    listing.compute_price_per_m2()
    return listing


def scrape(test_mode: bool = False) -> list[KotListing]:
    session = make_session()
    results: list[KotListing] = []

    log.info("  [kotwijs.be] Querying official KU Leuven kot database...")
    print("  [kotwijs.be] Querying official KU Leuven kot database...")

    page = 1
    total = None
    while True:
        items, total_count = _search_page(session, page)
        if total is None and total_count:
            total = total_count
            log.info(f"  [kotwijs.be] {total} listings in database")
            print(f"  [kotwijs.be] {total} listings in database")
        if not items:
            if page == 1:
                log.warning("  [kotwijs.be] Search returned no items — check debug.log")
                print("  [kotwijs.be] Search returned no items — check debug.log")
            break

        log.info(f"  [kotwijs.be] Page {page}: {len(items)} items")
        for item in items:
            lid = item.get("id")
            if lid is None:
                continue
            polite_delay(0.4, 0.9)  # lightweight JSON API — short delay is polite enough
            data = _get(session, f"tenants/listingdetail/getdetail?id={lid}")
            detail = (data or {}).get("data")
            if not detail:
                log.warning(f"    [SKIP] detail {lid}: no data")
                continue
            try:
                listing = _parse_detail(item, detail)
            except Exception as e:
                log.warning(f"    [SKIP] detail {lid}: parse error {e}", exc_info=True)
                continue
            results.append(listing)
            print(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")
            log.info(f"    + {listing.title[:60]}  ({listing.price_eur_month} €)")

            if test_mode and len(results) >= 3:
                log.info(f"  [kotwijs.be] Done (test) — {len(results)} listings.")
                print(f"  [kotwijs.be] Done (test) — {len(results)} listings.")
                return results

        if len(items) < PAGE_SIZE:
            break
        page += 1
        polite_delay(0.8, 1.5)

    log.info(f"  [kotwijs.be] Done — {len(results)} listings.")
    print(f"  [kotwijs.be] Done — {len(results)} listings.")
    return results
