# Stadswonen Rotterdam — kamer-alert

Checkt elke 15 minuten het aanbod van Stadswonen Rotterdam en stuurt je een
Telegram-bericht zodra er een nieuwe kamer is die aan jouw filters voldoet.

## Filters (standaard)

| filter | waarde |
| --- | --- |
| max prijs | € 500 |
| wijken | Centrum, Kralingen |
| geslacht | man (advertenties zonder voorkeur tellen ook mee) |

Aanpassen kan in `.github/workflows/check.yml` onder `env:`.

## Eenmalige setup (10 minuten)

### 1. Maak een Telegram-bot

1. Open Telegram, zoek **@BotFather**.
2. `/newbot` → geef 'm een naam → je krijgt een **bot token**
   (`123456:ABC...`). Bewaar 'm.
3. Stuur je nieuwe bot een willekeurig bericht (klik op de link die BotFather
   geeft of zoek de botnaam).
4. Open in je browser:
   `https://api.telegram.org/bot<JOUW_TOKEN>/getUpdates`
   Zoek `"chat":{"id":<getal>}` — dat getal is je **chat ID**.

### 2. Repo aanmaken

1. Maak een nieuwe (private) GitHub repo.
2. Upload alle bestanden uit dit pakket.
3. Ga naar **Settings → Secrets and variables → Actions → New repository
   secret** en voeg toe:
   - `TELEGRAM_BOT_TOKEN` = je bot token
   - `TELEGRAM_CHAT_ID` = je chat ID

### 3. Workflow aanzetten

1. Tab **Actions** → klik **Stadswonen check** → **Run workflow** om handmatig
   een eerste run te doen. De cron-trigger start daarna vanzelf elk kwartier.
2. Bij de eerste run zal `seen.json` worden gevuld met alle huidige
   advertenties die aan je filters voldoen. Je krijgt voor die batch een
   Telegram-melding. Daarna alleen voor écht nieuwe kamers.

## Filters aanpassen

In `.github/workflows/check.yml`:

```yaml
env:
  MAX_PRICE: "500"
  WIJKEN: "centrum,kralingen"   # comma-gescheiden, kleine letters
  GENDER: "man"                  # 'man', 'vrouw', of 'alles'
```

Commit & push, klaar.

## Lokaal testen

```bash
pip install -r requirements.txt
python -m playwright install chromium
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_CHAT_ID=...
python scraper.py
```

## Eerste run: verifieer dat de selectors kloppen

De scraper schraapt op basis van links naar `/nl/aanbod/<id>`. Mocht de site
ooit z'n URL-structuur wijzigen en de scraper geen listings vinden:

- Check de **Actions-run artifacts**: `last_page.html` wordt geüpload met de
  gerenderde pagina-inhoud.
- Pas in `scraper.py` de selector `a[href*='/nl/aanbod/']` aan op basis van
  wat je in de HTML ziet.

## Waarom Telegram en geen email?

- Push-notificatie op je telefoon, binnen ~2 seconden.
- Geen SMTP-config, geen spamfolder-risico.
- Setup is letterlijk twee minuten.

Wil je tóch email? Vervang `notify.py` met een SMTP-implementatie en gebruik
Gmail's app-passwords of een dienst als Resend / SendGrid.

## Kosten

GitHub Actions free tier: 2.000 minuten/maand voor private repos, onbeperkt
voor public repos. Elke run duurt ~1 minuut → max ~2.880 minuten/maand bij
elke 15 min. **Tip:** maak de repo public (er staat geen geheim in de code
zelf — alleen in Secrets), dan is het hoe dan ook gratis.
