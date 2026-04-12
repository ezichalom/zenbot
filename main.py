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

BAD_WORDS = [
    "ベルト", "belt", "pulseira",
    "strap", "バンド", "band",
    "ケースのみ", "case only",
    "部品"
]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
]

# 🔥 TAXA APROXIMADA (ajustável)
JPY_TO_BRL = 0.035


def convert_price(price_text):
    try:
        value = int(re.sub(r"[^\d]", "", price_text))
        brl = int(value * JPY_TO_BRL)
        return f"¥{value:,} (~R$ {brl:,})"
    except:
        return price_text


def fetch(url):
    for _ in range(3):
        try:
            res = requests.get(
                url,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept-Language": "ja-JP"
                },
                timeout=15
            )

            if res.status_code != 200:
                continue

            text = res.text

            if "captcha" in text.lower():
                continue
            if len(text) < 5000:
                continue

            return text

        except:
            pass

    return None


def scrape_yahoo(keyword):
    url = f"https://auctions.yahoo.co.jp/search/search?p={keyword}&ei=UTF-8&sort=end&order=d"
    html = fetch(url)
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select("li.Product"):
        try:
            a = li.select_one("a")
            if not a:
                continue

            href = a.get("href")
            clean_url = href.split("?")[0]
            auction_id = clean_url.split("/")[-1]

            title = li.select_one("h3").get_text(strip=True)

            if any(b.lower() in title.lower() for b in BAD_WORDS):
                continue

            price_tag = li.select_one(".Product__priceValue")
            price = price_tag.get_text(strip=True) if price_tag else "N/A"

            img_tag = li.select_one("img")
            image = img_tag["src"] if img_tag else None

            items.append({
                "id": auction_id,
                "title": title,
                "price": price,
                "image": image
            })

        except:
            continue

    return items[:10]  # 🔥 sniper intermediário


def to_zen(item):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={item['id']}"


def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text


async def run():
    while True:
        for keyword in KEYWORDS:
            try:
                items = scrape_yahoo(keyword)

                for item in items:
                    if item["id"] in seen:
                        continue

                    seen.add(item["id"])

                    title = translate(item["title"])[:60].upper()
                    price = convert_price(item["price"])
                    link = to_zen(item)

                    msg = f"""🔥 OPORTUNIDADE

🔍 {title}

💰 {price}
⚡ Leilão

🔗 Comprar:
{link}
"""

                    if item["image"]:
                        try:
                            await bot.send_photo(
                                chat_id=CHAT_ID,
                                photo=item["image"],
                                caption=msg
                            )
                        except:
                            await bot.send_message(chat_id=CHAT_ID, text=msg)
                    else:
                        await bot.send_message(chat_id=CHAT_ID, text=msg)

                await asyncio.sleep(random.uniform(2, 4))

            except Exception as e:
                print("erro:", e)

        await asyncio.sleep(90)


asyncio.run(run())
