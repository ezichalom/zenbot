import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from deep_translator import GoogleTranslator
import re

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ZYTE_API_KEY = os.getenv("ZYTE_API_KEY")

bot = Bot(token=TOKEN)
seen = set()

KEYWORDS = [
    "tag heuer WAZ1110",
    "tag heuer WAZ1112",
    "tag heuer CAZ1010",
    "tag heuer formula 1",
    "タグホイヤー フォーミュラ1",
    "bvlgari scuba",
    "bvlgari aluminium AL38",
    "ブルガリ アルミニウム",
    "AL38TA",
    "Omega Speedmaster 3513.50",
    "Omega Speedmaster 3513.30",
    "オメガ スピードマスター",
    "WAZ1110",
    "AL38"
]

BAD_WORDS = [
    "belt", "strap", "ベルト",
    "band", "バンド",
    "部品", "parts",
]

JPY_TO_BRL = 0.035


def convert_price(price_text):
    try:
        value = int(re.sub(r"[^\d]", "", price_text))
        brl = int(value * JPY_TO_BRL)
        return f"¥{value:,} (~R$ {brl:,})"
    except:
        return price_text


# 🟢 Yahoo grátis
def fetch(url):
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.text
    except:
        pass
    return None


# 🔴 Zyte
def fetch_zyte(url):
    try:
        res = requests.post(
            "https://api.zyte.com/v1/extract",
            auth=(ZYTE_API_KEY, ""),
            json={"url": url, "browserHtml": True},
            timeout=20
        )
        return res.json().get("browserHtml")
    except:
        return None


# 🔥 Yahoo
def scrape_yahoo(keyword):
    url = f"https://auctions.yahoo.co.jp/search/search?p={keyword}"
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

            if not ("1日" in li.text or "時間" in li.text):
                continue

            href = li.select_one("a").get("href")
            auction_id = href.split("/")[-1]

            price = li.select_one(".Product__priceValue")
            price = price.get_text(strip=True) if price else "N/A"

            items.append({
                "id": "yahoo_" + auction_id,
                "title": title,
                "price": price,
                "link": f"https://zenmarket.jp/pt/auction.aspx?itemCode={auction_id}"
            })

        except:
            continue

    return items[:5]


# 🔥 Mercari
def scrape_mercari(keyword):
    url = f"https://jp.mercari.com/search?keyword={keyword}&sort=created_time&order=desc"
    html = fetch_zyte(url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        if "/item/" not in a["href"]:
            continue

        try:
            title = a.get_text(strip=True)
            if not title:
                continue

            if any(b.lower() in title.lower() for b in BAD_WORDS):
                continue

            item_id = a["href"].split("/")[-1]

            items.append({
                "id": "mercari_" + item_id,
                "title": title,
                "price": "Buy Now",
                "link": f"https://zenmarket.jp/pt/?url=https://jp.mercari.com{a['href']}"
            })

        except:
            continue

    return items[:5]


# 🔥 Rakuma
def scrape_rakuma(keyword):
    url = f"https://fril.jp/s?query={keyword}&sort=created_at_desc"
    html = fetch_zyte(url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        if "/item/" not in a["href"]:
            continue

        try:
            title = a.get_text(strip=True)
            if not title:
                continue

            if any(b.lower() in title.lower() for b in BAD_WORDS):
                continue

            item_id = a["href"].split("/")[-1]

            items.append({
                "id": "rakuma_" + item_id,
                "title": title,
                "price": "Buy Now",
                "link": f"https://zenmarket.jp/pt/?url=https://fril.jp{a['href']}"
            })

        except:
            continue

    return items[:5]


# 🔥 Rakuten (novo)
def scrape_rakuten(keyword):
    url = f"https://search.rakuten.co.jp/search/mall/{keyword}/"
    html = fetch_zyte(url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        if "rakuten.co.jp" not in a["href"]:
            continue

        try:
            title = a.get_text(strip=True)
            if len(title) < 20:
                continue

            if any(b.lower() in title.lower() for b in BAD_WORDS):
                continue

            items.append({
                "id": "rakuten_" + a["href"],
                "title": title,
                "price": "Store Price",
                "link": f"https://zenmarket.jp/pt/?url={a['href']}"
            })

        except:
            continue

    return items[:3]  # menos spam


def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text


async def run():
    while True:

        # ⚡ SNIPER
        for keyword in KEYWORDS:
            items = []
            items += scrape_mercari(keyword)
            items += scrape_rakuma(keyword)
            items += scrape_rakuten(keyword)

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                title = translate(item["title"])[:60]

                msg = f"""⚡ COMPRA IMEDIATA

⌚ {title}

💰 {item['price']}

🔗 {item['link']}
"""

                await bot.send_message(chat_id=CHAT_ID, text=msg)

        await asyncio.sleep(30)

        # 🔥 LEILÃO
        for keyword in KEYWORDS:
            items = scrape_yahoo(keyword)

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                title = translate(item["title"])[:60]
                price = convert_price(item["price"])

                msg = f"""🔥 LEILÃO TERMINANDO

⌚ {title}

💰 {price}

🔗 {item['link']}
"""

                await bot.send_message(chat_id=CHAT_ID, text=msg)

        await asyncio.sleep(90)


asyncio.run(run())
