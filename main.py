import os
import asyncio
import requests
from telegram import Bot
from deep_translator import GoogleTranslator

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

KEYWORDS = ["nike dunk", "jordan 1", "rolex", "seiko"]

seen = set()

def search(keyword):
    url = f"https://jp.mercari.com/search?keyword={keyword}"
    
    # simulação melhor (link real)
    return [{
        "id": f"{keyword}",
        "title": keyword,
        "price": "???",
        "url": url
    }]

def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text

async def run():
    while True:
        for keyword in KEYWORDS:
            items = search(keyword)

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
