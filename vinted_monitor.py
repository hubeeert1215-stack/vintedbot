"""
Vinted Monitor - wykrywa nowe oferty pasujące do zadanych fraz i progów cenowych,
wysyła powiadomienia na Telegram.

WYMAGANIA:
    pip install requests

KONFIGURACJA:
    1. Utwórz bota na Telegramie przez @BotFather -> dostaniesz TELEGRAM_BOT_TOKEN
    2. Napisz do swojego bota cokolwiek, potem wejdź na:
       https://api.telegram.org/bot<TOKEN>/getUpdates
       i odczytaj swój "chat_id"
    3. Uzupełnij sekcję CONFIG poniżej

URUCHAMIANIE:
    Najlepiej jako zadanie cykliczne (np. cron co 5 minut):
        */5 * * * * /usr/bin/python3 /sciezka/do/vinted_monitor.py

    Można też uruchomić jednorazowo, żeby sprawdzić działanie:
        python3 vinted_monitor.py

UWAGA:
    - Vinted nie udostępnia oficjalnego publicznego API do tego celu - skrypt
      korzysta z tego samego endpointu, którego używa wyszukiwarka na stronie.
      Zbyt częste odpytywanie (np. co kilka sekund) może skutkować
      ograniczeniem/blokadą - odstęp 5 minut jest bezpieczny.
    - Progi cenowe ("dobra cena") ustawiasz sam, na podstawie własnej wiedzy
      o rynku dla danego produktu.
"""

import json
import os
import time
import requests

# ============== CONFIG ==============

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "TUTAJ_WKLEJ_TOKEN_BOTA")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "TUTAJ_WKLEJ_CHAT_ID")

VINTED_DOMAIN = "www.vinted.pl"  # zmień na swoją domenę Vinted (np. www.vinted.de)

# Lista monitorowanych produktów: fraza wyszukiwania + maksymalna cena "dobrej okazji"
WATCHLIST = [
    # Adidas
    {"query": "bluza adidas originals", "max_price": 48, "min_price": 0},
    {"query": "adidas firebird", "max_price": 60, "min_price": 0},

    # Carhartt
    {"query": "carhartt kurtka detroit", "max_price": 85, "min_price": 0},
    {"query": "carhartt active jacket", "max_price": 85, "min_price": 0},
    {"query": "carhartt wip kurtka", "max_price": 72, "min_price": 0},
    {"query": "carhartt vintage kurtka", "max_price": 95, "min_price": 0},

    # Ralph Lauren
    {"query": "ralph lauren polo vintage", "max_price": 48, "min_price": 0},
    {"query": "ralph lauren kurtka", "max_price": 72, "min_price": 0},
    {"query": "polo ralph lauren sweter", "max_price": 48, "min_price": 0},

    # Patagonia
    {"query": "patagonia synchilla", "max_price": 72, "min_price": 0},
    {"query": "patagonia retro x", "max_price": 120, "min_price": 0},
    {"query": "patagonia kurtka puchowa", "max_price": 108, "min_price": 0},

    # Luksusowe/drogie marki - realna okazja
    {"query": "canada goose kurtka", "max_price": 120, "min_price": 0},
    {"query": "stone island kurtka", "max_price": 108, "min_price": 0},
    {"query": "burberry kurtka", "max_price": 96, "min_price": 0},
    {"query": "barbour kurtka woskowana", "max_price": 85, "min_price": 0},
    {"query": "the north face nuptse vintage", "max_price": 96, "min_price": 0},
    {"query": "supreme bluza", "max_price": 72, "min_price": 0},
    {"query": "cp company kurtka", "max_price": 108, "min_price": 0},
    {"query": "arcteryx kurtka", "max_price": 120, "min_price": 0},
]
CHECK_INTERVAL_SECONDS = 300  # 5 minut - odstęp między sprawdzeniami przy trybie ciągłym
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
    """Odpytuje endpoint wyszukiwania Vinted i zwraca liste ofert."""
    url = f"https://{VINTED_DOMAIN}/api/v2/catalog/items"
    params = {
        "search_text": query,
        "order": order,
        "per_page": per_page,
    }
    # Vinted czesto wymaga wczesniejszego pobrania ciasteczek z glownej strony,
    # zeby zaakceptowac zapytania do API - stad request do strony glownej ponizej.
    session.get(f"https://{VINTED_DOMAIN}/", headers=HEADERS, timeout=10)
    resp = session.get(url, headers=HEADERS, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data.get("items", [])


def check_watchlist():
    seen_items = load_seen_items()
    session = requests.Session()
    new_matches = []

    for entry in WATCHLIST:
        query = entry["query"]
        max_price = entry.get("max_price")
        min_price = entry.get("min_price", 0)

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

            price_info = item.get("price", {})
            price = float(price_info.get("amount", 0))

            seen_items.add(item_id)

            if min_price <= price <= (max_price if max_price is not None else float("inf")):
                title = item.get("title", "brak tytulu")
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
    # Domyslnie uruchamia jedno sprawdzenie - do pracy ciaglej w tle
    # zamien ponizsza linie na run_forever(), albo odpalaj przez cron.
    run_once()
