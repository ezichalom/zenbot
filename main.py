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

def already_seen(item_id):
    cursor.execute("SELECT 1 FROM seen WHERE id=?", (item_id,))
    return cursor.fetchone() is not None

def mark_seen(item_id):
    cursor.execute("INSERT OR IGNORE INTO seen (id) VALUES (?)", (item_id,))
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
    "belt", "strap", "ベルト",
    "band", "バンド",
    "部品", "parts", "part",
    "box", "case", "empty",
    "空箱", "箱のみ",
    "manual", "冊子",
    "only", "のみ"
]

# =========================
# PREÇO
# =========================
def parse_price(text):
    jpy = re.search(r"¥\s?([\d,]+)", text)
    if jpy:
        return int(jpy.group(1).replace(",", ""))
    return None

def convert_price(jpy):
    if not jpy:
        return "N/A"
    brl = int(jpy * JPY_TO_BRL)
    return f"¥{jpy:,} (~R$ {brl:,})"

# =========================
# FILTRO
# =========================
def is_valid(title, price_jpy):
    t = title.lower()

    # lixo
    if any(b in t for b in BAD_WORDS):
        return False

    # precisa ser relógio
    if not any(x in t for x in ["watch", "腕時計", "時計"]):
        return False

    if not price_jpy:
        return False

    brl = price_jpy * JPY_TO_BRL

    if "tag heuer" in t or "タグホイヤー" in t:
        return brl <= 4500

    if "bvlgari" in t or "ブルガリ" in t:
        return brl <= 4100

    return brl <= 6800

# =========================
# ZEN CHECK
# =========================
def zenmarket_online():
    try:
        requests.get("https://zenmarket.jp", timeout=5)
        return True
    except:
        return False

async def wait_for_zen():
    while True:
        if zenmarket_online():
            print("✅ Zen voltou")
            return
        print("⏳ Zen OFF...")
        await asyncio.sleep(60)

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
# MERCARI SNIPER
# =========================
def scrape_mercari_sniper(keyword):
    html = fetch_zyte(f"https://jp.mercari.com/search?keyword={keyword}&sort=created_time&order=desc")
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

            price_jpy = parse_price(block)

            if not is_valid(title, price_jpy):
                continue

            item_id = a["href"].split("/")[-1]

            items.append({
                "id": "mercari_" + item_id,
                "title": title,
                "price": convert_price(price_jpy),
                "link": f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={item_id}"
            })

        except:
            continue

    return items[:5]

# =========================
# MERCARI SCANNER
# =========================
def scrape_mercari_scanner(keyword):
    html = fetch_zyte(f"https://jp.mercari.com/search?keyword={keyword}&sort=price&order=asc")
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

            price_jpy = parse_price(block)

            if not is_valid(title, price_jpy):
                continue

            item_id = a["href"].split("/")[-1]

            items.append({
                "id": "scan_" + item_id,
                "title": title,
                "price": convert_price(price_jpy),
                "link": f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={item_id}"
            })

        except:
            continue

    return items[:5]

# =========================
# YAHOO
# =========================
def scrape_yahoo(keyword):
    html = fetch(f"https://auctions.yahoo.co.jp/search/search?p={keyword}")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select("li.Product"):
        try:
            title = li.select_one("h3")
            if not title:
                continue

            title = title.get_text(strip=True)
            text = li.get_text()

            href = li.select_one("a").get("href")
            auction_id = href.split("/")[-1]

            price_jpy = parse_price(text)

            if not is_valid(title, price_jpy):
                continue

            items.append({
                "id": "yahoo_" + auction_id,
                "title": title,
                "price": convert_price(price_jpy),
                "link": f"https://zenmarket.jp/pt/auction.aspx?itemCode={auction_id}"
            })

        except:
            continue

    return items[:5]

# =========================
# TRADUÇÃO
# =========================
def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except:
        return text

# =========================
# ENVIO
# =========================
async def send(msg):
    await bot.send_message(chat_id=CHAT_ID, text=msg, disable_web_page_preview=True)

# =========================
# LOOPS
# =========================
async def mercari_sniper_loop():
    while True:
        if not zenmarket_online():
            await wait_for_zen()
            continue

        for keyword in KEYWORDS:
            items = scrape_mercari_sniper(keyword)

            for item in items:
                if already_seen(item["id"]):
                    continue

                mark_seen(item["id"])

                await send(f"⚡ {translate(item['title'])[:60]}\n💰 {item['price']}\n{item['link']}")

        await asyncio.sleep(300)

async def mercari_scanner_loop():
    while True:
        if not zenmarket_online():
            await wait_for_zen()
            continue

        for keyword in KEYWORDS:
            items = scrape_mercari_scanner(keyword)

            for item in items:
                if already_seen(item["id"]):
                    continue

                mark_seen(item["id"])

                await send(f"🧠 {translate(item['title'])[:60]}\n💰 {item['price']}\n{item['link']}")

        await asyncio.sleep(120)  # 2 minutos

async def yahoo_loop():
    while True:
        if not zenmarket_online():
            await wait_for_zen()
            continue

        for keyword in KEYWORDS:
            items = scrape_yahoo(keyword)

            for item in items:
                if already_seen(item["id"]):
                    continue

                mark_seen(item["id"])

                await send(f"🔥 {translate(item['title'])[:60]}\n💰 {item['price']}\n{item['link']}")

        await asyncio.sleep(10800)

# =========================
# MAIN
# =========================
async def main():
    await asyncio.gather(
        mercari_sniper_loop(),
        mercari_scanner_loop(),
        yahoo_loop()
    )

asyncio.run(main())