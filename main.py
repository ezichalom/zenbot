import requests
import time
import os
from deep_translator import GoogleTranslator
from telegram import Bot

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

def load_keywords():
    with open("keywords.txt", "r") as f:
        return [line.strip() for line in f.readlines()]

seen = set()

def search(keyword):
    url = f"https://jp.mercari.com/search?keyword={keyword}"
    return [{"id": f"{keyword}-{int(time.time())}", "name": keyword, "price": "test", "url": url}]

def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text

def send(msg):
    bot.send_message(chat_id=CHAT_ID, text=msg)

def run():
    while True:
        keywords = load_keywords()

        for keyword in keywords:
            items = search(keyword)

            for item in items:
                if item["id"] in seen:
                    continue

                seen.add(item["id"])

                translated = translate(item["name"])

                msg = f"""
🔥 NOVO ITEM

🔍 {keyword}
📝 {translated}
💴 {item['price']}

🔗 {item['url']}
"""

                send(msg)

        time.sleep(60)

if __name__ == "__main__":
    run()
