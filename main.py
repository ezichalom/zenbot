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

# 🔥 KEYWORDS OTIMIZADAS
KEYWORDS = [
    # TAG HEUER
    "tag heuer WAZ1110",
    "tag heuer WAZ1112",
    "tag heuer CAZ1010",
    "tag heuer formula 1 watch",
    "タグホイヤー WAZ1110",
    "タグホイヤー フォーミュラ1 時計",

    # BVLGARI
    "bvlgari aluminium AL38",
    "bvlgari scuba",
    "ブルガリ アルミニウム AL38",
    "ブルガリ スクーバ",

    # SHORT KEY (mais agressivo)
    "WAZ1110",
    "AL38"
]

# 🔥 FILTRO DE LIXO
BAD_WORDS = [
    "ベルト", "belt", "pulseira",
    "strap", "バンド", "band",
    "ケースのみ", "case only",
    "部品", "parts"
]

# 🔥 USER AGENTS ROTATIVO
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
    "Mozilla/5.0 (X11; Linux x86_64)"
]


# 🔥 FETCH ANTI-BLOQUEIO
def fetch(url):
    for _ in range(3):
        try:
            res = requests.get(
                url,
                headers={
                    "User-Agent": random.choice(USER_AGENTS),
                    "Accept-Language": "ja-JP,ja;q=0.9"
                },
                timeout=15
            )

            if res.status_code != 200:
                continue

            text = res.text

            if "captcha" in text.lower():
                continue
            if "Access Denied" in text:
                continue
            if len(text) < 5000:
                continue

            return text

        except:
            pass

    return None


# 🔥 SCRAPER YAHOO (MELHORADO)
def scrape_yahoo(keyword):
    url = f"https://auctions.yahoo.co.jp/search/search?p={keyword}&ei=UTF-8"
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

            title_tag = li.select_one("h3")
            title = title_tag.get_text(strip=True) if title_tag else ""

            # filtro lixo
            if any(b.lower() in title.lower() for b in BAD_WORDS):
                continue

            # preço
            price_tag = li.select_one(".Product__priceValue")
            price = price_tag.get_text(strip=True) if price_tag else "N/A"

            # imagem
            img_tag = li.select_one("img")
            image = img_tag["src"] if img_tag else None

            items.append({
                "id": "yahoo_" + auction_id,
                "title": title,
                "price": price,
                "auction_id": auction_id,
                "image": image
            })

        except:
            continue

    return items[:5]


# 🔥 ZENMARKET
def to_zen(item):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={item['auction_id']}"


def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text


# 🔥 LOOP PRINCIPAL
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

🔍 {title}

💰 {price}
⚡ Leilão

🔗 Comprar:
{zen_url}
"""

                    # 🔥 ENVIO COM IMAGEM (seguro)
                    if item["image"]:
                        try:
                            await bot.send_photo(
                                chat_id=CHAT_ID,
                                photo=item["image"],
                                caption=msg
                            )
                        except:
                            await bot.send_message(
                                chat_id=CHAT_ID,
                                text=msg
                            )
                    else:
                        await bot.send_message(
                            chat_id=CHAT_ID,
                            text=msg
                        )

                await asyncio.sleep(random.uniform(3, 6))

            except Exception as e:
                print("erro:", e)

        await asyncio.sleep(90)  # 🔥 menos bloqueio


asyncio.run(run())
