"""Shared helpers for all scrapers."""
from __future__ import annotations

import os
import random
import re
import time

import requests

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Phrases that signal a listing is NOT available.  Matched on word
# boundaries so "verhuurd" does not fire on "verhuurder" (= landlord)
# and "optie" does not fire on "cookie-opties".
_UNAVAILABLE_PATTERNS = [
    r"\bverhuurd\b",
    r"\bal verhuurd\b",
    r"\bbezet\b",
    r"\bniet (?:meer )?beschikbaar\b",
    r"\blou[ée]\b",
    r"\bpas disponible\b",
    r"\bnot available\b",
    r"\bonder optie\b",
    r"\bin optie\b",
]
_UNAVAILABLE_RE = re.compile("|".join(_UNAVAILABLE_PATTERNS), re.IGNORECASE)


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def polite_delay(min_s: float = 1.5, max_s: float = 3.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def is_unavailable(text: str) -> bool:
    """True when the text explicitly says the listing is taken."""
    return bool(_UNAVAILABLE_RE.search(text))


def get_text(tag, default: str = "") -> str:
    if tag is None:
        return default
    t = tag.get_text(separator=" ", strip=True)
    return t if t else default


def parse_euro_amount(text: str) -> str:
    """Pull a money/size amount out of text and normalise it.

    Handles the Belgian formats "1.250" (thousands dot), "650,50"
    (decimal comma) and plain "599".  Returns "" when no number found.
    """
    if not text:
        return ""
    text = text.replace("\xa0", " ")
    m = re.search(r"\d{1,3}(?:\.\d{3})+(?:,\d+)?|\d+(?:,\d+)?", text)
    if not m:
        return ""
    raw = m.group(0)
    # "1.250" / "1.250,50" -> dot is a thousands separator
    raw = raw.replace(".", "").replace(",", ".")
    try:
        val = float(raw)
    except ValueError:
        return ""
    return f"{val:.2f}".rstrip("0").rstrip(".")


def yes_if_mentioned(text_lower: str, keywords: list[str]) -> str:
    """'Yes' when a keyword occurs in the text, otherwise '' (unknown).

    Free-text listings can only confirm that something exists — the
    absence of a word never proves 'No', so we leave the field empty.
    """
    return "Yes" if any(k in text_lower for k in keywords) else ""


def yes_no(condition: bool | None) -> str:
    """Map a definite boolean to Yes/No; None means unknown -> ''."""
    if condition is None:
        return ""
    return "Yes" if condition else "No"


def launch_browser(pw):
    """Launch Chromium for Playwright scrapers.

    Two optional environment variables make the scrapers runnable in
    unusual environments (CI containers, proxied networks):
      KOT_CHROMIUM  - path to a chromium executable
      KOT_PW_PROXY  - proxy server URL to route browser traffic through
    On a normal machine neither is set and the Playwright-managed
    browser is used directly.
    """
    kwargs = {"headless": True}
    exe = os.environ.get("KOT_CHROMIUM")
    if exe:
        kwargs["executable_path"] = exe
    proxy = os.environ.get("KOT_PW_PROXY")
    if proxy:
        kwargs["proxy"] = {"server": proxy}
        kwargs["args"] = ["--no-sandbox"]
    return pw.chromium.launch(**kwargs)


def new_browser_context(browser):
    return browser.new_context(
        user_agent=HEADERS["User-Agent"],
        viewport={"width": 1280, "height": 900},
        locale="nl-BE",
    )
