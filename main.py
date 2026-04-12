import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from deep_translator import GoogleTranslator

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)
seen = set()

KEYWORDS = ["nike dunk", "jordan 1", "rolex", "seiko"]

HEADERS = {
    "User-Agent": "Mozilla/5.0"
}

def scrape_mercari(keyword):
    url = f"https://jp.mercari.com/search?keyword={keyword}"
    res = requests.get(url, headers=HEADERS)
    soup = BeautifulSoup(res.text, "html.parser")

    items = []

    for a in soup.select("a"):
        href = a.get("href", "")

        if "/item/" in href:
            title = a.text.strip()
            full_url = f"https://jp.mercari.com{href}"

            item_id = href.split("/")[-1]

            if title:
                items.append({
                    "id": item_id,
                    "title": title,
                    "url": full_url
                })

    return items[:5]

def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text

async def run():
    while True:
        for keyword in KEYWORDS:
            try:
                items = scrape_mercari(keyword)

                for item in items:
                    if item["id"] in seen:
                        continue

                    seen.add(item["id"])

                    translated = translate(item["title"])

                    msg = f"""
🔥 NOVO ITEM

🔍 {keyword}
📝 {translated}

🔗 {item['url']}
"""

                    await bot.send_message(chat_id=CHAT_ID, text=msg)

            except Exception as e:
                print("erro:", e)

        await asyncio.sleep(60)

asyncio.run(run())

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                translated = translate(item["title"])

                msg = f"""
🔥 NOVO ITEM

🔍 {keyword}
📝 {translated}
💴 {item['price']}

🔗 {item['url']}
"""

                await bot.send_message(chat_id=CHAT_ID, text=msg)

        await asyncio.sleep(60)

asyncio.run(run())
