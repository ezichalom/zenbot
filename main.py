import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from deep_translator import GoogleTranslator
import re
import sqlite3

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
ZYTE_API_KEY = os.getenv("ZYTE_API_KEY")

bot = Bot(token=TOKEN)

conn = sqlite3.connect("seen.db")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
conn.commit()

def seen(i):
    cursor.execute("SELECT 1 FROM seen WHERE id=?", (i,))
    return cursor.fetchone()

def save(i):
    cursor.execute("INSERT OR IGNORE INTO seen VALUES (?)", (i,))
    conn.commit()

JPY_TO_BRL = 0.035

KEYWORDS = [
    "tag heuer","タグホイヤー","waz","caz",
    "bvlgari","ブルガリ","al38",
    "omega","オメガ","speedmaster","3513"
]

BAD_WORDS = [
    "belt","strap","ベルト","band","バンド",
    "parts","部品","box","case","empty","箱のみ",
    "manual","冊子","only","のみ",
    "pen","seed","card","book","reading",
    "pokemon","yugioh","toy","figure"
]

def parse_price(text):
    m = re.search(r"¥\s?([\d,]+)", text)
    return int(m.group(1).replace(",", "")) if m else None

def convert(p):
    if not p:
        return "N/A"
    return f"¥{p:,} (~R$ {int(p*JPY_TO_BRL):,})"

def valid(title, price):
    t = title.lower()

    if any(b in t for b in BAD_WORDS):
        return False

    if not any(x in t for x in ["watch","時計","腕時計"]):
        return False

    if not price:
        return False

    if price < 20000:
        return False

    return True

def translate(t):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(t)
    except:
        return t

def fetch_zyte(url):
    try:
        r = requests.post(
            "https://api.zyte.com/v1/extract",
            auth=(ZYTE_API_KEY,""),
            json={"url":url,"browserHtml":True},
            timeout=15
        )
        return r.json().get("browserHtml")
    except:
        return None

def fetch(url):
    try:
        r = requests.get(url, timeout=10)
        return r.text if r.status_code == 200 else None
    except:
        return None

def mercari(k, sort):
    html = fetch_zyte(f"https://jp.mercari.com/search?keyword={k}&sort={sort}&order=desc")
    if not html:
        return []

    soup = BeautifulSoup(html,"html.parser")
    items=[]

    for a in soup.find_all("a",href=True):
        if "/item/" not in a["href"]:
            continue

        try:
            title=a.get_text(strip=True)
            block=a.parent.get_text(" ",strip=True)

            if "SOLD" in block or "売り切れ" in block:
                continue

            price=parse_price(block)

            if not valid(title,price):
                continue

            id=a["href"].split("/")[-1]

            items.append({
                "id":id,
                "title":title,
                "price":price,
                "link":f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={id}"
            })

        except:
            continue

    return items[:5]

def yahoo(k):
    html=fetch(f"https://auctions.yahoo.co.jp/search/search?p={k}")
    if not html:
        return []

    soup=BeautifulSoup(html,"html.parser")
    items=[]

    for li in soup.select("li.Product"):
        try:
            title=li.select_one("h3").get_text(strip=True)
            text=li.get_text()
            price=parse_price(text)

            if not valid(title,price):
                continue

            link=li.select_one("a").get("href")
            id=link.split("/")[-1]

            items.append({
                "id":id,
                "title":title,
                "price":price,
                "link":f"https://zenmarket.jp/pt/auction.aspx?itemCode={id}"
            })

        except:
            continue

    return items[:5]

async def send(i,src):
    msg=f"""🔥 OPORTUNIDADE ({src})

⌚ {translate(i['title'])[:70]}

💰 {convert(i['price'])}

🔗 {i['link']}
"""
    await bot.send_message(chat_id=CHAT_ID,text=msg)

async def mercari_loop():
    while True:
        for k in KEYWORDS:
            items=mercari(k,"created_time")+mercari(k,"price")

            for i in items:
                if seen(i["id"]):
                    continue
                save(i["id"])
                await send(i,"MERCARI")

        await asyncio.sleep(120)

async def yahoo_loop():
    while True:
        for k in KEYWORDS:
            items=yahoo(k)

            for i in items:
                if seen(i["id"]):
                    continue
                save(i["id"])
                await send(i,"YAHOO")

        await asyncio.sleep(10800)

async def main():
    await asyncio.gather(
        mercari_loop(),
        yahoo_loop()
    )

asyncio.run(main())