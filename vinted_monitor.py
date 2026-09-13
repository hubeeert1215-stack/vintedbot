"""
Vinted Monitor - wykrywa nowe oferty pasujące do zadanych fraz i progów cenowych,
z filtrem marki w tytule (odrzuca spam/keyword-stuffing w opisach),
wysyla powiadomienia na Telegram.

WYMAGANIA:
    pip install requests

KONFIGURACJA:
    Patrz sekcja CONFIG ponizej - token bota i chat_id sa wczytywane
    ze zmiennych srodowiskowych (ustawianych jako sekrety w GitHub Actions).
"""

import json
import os
import time
import requests

# ============== CONFIG ==============

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TUTAJ_WKLEJ_TOKEN_BOTA")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "TUTAJ_WKLEJ_CHAT_ID")

VINTED_DOMAIN = "www.vinted.pl"

# Lista monitorowanych produktow. Kazdy wpis:
#   query          - fraza wyszukiwania na Vinted
#   max_price      - maksymalna cena "dobrej okazji"
#   min_price      - minimalna cena (domyslnie 0)
#   brand_keywords - lista slow kluczowych; oferta MUSI miec jedno z nich
#                    w TYTULE (nie tylko w opisie), inaczej zostanie odrzucona
#                    jako falszywe trafienie (spam slowami-kluczami w opisie)
WATCHLIST = [
    # Adidas
    {"query": "bluza adidas originals", "max_price": 48, "min_price": 0,
     "brand_keywords": ["adidas"]},
    {"query": "adidas firebird", "max_price": 60, "min_price": 0,
     "brand_keywords": ["adidas", "firebird"]},

    # Carhartt
    {"query": "carhartt kurtka detroit", "max_price": 85, "min_price": 0,
     "brand_keywords": ["carhartt"]},
    {"query": "carhartt active jacket", "max_price": 85, "min_price": 0,
     "brand_keywords": ["carhartt"]},
    {"query": "carhartt wip kurtka", "max_price": 72, "min_price": 0,
     "brand_keywords": ["carhartt"]},
    {"query": "carhartt vintage kurtka", "max_price": 95, "min_price": 0,
     "brand_keywords": ["carhartt"]},

    # Ralph Lauren (obnizone progi - to bardzo popularna marka na Vinted,
    # wiec ogranicza to liczbe "zwyklych" trafien)
    {"query": "ralph lauren polo vintage", "max_price": 35, "min_price": 0,
     "brand_keywords": ["ralph lauren", "polo ralph", "ralph"]},
    {"query": "ralph lauren kurtka", "max_price": 50, "min_price": 0,
     "brand_keywords": ["ralph lauren", "polo ralph", "ralph"]},
    {"query": "polo ralph lauren sweter", "max_price": 35, "min_price": 0,
     "brand_keywords": ["ralph lauren", "polo ralph", "ralph"]},

    # Patagonia
    {"query": "patagonia synchilla", "max_price": 72, "min_price": 0,
     "brand_keywords": ["patagonia"]},
    {"query": "patagonia retro x", "max_price": 120, "min_price": 0,
     "brand_keywords": ["patagonia"]},
    {"query": "patagonia kurtka puchowa", "max_price": 108, "min_price": 0,
     "brand_keywords": ["patagonia"]},

    # Luksusowe/drogie marki - realna okazja
    {"query": "moncler kurtka", "max_price": 120, "min_price": 0,
     "brand_keywords": ["moncler"]},
    {"query": "canada goose kurtka", "max_price": 120, "min_price": 0,
     "brand_keywords": ["canada goose", "canada-goose"]},
    {"query": "stone island kurtka", "max_price": 108, "min_price": 0,
     "brand_keywords": ["stone island", "stone-island"]},
    {"query": "burberry kurtka", "max_price": 96, "min_price": 0,
     "brand_keywords": ["burberry"]},
    {"query": "barbour kurtka woskowana", "max_price": 85, "min_price": 0,
     "brand_keywords": ["barbour"]},
    {"query": "the north face nuptse vintage", "max_price": 96, "min_price": 0,
     "brand_keywords": ["north face", "tnf", "nuptse"]},
    {"query": "supreme bluza", "max_price": 72, "min_price": 0,
     "brand_keywords": ["supreme"]},
    {"query": "cp company kurtka", "max_price": 108, "min_price": 0,
     "brand_keywords": ["cp company", "c.p. company", "c.p.company"]},
    {"query": "arcteryx kurtka", "max_price": 120, "min_price": 0,
     "brand_keywords": ["arcteryx", "arc'teryx", "arc teryx"]},
    {"query": "off white bluza", "max_price": 108, "min_price": 0,
     "brand_keywords": ["off white", "off-white", "offwhite"]},
]

