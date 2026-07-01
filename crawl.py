"""
Leuven Kot Crawler
Usage:
  python crawl.py          # full crawl, all sites
  python crawl.py --test   # quick test — 3 listings per site only

A detailed debug log is written to debug.log after every run.
Paste its contents when asking for scraper fixes.
"""
from __future__ import annotations

import sys
from datetime import datetime

import debug_log
from excel_export import export_to_excel
from scrapers import huurwoningen, immoweb, kotwijs, tweedehands, zimmo

# kotwijs.be first: it is the official KU Leuven kot database and by far
# the richest source. The others add listings that are not on kotwijs.
SITES = [
    ("kotwijs.be",      kotwijs.scrape),
    ("2dehands.be",     tweedehands.scrape),
    ("huurwoningen.be", huurwoningen.scrape),
    ("immoweb.be",      immoweb.scrape),
    ("zimmo.be",        zimmo.scrape),
]


def deduplicate(listings: list) -> list:
    """Drop cross-site duplicates: same street address AND same price.

    Sites earlier in SITES win (kotwijs has the best data quality).
    Only addresses with a house number count — a bare city name like
    "Leuven" would wrongly merge different koten.
    """
    log = debug_log.get("main")
    seen: set[tuple[str, str]] = set()
    unique = []
    for l in listings:
        addr = "".join(l.address.lower().split())
        if addr and any(c.isdigit() for c in addr) and l.price_eur_month:
            key = (addr, l.price_eur_month)
            if key in seen:
                log.debug(f"Duplicate dropped: {l.source} {l.address} ({l.price_eur_month} €)")
                continue
            seen.add(key)
        unique.append(l)
    removed = len(listings) - len(unique)
    if removed:
        log.info(f"  Deduplication removed {removed} cross-site duplicates")
        print(f"  Removed {removed} cross-site duplicates")
    return unique


def main() -> None:
    test_mode = "--test" in sys.argv
    debug_log.setup(test_mode=test_mode)
    log = debug_log.get("main")

    print()
    print("=" * 54)
    print("  LEUVEN KOT CRAWLER")
    print(f"  Started : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if test_mode:
        print("  Mode    : TEST (max 3 listings per site)")
    else:
        print("  Mode    : FULL")
    print("=" * 54)

    all_listings = []

    for site_name, scrape_fn in SITES:
        print(f"\n{'─' * 54}")
        print(f"  {site_name}")
        print(f"{'─' * 54}")
        try:
            listings = scrape_fn(test_mode=test_mode)
            all_listings.extend(listings)
            log.info(f"  {site_name}: {len(listings)} listings collected")
        except Exception as e:
            log.error(f"  [ERROR] {site_name} crashed: {e}", exc_info=True)
            print(f"  [ERROR] {site_name} crashed: {e}")

    all_listings = deduplicate(all_listings)

    print()
    print("=" * 54)
    print(f"  Total available koten found: {len(all_listings)}")
    print("=" * 54)

    log.info(f"  Total: {len(all_listings)} listings")

    if not all_listings:
        print("\n  No listings found.")
        print("  Possible reasons:")
        print("    - Sites changed their HTML/API (check debug.log)")
        print("    - Network/firewall blocking requests")
        print("    - All koten are taken right now (try again tomorrow)")
        print(f"\n  Full details written to: debug.log")
        log.info("Run finished with 0 results — share debug.log for diagnosis.")
        return

    filename = f"koten_leuven_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    print(f"\n  Writing Excel file: {filename}")
    export_to_excel(all_listings, filename)

    print("\n  Done! Open the Excel file and click links to view listings.")
    print(f"  Debug log: debug.log")
    log.info(f"Excel written: {filename}")


if __name__ == "__main__":
    main()
