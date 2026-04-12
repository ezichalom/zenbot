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
    "tag heuer formula 1",
    "bvlgari aluminium",
    "cartier chronoscaph",
    "タグホイヤー フォーミュラ1",
    "ブルガリ アルミニウム",
]

# FILTRO DE LIXO
BAD_WORDS = [
    "ベルト", "belt", "pulseira",
    "strap", "バンド", "band",
    "ケースのみ", "case only",
    "ジャンク部品"
]

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept-Language": "ja-JP,ja;q=0.9"
})


def fetch(url):
    try:
        res = session.get(url, timeout=15)
        if res.status_code == 200 and len(res.text) > 5000:
            return res.text
    except:
        pass
    return None


# -------- YAHOO MELHORADO --------
def scrape_yahoo(keyword):
    url = f"https://auctions.yahoo.co.jp/search/search?p={keyword}&ei=UTF-8"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for item in soup.select("li.Product"):
        try:
            link_tag = item.select_one("a")
            if not link_tag:
                continue

            href = link_tag.get("href")
            clean_url = href.split("?")[0]
            auction_id = clean_url.split("/")[-1]

            title_tag = item.select_one("h3")
            title = title_tag.get_text(strip=True) if title_tag else ""

            # filtro lixo
            if any(bad.lower() in title.lower() for bad in BAD_WORDS):
                continue

            # preço
            price_tag = item.select_one(".Product__priceValue")
            price = price_tag.get_text(strip=True) if price_tag else "N/A"

            # imagem
            img_tag = item.select_one("img")
            image = img_tag["src"] if img_tag else None

            items.append({
                "id": "yahoo_" + auction_id,
                "title": title,
                "price": price,
                "url": clean_url,
                "auction_id": auction_id,
                "image": image,
                "type": "Leilão",
                "source": "Yahoo"
            })

        except:
            continue

    return items[:5]


# -------- ZENMARKET --------
def to_zen(item):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={item['auction_id']}"


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
                items = scrape_yahoo(keyword)

                for item in items:
                    if item["id"] in seen:
                        continue

                    seen.add(item["id"])

                    title = translate(item["title"])[:80]
                    price = item["price"] if item["price"] != "N/A" else "Consultar"

                    zen_url = to_zen(item)

                    msg = f"""🔥 OPORTUNIDADE

⌚ {title}

💰 {price}
⚡ Leilão

🔗 Comprar:
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

                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                print("erro:", e)

        await asyncio.sleep(30)


asyncio.run(run())
