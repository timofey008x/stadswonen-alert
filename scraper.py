"""
Stadswonen Rotterdam aanbod-scraper.

Laadt https://www.stadswonenrotterdam.nl/nl/aanbod met een headless browser,
wacht tot de listings zijn ingeladen, en filtert op:
- max prijs
- voorkeur-wijken
- geslachtsvoorkeur (advertentie moet 'man' of geen voorkeur zijn)

Nieuwe matches worden gemeld via notify.py.
Reeds gemelde advertenties staan in seen.json en worden overgeslagen.
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

MAX_PRICE = float(os.environ.get("MAX_PRICE", "500"))
# kleine, ruime, accent-tolerante lijst — wordt case-insensitive gematched
WIJKEN = [w.strip().lower() for w in os.environ.get(
    "WIJKEN", "centrum,kralingen"
).split(",") if w.strip()]
# 'man' = alleen advertenties zonder voorkeur of die specifiek man toelaten
GENDER = os.environ.get("GENDER", "man").lower()


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
    """Pak het eerste bedrag uit een tekstblok, ondersteunt '€ 1.234,56' en '€499'."""
    # Verwijder duizendtal-punten, vervang decimaal-komma door punt
    m = re.search(r"€\s*([\d\.\,]+)", text)
    if not m:
        return None
    raw = m.group(1).replace(".", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def gender_ok(text: str) -> bool:
    """
    Bekijk hoe de advertentie geslacht beschrijft.
    Accepteer als:
      - er helemaal geen voorkeur staat (de meeste advertenties), of
      - de voorkeur 'man' bevat, of
      - 'geen voorkeur' / 'm/v' / 'iedereen' wordt genoemd
    Weiger expliciet als er alleen 'vrouw' / 'vrouwelijk' staat zonder 'man'.
    """
    t = text.lower()

    # Trefwoorden die op geslachtsvoorkeur duiden
    has_gender_signal = any(k in t for k in (
        "geslacht", "voorkeur", "vrouw", "man", "m/v", "v/m"
    ))
    if not has_gender_signal:
        return True  # geen melding van geslacht → ok

    if GENDER == "man":
        if "vrouw" in t and "man" not in t and "m/v" not in t and "v/m" not in t:
            return False
        return True

    if GENDER == "vrouw":
        if "man" in t and "vrouw" not in t and "m/v" not in t and "v/m" not in t:
            return False
        return True

    return True  # GENDER='alles' o.i.d.


def wijk_ok(text: str) -> bool:
    t = text.lower()
    return any(w in t for w in WIJKEN)


# ---------------------------------------------------------------- scrape
def fetch_listings() -> list[dict]:
    """
    Haal de aanbod-pagina op en probeer listings te extraheren.

    Strategie: laad pagina, scroll naar onder (lazy loading), pak alle links
    die naar individuele aanbod-detailpagina's wijzen, en gebruik hun
    container-element als bron-tekst voor prijs/wijk/geslacht-info.
    """
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
        ):
            try:
                page.locator(sel).first.click(timeout=2_000)
                break
            except Exception:
                pass

        # Wacht tot er listing-achtige content is. We gebruiken een ruime hint:
        # de detailpagina's hebben /nl/aanbod/<id> als pad.
        try:
            page.wait_for_selector("a[href*='/nl/aanbod/']", timeout=15_000)
        except Exception:
            # Misschien een andere URL-structuur — sla pagina op voor debug
            DEBUG_HTML.write_text(page.content())
            browser.close()
            print(
                "WAARSCHUWING: geen listings gedetecteerd via "
                "a[href*='/nl/aanbod/']. Pagina opgeslagen in "
                f"{DEBUG_HTML.name} voor inspectie.",
                file=sys.stderr,
            )
            return []

        # Scroll om lazy-loaded items op te halen
        for _ in range(6):
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(800)

        # Bewaar gerenderde HTML voor debug — handig om selectors te tunen
        DEBUG_HTML.write_text(page.content())

        # Verzamel unieke aanbod-links + hun zichtbare container-tekst
        anchors = page.locator("a[href*='/nl/aanbod/']")
        n = anchors.count()
        out: dict[str, dict] = {}
        for i in range(n):
            a = anchors.nth(i)
            href = a.get_attribute("href") or ""
            # Negeer de overzichtspagina zelf en duplicates
            if href.rstrip("/").endswith("/aanbod"):
                continue
            url = href if href.startswith("http") else f"https://www.stadswonenrotterdam.nl{href}"
            if url in out:
                continue
            # Pak de tekst van de dichtstbijzijnde 'card'-container
            try:
                card = a.locator(
                    "xpath=ancestor::*[self::article or self::li or "
                    "contains(@class,'card') or contains(@class,'Card')][1]"
                )
                blob = (card.inner_text(timeout=2_000) or "").strip()
                if not blob:
                    blob = (a.inner_text(timeout=2_000) or "").strip()
            except Exception:
                blob = (a.inner_text(timeout=2_000) or "").strip()
            out[url] = {"url": url, "text": blob}

        browser.close()
        return list(out.values())


# ---------------------------------------------------------------- match
def matches_filters(listing: dict) -> tuple[bool, str]:
    text = listing["text"]
    price = parse_price(text)

    if price is None:
        return False, "geen prijs gevonden"
    if price > MAX_PRICE:
        return False, f"prijs €{price:.0f} > €{MAX_PRICE:.0f}"
    if not wijk_ok(text):
        return False, "wijk niet in voorkeurslijst"
    if not gender_ok(text):
        return False, "geslachtsvoorkeur mismatch"
    return True, "match"


# ---------------------------------------------------------------- main
def main() -> int:
    listings = fetch_listings()
    print(f"Gevonden listings op pagina: {len(listings)}")

    seen = load_seen()
    new_matches = []
    for lst in listings:
        ok, reason = matches_filters(lst)
        if not ok:
            continue
        if lst["url"] in seen:
            continue
        new_matches.append(lst)
        seen.add(lst["url"])

    print(f"Nieuwe matches: {len(new_matches)}")

    for m in new_matches:
        # Telegram bericht — kort, prijs + eerste regels + link
        first_line = next(
            (ln.strip() for ln in m["text"].splitlines() if ln.strip()),
            "Nieuwe kamer",
        )
        price = parse_price(m["text"])
        msg = (
            f"🏠 *Nieuwe kamer* — €{price:.0f}/mnd\n"
            f"{first_line}\n\n{m['url']}"
        )
        try:
            send_telegram(msg)
        except Exception as e:
            print(f"Telegram-fout: {e}", file=sys.stderr)

    save_seen(seen)
    return 0


if __name__ == "__main__":
    sys.exit(main())
