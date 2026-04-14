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

# =========================
# BANCO
# =========================
conn = sqlite3.connect("seen.db")
cursor = conn.cursor()
cursor.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
conn.commit()

def seen(item_id):
    cursor.execute("SELECT 1 FROM seen WHERE id=?", (item_id,))
    return cursor.fetchone()

def save(item_id):
    cursor.execute("INSERT OR IGNORE INTO seen VALUES (?)", (item_id,))
    conn.commit()

# =========================
# CONFIG
# =========================
JPY_TO_BRL = 0.035

KEYWORDS = [
    "tag heuer", "タグホイヤー", "WAZ1110", "WAZ1112", "CAZ1010",
    "bvlgari", "ブルガリ", "al38", "al38ta",
    "omega", "オメガ", "speedmaster", "3513"
]

BAD_WORDS = [
    "belt","strap","ベルト","band","バンド",
    "parts","部品","box","case","empty","箱のみ",
    "manual","冊子","only","のみ",
    "pen","seed","card","book","reading",
    "pokemon","yugioh","toy","figure"
]

MODEL_WORDS = ["al38","waz","caz","3513","speedmaster"]

# =========================
# FUNÇÕES
# =========================
def parse_price(text):
    jpy = re.search(r"¥\s?([\d,]+)", text)
    if jpy:
        return int(jpy.group(1).replace(",", ""))
    return None

def convert(price):
    if not price:
        return "Preço não encontrado"
    brl = int(price * JPY_TO_BRL)
    return f"¥{price:,} (~R$ {brl:,})"

def valid(title, price):
    t = title.lower()

    # lixo
    if any(b in t for b in BAD_WORDS):
        return False

    # precisa ter modelo relevante
    if not any(m in t for m in MODEL_WORDS):
        return False

    # precisa ter preço
    if not price:
        return False

    # corta lixo barato
    if price < 20000:
        return False

    return True

def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text

# =========================
# FETCH
# =========================
def fetch_zyte(url):
    try:
        res = requests.post(
            "https://api.zyte.com/v1/extract",
            auth=(ZYTE_API_KEY, ""),
            json={"url": url, "browserHtml": True},
            timeout=15
        )
        return res.json().get("browserHtml")
    except:
        return None

def fetch(url):
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.text
    except:
        return None

# =========================
# MERCARI
# =========================
def mercari(keyword, sort):
    url = f"https://jp.mercari.com/search?keyword={keyword}&sort={sort}&order=desc"
    html = fetch_zyte(url)

    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for a in soup.find_all("a", href=True):
        if "/item/" not in a["href"]:
            continue

        try:
            title = a.get_text(strip=True)
            block = a.parent.get_text(" ", strip=True)

            if "SOLD" in block or "売り切れ" in block:
                continue

            price = parse_price(block)

            if not valid(title, price):
                continue

            item_id = a["href"].split("/")[-1]

            items.append({
                "id": item_id,
                "title": title,
                "price": price,
                "link": f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={item_id}"
            })

        except:
            continue

    return items[:5]

# =========================
# YAHOO
# =========================
def yahoo(keyword):
    html = fetch(f"https://auctions.yahoo.co.jp/search/search?p={keyword}")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select("li.Product"):
        try:
            title = li.select_one("h3").get_text(strip=True)
            text = li.get_text()

            price = parse_price(text)

            if not valid(title, price):
                continue

            link = li.select_one("a").get("href")
            item_id = link.split("/")[-1]

            items.append({
                "id": item_id,
                "title": title,
                "price": price,
                "link": f"https://zenmarket.jp/pt/auction.aspx?itemCode={item_id}"
            })

        except:
            continue

    return items[:5]

# =========================
# ENVIO
# =========================
async def send(item, tipo):
    title = translate(item["title"])[:70]
    price = convert(item["price"])

    msg = f"""🔥 OPORTUNIDADE ({tipo})

⌚ {title}

💰 {price}

🔗 {item['link']}
"""
    await bot.send_message(chat_id=CHAT_ID, text=msg)

# =========================
# LOOPS
# =========================
async def mercari_loop():
    while True:
        for k in KEYWORDS:
            # SNIPER
            items = mercari(k, "created_time")

            # SCANNER
            items += mercari(k, "price")

            for item in items:
                if seen(item["id"]):
                    continue

                save(item["id"])
                await send(item, "MERCARI")

        await asyncio.sleep(120)  # 2 min

async def yahoo_loop():
    while True:
        for k in KEYWORDS:
            items = yahoo(k)

            for item in items:
                if seen(item["id"]):
                    continue

                save(item["id"])
                await send(item, "YAHOO")

        await asyncio.sleep(10800)  # 3h

# =========================
# MAIN
# =========================
async def main():
    await asyncio.gather(
        mercari_loop(),
        yahoo_loop()
    )

asyncio.run(main())