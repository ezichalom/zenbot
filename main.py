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

KEYWORDS = [
    "tag heuer WAZ1110",
    "bvlgari aluminium AL38",
    "Omega Speedmaster 3513",
    "タグホイヤー フォーミュラ1",
    "ブルガリ アルミニウム"
]

BAD_WORDS = ["belt", "strap", "ベルト", "band", "部品"]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"
]

JPY_TO_BRL = 0.035


def convert_price(price_text):
    try:
        value = int(re.sub(r"[^\d]", "", price_text))
        brl = int(value * JPY_TO_BRL)
        return f"¥{value:,} (~R$ {brl:,})"
    except:
        return price_text


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


# 🔥 YAHOO COM FILTRO DE TEMPO
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

            time_text = li.get_text()

            # 🔥 FILTRO TEMPO
            if not ("1日" in time_text or "時間" in time_text):
                continue

            a = li.select_one("a")
            href = a.get("href")
            auction_id = href.split("/")[-1]

            price_tag = li.select_one(".Product__priceValue")
            price = price_tag.get_text(strip=True) if price_tag else "N/A"

            img = li.select_one("img")
            image = img["src"] if img else None

            items.append({
                "id": auction_id,
                "title": title,
                "price": price,
                "image": image
            })

        except:
            continue

    return items[:5]


# 🔥 MERCARI (SNIPER)
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

            img = a.find("img")
            image = img["src"] if img else None

            items.append({
                "id": "mercari_" + item_id,
                "title": title,
                "price": "Buy Now",
                "image": image,
                "url": f"https://jp.mercari.com{href}"
            })

        except:
            continue

    return items[:5]


def to_zen_yahoo(id):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={id}"


def to_zen_direct(url):
    return f"https://zenmarket.jp/pt/?url={url}"


def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text


async def run():
    while True:

        # 🔥 SNIPER (rápido)
        for keyword in KEYWORDS:
            mercari_items = scrape_mercari(keyword)

            for item in mercari_items:
                if item["id"] in seen:
                    continue
                seen.add(item["id"])

                title = translate(item["title"])[:60]
                link = to_zen_direct(item["url"])

                msg = f"""⚡ COMPRA IMEDIATA

⌚ {title}

💰 BUY NOW

🔗 {link}
"""

                await bot.send_message(chat_id=CHAT_ID, text=msg)

        await asyncio.sleep(25)

        # 🔥 YAHOO (lento + filtrado)
        for keyword in KEYWORDS:
            yahoo_items = scrape_yahoo(keyword)

            for item in yahoo_items:
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

                if item["image"]:
                    await bot.send_photo(chat_id=CHAT_ID, photo=item["image"], caption=msg)
                else:
                    await bot.send_message(chat_id=CHAT_ID, text=msg)

        await asyncio.sleep(90)


asyncio.run(run())
