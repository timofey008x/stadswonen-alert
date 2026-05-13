"""
Stadswonen Rotterdam aanbod-scraper — v2

Verbeteringen t.o.v. v1:
- Leest tekst direct van de <a>-tag (niet de container)
- Paginering: klikt door alle pagina's
- Wijk-check op zowel adres als wijknaam (Kralingen-check werkt nu ook
  als Stadswonen de wijk 'De Snor' noemt maar het adres 'Kralingse...' bevat)
- Centrum-check: matcht 'stadscentrum', 'centrum', 'coolsingel' etc.
"""

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterable

from playwright.sync_api import sync_playwright

from notify import send_telegram

# ---------------------------------------------------------------- config
URL = "https://www.stadswonenrotterdam.nl/nl/aanbod"
SEEN_FILE = Path(__file__).parent / "seen.json"
DEBUG_HTML = Path(__file__).parent / "last_page.html"

MAX_PRICE_KAMER = float(os.environ.get("MAX_PRICE_KAMER", "500"))
MAX_PRICE_STUDIO = float(os.environ.get("MAX_PRICE_STUDIO", "600"))
GENDER = os.environ.get("GENDER", "man").lower()

# Zoektermen per wijk — matcht op adres + wijknaam samen (case-insensitive)
WIJK_TERMEN: list[list[str]] = [
    # Centrum / Stadscentrum / bekende centrumstraten
    ["stadscentrum", "centrum", "coolsingel", "blaak", "meent", "hoogstraat",
     "witte de with", "westblaak", "schiedamse vest", "eendrachtsplein"],
    # Kralingen — ook als Stadswonen een andere buurt-/complexnaam gebruikt
    ["kralingen", "kralingse", "kralingsche", "de snor", "kralingsveen",
     "boerengat", "kralingse zoom"],
]


# ---------------------------------------------------------------- helpers
def load_seen() -> set[str]:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()))
        except json.JSONDecodeError:
            return set()
    return set()


def save_seen(seen: Iterable[str]) -> None:
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2))


def parse_price(text: str) -> float | None:
    m = re.search(r"€\s*([\d\.]+(?:,\d+)?)", text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def gender_ok(text: str) -> bool:
    t = text.lower()
    if "geslacht: vrouw" in t:
        return False
    return True


def wijk_ok(text: str) -> bool:
    t = text.lower()
    for termen in WIJK_TERMEN:
        if any(term in t for term in termen):
            return True
    return False


# ---------------------------------------------------------------- scrape
def fetch_listings() -> list[dict]:
    results: dict[str, dict] = {}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="nl-NL",
        )
        page = ctx.new_page()
        page.goto(URL, wait_until="networkidle", timeout=60_000)

        # Cookie-popup wegklikken (best effort)
        for sel in (
            "button:has-text('Accepteer')",
            "button:has-text('Akkoord')",
            "button:has-text('Alles toestaan')",
            "button:has-text('Toestemmen')",
        ):
            try:
                page.locator(sel).first.click(timeout=2_000)
                page.wait_for_timeout(500)
                break
            except Exception:
                pass

        pagina = 1
        while True:
            print(f"Scraping pagina {pagina}...")

            try:
                page.wait_for_selector("a[href*='/nl/aanbod/']", timeout=15_000)
            except Exception:
                DEBUG_HTML.write_text(page.content())
                print(
                    f"WAARSCHUWING: geen listings op pagina {pagina}. "
                    f"HTML opgeslagen als {DEBUG_HTML.name}",
                    file=sys.stderr,
                )
                break

            if pagina == 1:
                DEBUG_HTML.write_text(page.content())

            anchors = page.locator("a[href*='/nl/aanbod/']")
            n = anchors.count()
            for i in range(n):
                a = anchors.nth(i)
                href = a.get_attribute("href") or ""
                if href.rstrip("/").endswith("/aanbod"):
                    continue
                url = (
                    href
                    if href.startswith("http")
                    else f"https://www.stadswonenrotterdam.nl{href}"
                )
                if url in results:
                    continue
                try:
                    blob = (a.inner_text(timeout=2_000) or "").strip()
                except Exception:
                    blob = ""
                results[url] = {"url": url, "text": blob}

            # Volgende pagina knop — niet disabled
            volgende = page.locator(
                "button:has(span:has-text('Ga naar volgende pagina')):not([disabled])"
            )
            if volgende.count() == 0:
                print("Laatste pagina bereikt.")
                break

            volgende.first.click()
            page.wait_for_timeout(2_000)
            pagina += 1

            if pagina > 20:
                print("Paginalimiet bereikt (20).")
                break

        browser.close()

    return list(results.values())


# ---------------------------------------------------------------- match
def matches_filters(listing: dict) -> tuple[bool, str]:
    text = listing["text"]
    price = parse_price(text)

    if price is None:
        return False, "geen prijs gevonden"

    # Bepaal type op basis van eerste woord in de card-tekst
    is_studio = text.lower().lstrip().startswith("studio")
    limiet = MAX_PRICE_STUDIO if is_studio else MAX_PRICE_KAMER
    type_label = "studio" if is_studio else "kamer"

    if price > limiet:
        return False, f"prijs E{price:.0f} > max E{limiet:.0f} ({type_label})"
    if not wijk_ok(text):
        return False, "wijk niet in voorkeurslijst"
    if not gender_ok(text):
        return False, "geslacht: vrouw"
    return True, "match"


# ---------------------------------------------------------------- main
def main() -> int:
    listings = fetch_listings()
    print(f"Totaal listings gevonden: {len(listings)}")

    seen = load_seen()
    new_matches: list[dict] = []

    for lst in listings:
        ok, reason = matches_filters(lst)
        status = "MATCH" if ok else f"skip ({reason})"
        label = next(
            (ln.strip() for ln in lst["text"].splitlines() if ln.strip()),
            lst["url"],
        )
        print(f"  {status} | {label[:70]}")

        if not ok:
            continue
        if lst["url"] in seen:
            print(f"    -> al gezien, skip")
            continue
        new_matches.append(lst)
        seen.add(lst["url"])

    print(f"\nNieuwe matches: {len(new_matches)}")

    for m in new_matches:
        price = parse_price(m["text"])
        first_line = next(
            (ln.strip() for ln in m["text"].splitlines() if ln.strip()),
            "Nieuwe kamer",
        )
        msg = (
            f"Nieuwe kamer - E{price:.0f}/mnd\n"
            f"{first_line}\n\n{m['url']}"
        )
        try:
            send_telegram(msg)
            print(f"  Telegram verstuurd: {first_line[:50]}")
        except Exception as e:
            print(f"  Telegram-fout: {e}", file=sys.stderr)

    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
