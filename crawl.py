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
from scrapers import studentenkot, kotweb, kotnet, immoweb

SITES = [
    ("2dehands.be",       studentenkot.scrape),
    ("zimmo.be",          kotweb.scrape),
    ("huurwoningen.be",   kotnet.scrape),
    ("immoweb.be",        immoweb.scrape),
]


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

    print()
    print("=" * 54)
    print(f"  Total available koten found: {len(all_listings)}")
    print("=" * 54)

    log.info(f"  Total: {len(all_listings)} listings")

    if not all_listings:
        print("\n  No listings found.")
        print("  Possible reasons:")
        print("    - Sites changed their HTML (update CSS selectors)")
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
