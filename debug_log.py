"""
Debug logger — writes a detailed run log to debug.log every time crawl.py runs.
Paste the content of debug.log here to get scrapers fixed.
"""
from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path

LOG_FILE = Path("debug.log")
MAX_LOG_SIZE_BYTES = 2 * 1024 * 1024  # rotate after 2 MB

_file_handler: logging.FileHandler | None = None


class _ConsoleFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return record.getMessage()


class _FileFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%H:%M:%S")
        return f"{ts} [{record.levelname:<7}] {record.name}: {record.getMessage()}"


def setup(test_mode: bool = False) -> None:
    """Call once at startup from crawl.py."""
    # Rotate if log is too big
    if LOG_FILE.exists() and LOG_FILE.stat().st_size > MAX_LOG_SIZE_BYTES:
        LOG_FILE.rename(LOG_FILE.with_suffix(".log.old"))

    global _file_handler

    root = logging.getLogger("kot")
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    # File: full detail (DEBUG)
    _file_handler = logging.FileHandler(LOG_FILE, encoding="utf-8", mode="a")
    _file_handler.setLevel(logging.DEBUG)
    _file_handler.setFormatter(_FileFormatter())
    root.addHandler(_file_handler)

    # Console: only INFO and above (clean output)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(_ConsoleFormatter())
    root.addHandler(ch)

    # Write run header directly so it stands out
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write("\n")
        f.write("=" * 70 + "\n")
        f.write(f"  RUN  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}   "
                f"mode={'TEST' if test_mode else 'FULL'}\n")
        f.write("=" * 70 + "\n")


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"kot.{name}")


# ── Convenience helpers used by scrapers ─────────────────────────────────────

def log_request(logger: logging.Logger, url: str, status: int, content_type: str = "") -> None:
    logger.debug(f"GET {status}  {url}  [{content_type}]")


def log_html_sample(logger: logging.Logger, url: str, html: str, chars: int = 3000) -> None:
    """Log a chunk of raw HTML — essential for diagnosing selector mismatches."""
    logger.debug(
        f"HTML SAMPLE ({len(html)} chars total) from {url}\n"
        f"{'─' * 60}\n"
        f"{html[:chars]}\n"
        f"{'─' * 60}"
    )


def log_cards_found(logger: logging.Logger, selector_tried: str, count: int) -> None:
    if count:
        logger.debug(f"Cards found via '{selector_tried}': {count}")
    else:
        logger.debug(f"Cards NOT found via '{selector_tried}'")


def log_field(logger: logging.Logger, field: str, raw: str, parsed: str) -> None:
    logger.debug(f"  field '{field}': raw={raw!r}  →  {parsed!r}")
