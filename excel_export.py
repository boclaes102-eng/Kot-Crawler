from __future__ import annotations

from datetime import datetime
from typing import List

import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

from models import KotListing

HEADERS = [
    ("Link",               28),
    ("Bron",               14),
    ("Titel",              30),
    ("Adres",              28),
    ("Wijk",               18),
    ("Prijs (€/maand)",    16),
    ("Opp. (m²)",          12),
    ("€/m²",               10),
    ("Type",               14),
    ("Gemeubeld",          12),
    ("Eigen badkamer",     14),
    ("Gedeelde badkamer",  16),
    ("Eigen keuken",       13),
    ("Gedeelde keuken",    15),
    ("Internet incl.",     14),
    ("Kosten incl.",       13),
    ("Wasmachine",         13),
    ("Lift",               10),
    ("Huisdieren OK",      14),
    ("Beschikbaar vanaf",  18),
    ("Gecrawled op",       18),
]

HEADER_FILL   = PatternFill("solid", fgColor="1F497D")
ROW_FILL_ODD  = PatternFill("solid", fgColor="FFFFFF")
ROW_FILL_EVEN = PatternFill("solid", fgColor="DCE6F1")

THIN = Border(
    left=Side(style="thin", color="B8CCE4"),
    right=Side(style="thin", color="B8CCE4"),
    top=Side(style="thin", color="B8CCE4"),
    bottom=Side(style="thin", color="B8CCE4"),
)


def _listing_to_row(listing: KotListing) -> list:
    return [
        listing.url,
        listing.source,
        listing.title,
        listing.address,
        listing.neighborhood,
        listing.price_eur_month,
        listing.size_m2,
        listing.price_per_m2,
        listing.listing_type,
        listing.furnished,
        listing.private_bathroom,
        listing.shared_bathroom,
        listing.private_kitchen,
        listing.shared_kitchen,
        listing.internet_included,
        listing.utilities_included,
        listing.washing_machine,
        listing.elevator,
        listing.pets_allowed,
        listing.available_from,
        listing.scraped_on,
    ]


def export_to_excel(listings: List[KotListing], filepath: str) -> None:
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Koten Leuven"
    ws.freeze_panes = "A2"

    # ── Header row ──────────────────────────────────────────────────────
    header_font = Font(bold=True, color="FFFFFF", size=11)
    for col_idx, (label, width) in enumerate(HEADERS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.font = header_font
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
        cell.border = THIN
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.row_dimensions[1].height = 20

    # ── Data rows ────────────────────────────────────────────────────────
    link_font_blue   = Font(color="0563C1", underline="single", size=10)
    normal_font      = Font(size=10)
    missing_font     = Font(color="A6A6A6", italic=True, size=10)

    for row_idx, listing in enumerate(listings, start=2):
        fill = ROW_FILL_ODD if row_idx % 2 == 1 else ROW_FILL_EVEN
        row_data = _listing_to_row(listing)

        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.fill = fill
            cell.border = THIN
            cell.alignment = Alignment(vertical="center", wrap_text=False)

            if col_idx == 1 and value and value != "missing":
                # Clickable hyperlink in the first column
                cell.hyperlink = value
                cell.value = "Open listing"
                cell.font = link_font_blue
            elif value == "missing":
                cell.font = missing_font
            else:
                cell.font = normal_font

        ws.row_dimensions[row_idx].height = 16

    # ── Summary sheet ────────────────────────────────────────────────────
    ws_summary = wb.create_sheet("Samenvatting")
    ws_summary["A1"] = "Gecrawled op"
    ws_summary["B1"] = datetime.now().strftime("%Y-%m-%d %H:%M")
    ws_summary["A2"] = "Aantal koten gevonden"
    ws_summary["B2"] = len(listings)

    sources: dict[str, int] = {}
    for l in listings:
        sources[l.source] = sources.get(l.source, 0) + 1
    ws_summary["A4"] = "Per website"
    for i, (src, count) in enumerate(sources.items(), start=5):
        ws_summary.cell(row=i, column=1, value=src)
        ws_summary.cell(row=i, column=2, value=count)

    wb.save(filepath)
    print(f"\n  Saved: {filepath}")
    print(f"  Listings: {len(listings)}")
    for src, count in sources.items():
        print(f"    {src}: {count}")
