import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from deep_translator import GoogleTranslator
import random

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)
seen = set()

KEYWORDS = [
    "Bvlgari al38",
    "bvlgari aluminium",
    "tag heuer formula 1",
    "Cartier chronoscaph",
    "ブルガリ アルミニウム",
    "タグホイヤー フォーミュラ1",
    "カルティエ クロノスカフ"
]

# 🔥 SESSION (muito mais difícil de bloquear)
session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8",
    "Accept": "text/html,application/xhtml+xml",
    "Connection": "keep-alive"
})


# 🔥 FETCH ANTI-BLOQUEIO
def fetch(url):
    for _ in range(3):
        try:
            res = session.get(url, timeout=15)

            if res.status_code != 200:
                continue

            text = res.text

            # 🔥 DETECTA BLOQUEIO
            if "Access Denied" in text:
                return None
            if "captcha" in text.lower():
                return None
            if len(text) < 5000:
                return None

            return text

        except Exception as e:
            print("fetch error:", e)

    return None


# -------- MERCARI --------
def scrape_mercari(keyword):
    url = f"https://jp.mercari.com/search?keyword={keyword}"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.select("a[href*='/item/']"):
        href = a.get("href")
        if not href:
            continue

        item_id = "mercari_" + href.split("/")[-1]
        full_url = f"https://jp.mercari.com{href}"

        title = a.get_text(strip=True)

        img = a.find("img")
        image = img["src"] if img else None

        price = None
        for s in a.find_all("span"):
            if "¥" in s.text:
                price = s.text
                break

        items.append({
            "id": item_id,
            "title": title,
            "price": price,
            "url": full_url,
            "image": image,
            "type": "Buy Now",
            "source": "Mercari"
        })

    return items[:5]


# -------- YAHOO --------
def scrape_yahoo(keyword):
    url = f"https://auctions.yahoo.co.jp/search/search?p={keyword}&auccat=0"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.select("a[href*='/auction/']"):
        href = a.get("href")
        if not href:
            continue

        item_id = "yahoo_" + href.split("/")[-1]

        title = a.get_text(strip=True)

        img = a.find("img")
        image = img["src"] if img else None

        price = None
        for s in a.find_all("span"):
            if "円" in s.text:
                price = s.text
                break

        items.append({
            "id": item_id,
            "title": title,
            "price": price,
            "url": href,
            "image": image,
            "type": "Auction",
            "source": "Yahoo"
        })

    return items[:5]


# -------- RAKUMA --------
def scrape_rakuma(keyword):
    url = f"https://fril.jp/s?query={keyword}"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.select("a[href*='/item/']"):
        href = a.get("href")
        if not href:
            continue

        item_id = "rakuma_" + href.split("/")[-1]
        full_url = f"https://fril.jp{href}"

        title = a.get_text(strip=True)

        img = a.find("img")
        image = img["src"] if img else None

        items.append({
            "id": item_id,
            "title": title,
            "price": "N/A",
            "url": full_url,
            "image": image,
            "type": "Buy Now",
            "source": "Rakuma"
        })

    return items[:5]


# -------- ZENMARKET --------
def to_zen(url):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={url}"


def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text


# -------- LOOP --------
async def run():
    while True:
        for keyword in KEYWORDS:
            try:
                items = []
                items += scrape_mercari(keyword)
                items += scrape_yahoo(keyword)
                items += scrape_rakuma(keyword)

                for item in items:
                    if item["id"] in seen:
                        continue

                    seen.add(item["id"])

                    translated = translate(item["title"])
                    zen_url = to_zen(item["url"])

                    msg = f"""
🔥 OPORTUNIDADE

📦 {item['source']}
🔍 {keyword}

📝 {translated}
💴 {item['price']}
⚡ {item['type']}

🔗 ZenMarket:
{zen_url}
"""

                    if item["image"]:
                        await bot.send_photo(
                            chat_id=CHAT_ID,
                            photo=item["image"],
                            caption=msg
                        )
                    else:
                        await bot.send_message(
                            chat_id=CHAT_ID,
                            text=msg
                        )

                # 🔥 anti-bloqueio
                await asyncio.sleep(random.uniform(3, 6))

            except Exception as e:
                print("erro:", e)

        await asyncio.sleep(30)


asyncio.run(run())