CHECK_INTERVAL_SECONDS = 300
SEEN_ITEMS_FILE = "vinted_seen_items.json"

# ============== KOD ==============

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def load_seen_items():
    if os.path.exists(SEEN_ITEMS_FILE):
        with open(SEEN_ITEMS_FILE, "r", encoding="utf-8") as f:
            return set(json.load(f))
    return set()


def save_seen_items(seen_items):
    with open(SEEN_ITEMS_FILE, "w", encoding="utf-8") as f:
        json.dump(list(seen_items), f)


def send_telegram_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}
    try:
        r = requests.post(url, data=payload, timeout=10)
        r.raise_for_status()
    except requests.RequestException as e:
        print(f"Blad wysylania powiadomienia Telegram: {e}")


def search_vinted(query, session, order="newest_first", per_page=20):
    url = f"https://{VINTED_DOMAIN}/api/v2/catalog/items"
    params = {
        "search_text": query,
        "order": order,
        "per_page": per_page,
    }
    session.get(f"https://{VINTED_DOMAIN}/", headers=HEADERS, timeout=10)
    resp = session.get(url, headers=HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def title_matches_brand(title, brand_keywords):
    """Sprawdza czy tytul oferty faktycznie zawiera jedno ze slow-kluczy marki."""
    title_lower = title.lower()
    return any(keyword.lower() in title_lower for keyword in brand_keywords)


def check_watchlist():
    seen_items = load_seen_items()
    session = requests.Session()
    new_matches = []

    for entry in WATCHLIST:
        query = entry["query"]
        max_price = entry.get("max_price")
        min_price = entry.get("min_price", 0)
        brand_keywords = entry.get("brand_keywords", [])

        try:
            items = search_vinted(query, session)
        except requests.RequestException as e:
            print(f"Blad zapytania dla '{query}': {e}")
            continue

        print(f"Fraza '{query}': pobrano {len(items)} ofert z Vinted.")

        for item in items:
            item_id = str(item.get("id"))
            if item_id in seen_items:
                continue

            title = item.get("title", "")
            seen_items.add(item_id)

            # Odrzuc falszywe trafienia - marka musi byc faktycznie w tytule
            if brand_keywords and not title_matches_brand(title, brand_keywords):
                continue

            price_info = item.get("price", {})
            price = float(price_info.get("amount", 0))

            if min_price <= price <= (max_price if max_price is not None else float("inf")):
                item_url = item.get("url", "")
                new_matches.append(
                    f"🟢 <b>{title}</b>\n"
                    f"Cena: {price} zl\n"
                    f"Fraza: {query}\n"
                    f"{item_url}"
                )

    save_seen_items(seen_items)
    return new_matches


def run_once():
    matches = check_watchlist()
    if matches:
        for m in matches:
            send_telegram_message(m)
        print(f"Znaleziono {len(matches)} nowych ofert w dobrej cenie.")
    else:
        print("Brak nowych ofert w dobrej cenie.")


def run_forever():
    print("Start monitorowania Vinted (Ctrl+C aby zatrzymac)...")
    while True:
        run_once()
        time.sleep(CHECK_INTERVAL_SECONDS)


if __name__ == "__main__":
    run_once()
