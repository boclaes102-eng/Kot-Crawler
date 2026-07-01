from __future__ import annotations

import random
import time

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Dutch/French words that signal a listing is NOT available
UNAVAILABLE_KEYWORDS = [
    "verhuurd", "bezet", "niet beschikbaar", "al verhuurd",
    "loué", "occupé", "pas disponible", "taken", "not available",
    "optie", "option",  # "under option" = probably going to be taken
]


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    return s


def polite_delay(min_s: float = 1.5, max_s: float = 3.0) -> None:
    time.sleep(random.uniform(min_s, max_s))


def is_unavailable(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in UNAVAILABLE_KEYWORDS)


def get_text(tag, default: str = "missing") -> str:
    if tag is None:
        return default
    t = tag.get_text(separator=" ", strip=True)
    return t if t else default


def extract_number(text: str) -> str:
    """Pull first number (with optional decimal) from a string."""
    import re
    m = re.search(r"[\d]+(?:[.,]\d+)?", text.replace("\xa0", ""))
    return m.group(0).replace(",", ".") if m else "missing"
