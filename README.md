# Leuven Kot Crawler

Finds available student rooms (koten) in Leuven across multiple sites and
writes them to a styled Excel file with clickable links.

## Usage

```
setup.bat               (first time only — installs dependencies)
python crawl.py         full crawl of all sites
python crawl.py --test  quick test, max 3 listings per site
```

Every run writes a detailed trace to `debug.log`. If a site returns 0
results, that file shows exactly which requests were made and what came
back — paste it when asking for scraper fixes.

## Sources

| Site | How it's scraped | Notes |
|---|---|---|
| **kotwijs.be** | JSON API (no browser) | The official KU Leuven kot database — the primary source, typically 400+ listings with reliable structured data (price, size, private/shared bathroom & kitchen, internet, utilities, furnished, availability). Automatically filtered to the **coming academic year** (e.g. 2026–2027), so units that are only free during the current/past year — i.e. already taken for next year — are excluded. |
| **2dehands.be** | Static HTML + embedded JSON-LD | Price/title/availability come from schema.org data on the detail page. |
| **huurwoningen.be** | Static HTML | Blocks datacenter IPs; works from a normal home connection. |
| **immoweb.be** | Playwright (Chromium) reading the site's JSON search API | Filters to Leuven postcodes and kot-sized listings. |
| **zimmo.be** | Playwright (Chromium) | Best effort — heavily JS-rendered with bot detection. |

Cross-site duplicates (same street address + same price) are removed;
kotwijs wins because its data quality is best.

## Excel columns

Link (clickable), Source, Title, Address, Neighborhood, Price (€/month),
Size (m²), Price/m² (computed), Type (Kamer/Studio/Appartement),
Furnished, Private bathroom, Shared bathroom, Private kitchen,
Shared kitchen, Internet included, Utilities included, Washing machine,
Elevator, Pets allowed, Available from, Scraped on.

**A cell is left empty when the site doesn't state the fact.** Free-text
sites can confirm a feature ("Yes") but can never prove its absence, so
only kotwijs and immoweb — which have structured data — produce "No"
values.

## Data quality rules

- Availability filtering matches whole words only: "verhuurd" (rented)
  no longer false-positives on "verhuurder" (landlord) in the site nav.
- Belgian number formats are handled: "1.250" is 1250 euro, "650,50"
  is 650.5.
- Nothing is guessed: a keyword scan of the description can only ever
  set "Yes", never "No".

## Environment overrides (for running in containers/CI)

- `KOT_CHROMIUM` — path to a Chromium executable for Playwright.
- `KOT_PW_PROXY` — proxy server URL for browser traffic.
