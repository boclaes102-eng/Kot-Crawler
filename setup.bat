@echo off
setlocal EnableDelayedExpansion
title Kot Crawler - Setup

echo.
echo ============================================
echo   KOT CRAWLER - Setup
echo ============================================
echo.

:: ── Check Python ────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python was not found on your system.
    echo.
    echo   Please install Python 3.10 or newer from:
    echo   https://www.python.org/downloads/
    echo.
    echo   IMPORTANT: During installation, tick the box:
    echo   "Add Python to PATH"
    echo.
    pause
    exit /b 1
)

for /f "tokens=2" %%V in ('python --version 2^>^&1') do set PYVER=%%V
echo [OK] Python %PYVER% found

:: ── Check pip ───────────────────────────────
pip --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] pip is not available. Reinstall Python and make sure pip is included.
    pause
    exit /b 1
)
echo [OK] pip found

:: ── Install Python packages ─────────────────
echo.
echo Installing Python packages (requests, beautifulsoup4, playwright, openpyxl, ...)
echo This may take a minute...
echo.
pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [ERROR] Package installation failed. Check the error above.
    pause
    exit /b 1
)
echo.
echo [OK] All Python packages installed

:: ── Install Playwright browser ──────────────
echo.
echo Installing Playwright browser (Chromium, needed for Immoweb)...
echo This downloads ~150 MB the first time.
echo.
playwright install chromium
if errorlevel 1 (
    echo.
    echo [WARNING] Playwright browser install failed.
    echo          The Immoweb scraper will be skipped.
    echo          You can retry manually: playwright install chromium
) else (
    echo [OK] Playwright Chromium installed
)

echo.
echo ============================================
echo   Setup complete!
echo.
echo   To crawl, run:   python crawl.py
echo   To quick-test:   python crawl.py --test
echo ============================================
echo.
pause
