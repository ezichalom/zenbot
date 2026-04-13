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

GOOD_WORDS = ["watch", "時計", "automatic", "chronograph"]

JPY_TO_BRL = 0.035


def convert_price(price_text):
    try:
        value = int(re.sub(r"[^\d]", "", price_text))
        brl = int(value * JPY_TO_BRL)
        return value, f"¥{value:,} (~R$ {brl:,})"
    except:
        return None, price_text


def score_item(title, price_value):
    title_lower = title.lower()

    if any(b in title_lower for b in BAD_WORDS):
        return "❌ IGNORAR"

    if price_value:
        if price_value < 30000:
            return "🔥 DEAL FORTE"
        elif price_value < 80000:
            return "⚠️ MÉDIO"

    if any(g in title_lower for g in GOOD_WORDS):
        return "⚠️ MÉDIO"

    return "❌ IGNORAR"


def fetch(url):
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.text
    except:
        pass
    return None


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


def scrape_mercari(keyword):
    html = fetch_zyte(f"https://jp.mercari.com/search?keyword={keyword}&sort=created_time&order=desc")
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

            item_id = a["href"].split("/")[-1]

            score = score_item(title, None)
            if score == "❌ IGNORAR":
                continue

            items.append({
                "id": "mercari_" + item_id,
                "title": title,
                "price": "Buy Now",
                "score": score,
                "link": f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={item_id}"
            })

        except:
            continue

    return items[:5]


def scrape_rakuma(keyword):
    html = fetch_zyte(f"https://fril.jp/s?query={keyword}&sort=created_at_desc")
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

            item_id = a["href"].split("/")[-1]

            score = score_item(title, None)
            if score == "❌ IGNORAR":
                continue

            items.append({
                "id": "rakuma_" + item_id,
                "title": title,
                "price": "Buy Now",
                "score": score,
                "link": f"https://zenmarket.jp/pt/?url=https://fril.jp{a['href']}"
            })

        except:
            continue

    return items[:5]


def scrape_yahoo(keyword):
    html = fetch(f"https://auctions.yahoo.co.jp/search/search?p={keyword}")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select("li.Product"):
        try:
            title = li.select_one("h3").get_text(strip=True)

            if not ("1日" in li.text or "時間" in li.text):
                continue

            href = li.select_one("a").get("href")
            auction_id = href.split("/")[-1]

            price_tag = li.select_one(".Product__priceValue")
            price_text = price_tag.get_text(strip=True) if price_tag else "N/A"

            value, price = convert_price(price_text)
            score = score_item(title, value)

            if score == "❌ IGNORAR":
                continue

            items.append({
                "id": "yahoo_" + auction_id,
                "title": title,
                "price": price,
                "score": score,
                "link": f"https://zenmarket.jp/pt/auction.aspx?itemCode={auction_id}"
            })

        except:
            continue

    return items[:5]


def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text


async def send(msg):
    await bot.send_message(
        chat_id=CHAT_ID,
        text=msg,
        disable_web_page_preview=True
    )


async def run():
    while True:

        for keyword in KEYWORDS:
            items = scrape_mercari(keyword) + scrape_rakuma(keyword)

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                title = translate(item["title"])[:60]

                msg = f"""{item['score']}

⚡ COMPRA IMEDIATA
⌚ {title}
💰 {item['price']}

🔗 {item['link']}
"""

                await send(msg)

        await asyncio.sleep(25)

        for keyword in KEYWORDS:
            items = scrape_yahoo(keyword)

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                title = translate(item["title"])[:60]

                msg = f"""{item['score']}

🔥 LEILÃO TERMINANDO
⌚ {title}
💰 {item['price']}

🔗 {item['link']}
"""

                await send(msg)

        await asyncio.sleep(90)


asyncio.run(run())