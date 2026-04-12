import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from deep_translator import GoogleTranslator
import random
import re

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)
seen = set()

# 🔥 KEYWORDS
KEYWORDS = [
    "tag heuer WAZ1110",
    "tag heuer WAZ1112",
    "tag heuer CAZ1010",
    "bvlgari aluminium AL38",
    "bvlgari scuba",
    "Omega Speedmaster 3513",
    "オメガ スピードマスター",
    "ブルガリ アルミニウム",
]

# 🔥 FILTRO DE LIXO
BAD_WORDS = [
    "belt", "strap", "ベルト",
    "band", "バンド",
    "部品", "parts",
    "ケースのみ", "case only"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
]

JPY_TO_BRL = 0.035


# 🔥 CONVERSÃO ¥ → R$
def convert_price(price_text):
    try:
        value = int(re.sub(r"[^\d]", "", price_text))
        brl = int(value * JPY_TO_BRL)
        return f"¥{value:,} (~R$ {brl:,})"
    except:
        return price_text


# 🔥 FETCH
def fetch(url):
    try:
        res = requests.get(
            url,
            headers={
                "User-Agent": random.choice(USER_AGENTS),
                "Accept-Language": "ja-JP"
            },
            timeout=10
        )
        if res.status_code == 200 and len(res.text) > 3000:
            return res.text
    except:
        pass
    return None


# 🔥 YAHOO (leilão inteligente)
def scrape_yahoo(keyword):
    url = f"https://auctions.yahoo.co.jp/search/search?p={keyword}&ei=UTF-8"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select("li.Product"):
        try:
            title = li.select_one("h3").get_text(strip=True)

            if any(b.lower() in title.lower() for b in BAD_WORDS):
                continue

            full_text = li.get_text()

            # 🔥 só itens próximos do fim
            if not ("1日" in full_text or "時間" in full_text):
                continue

            a = li.select_one("a")
            href = a.get("href")
            auction_id = href.split("/")[-1]

            price_tag = li.select_one(".Product__priceValue")
            price = price_tag.get_text(strip=True) if price_tag else "N/A"

            items.append({
                "id": auction_id,
                "title": title,
                "price": price
            })

        except:
            continue

    return items[:5]


# 🔥 MERCARI (sniper rápido)
def scrape_mercari(keyword):
    url = f"https://jp.mercari.com/search?keyword={keyword}&sort=created_time&order=desc"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.select("a[href*='/item/']"):
        try:
            title = a.get_text(strip=True)

            if any(b.lower() in title.lower() for b in BAD_WORDS):
                continue

            href = a.get("href")
            item_id = href.split("/")[-1]

            items.append({
                "id": "mercari_" + item_id,
                "title": title,
                "url": f"https://jp.mercari.com{href}"
            })

        except:
            continue

    return items[:5]


# 🔥 LINKS
def to_zen_yahoo(item_id):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={item_id}"


def to_zen_direct(url):
    return f"https://zenmarket.jp/pt/?url={url}"


# 🔥 TRADUÇÃO
def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text


# 🔥 LOOP
async def run():
    while True:

        # ⚡ COMPRA IMEDIATA
        for keyword in KEYWORDS:
            items = scrape_mercari(keyword)

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                title = translate(item["title"])[:60]
                link = to_zen_direct(item["url"])

                msg = f"""⚡ COMPRA IMEDIATA

⌚ {title}

💰 Buy Now

🔗 {link}
"""

                await bot.send_message(chat_id=CHAT_ID, text=msg)

        await asyncio.sleep(25)

        # 🔥 LEILÃO
        for keyword in KEYWORDS:
            items = scrape_yahoo(keyword)

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                title = translate(item["title"])[:60]
                price = convert_price(item["price"])
                link = to_zen_yahoo(item["id"])

                msg = f"""🔥 LEILÃO TERMINANDO

⌚ {title}

💰 {price}

🔗 {link}
"""

                await bot.send_message(chat_id=CHAT_ID, text=msg)

        await asyncio.sleep(90)


asyncio.run(run())
