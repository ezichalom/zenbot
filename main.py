import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

seen = set()

KEYWORDS = ["nike dunk", "jordan 1", "rolex", "seiko"]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

def mercari_to_zenmarket(url):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={url}"

def scrape_mercari(keyword):
    url = f"https://jp.mercari.com/search?keyword={keyword}"
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")

    items = []

    for a in soup.select("a"):
        href = a.get("href", "")

        if "/item/" in href:
            full_url = f"https://jp.mercari.com{href}"
            item_id = href.split("/")[-1]

            # evita duplicado
            if item_id in seen:
                continue

            # tenta pegar imagem
            img = a.find("img")
            image = img["src"] if img else None

            # título
            title = a.text.strip()

            items.append({
                "id": item_id,
                "title": title if title else "Produto",
                "url": full_url,
                "image": image
            })

    return items[:5]

async def run():
    while True:
        for keyword in KEYWORDS:
            try:
                items = scrape_mercari(keyword)

                for item in items:
                    if item["id"] in seen:
                        continue

                    seen.add(item["id"])

                    zen_url = mercari_to_zenmarket(item["url"])

                    msg = f"""
🔥 NOVO ITEM

🔍 {keyword}
📝 {item['title']}

🛒 Comprar via ZenMarket:
{zen_url}
"""

                    # envia com imagem se tiver
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

            except Exception as e:
                print("erro:", e)

        await asyncio.sleep(60)

asyncio.run(run())
