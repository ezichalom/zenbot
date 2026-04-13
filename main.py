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
# 🧠 BANCO
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
    "tag heuer WAZ1110",
    "tag heuer WAZ1112",
    "tag heuer CAZ1010",
    "tag heuer formula 1",
    "bvlgari aluminium AL38",
    "AL38TA",
    "Omega Speedmaster 3513",
    "WAZ1110",
    "AL38"
]

BAD_WORDS = ["belt", "strap", "ベルト", "band", "バンド", "部品"]

# =========================
# 💰 PREÇO
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
# 🎯 FILTRO
# =========================
def is_valid(title, price_jpy):
    t = title.lower()

    if any(b in t for b in BAD_WORDS):
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
# FETCH
# =========================
def fetch(url):
    try:
        res = requests.get(url, timeout=10)
        if res.status_code == 200:
            return res.text
    except:
        pass
    return None

def fetch_zyte(url):
    try:
        res = requests.post(
            "https://api.zyte.com/v1/extract",
            auth=(ZYTE_API_KEY, ""),
            json={"url": url, "browserHtml": True},
            timeout=20
        )
        return res.json().get("browserHtml")
    except:
        return None

# =========================
# 🔥 VALIDAÇÃO MERCARI
# =========================
def is_available_mercari(item_id):
    try:
        html = fetch_zyte(f"https://jp.mercari.com/item/{item_id}")
        if not html:
            return False

        text = html.lower()

        if any(word in text for word in ["sold", "売り切れ", "売切", "在庫なし"]):
            return False

        return True
    except:
        return False

# =========================
# 🔥 MERCARI
# =========================
def scrape_mercari(keyword):
    print("🔎 Buscando:", keyword)

    html = fetch_zyte(f"https://jp.mercari.com/search?keyword={keyword}&sort=created_time&order=desc")

    if not html:
        print("❌ Zyte falhou")
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for card in soup.find_all("a", href=True):
        if "/item/" not in card["href"]:
            continue

        try:
            title = card.get_text(strip=True)
            if not title:
                continue

            block = card.parent.get_text(" ", strip=True)

            if any(word in block for word in ["SOLD", "売り切れ", "売切"]):
                continue

            price_jpy = parse_price(block)

            if not is_valid(title, price_jpy):
                continue

            item_id = card["href"].split("/")[-1]

            # 🚨 TESTE (VALIDAÇÃO DESATIVADA)
            # if not is_available_mercari(item_id):
            #     continue

            items.append({
                "id": "mercari_" + item_id,
                "title": title,
                "price": convert_price(price_jpy),
                "link": f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={item_id}"
            })

        except:
            continue

    print("✅ Encontrados:", len(items))
    return items[:5]

# =========================
# 🔥 YAHOO
# =========================
def scrape_yahoo(keyword):
    print("🔥 Yahoo:", keyword)

    html = fetch(f"https://auctions.yahoo.co.jp/search/search?p={keyword}")
    if not html:
        return []

    soup = BeautifulSoup(html, "html.parser")
    items = []

    for li in soup.select("li.Product"):
        try:
            title = li.select_one("h3").get_text(strip=True)

            if not ("1日" in li.text or "時間" in li.text):
                continue

            href = li.select_one("a").get("href")
            auction_id = href.split("/")[-1]

            price_jpy = parse_price(li.text)

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

    print("✅ Yahoo encontrados:", len(items))
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
    await bot.send_message(
        chat_id=CHAT_ID,
        text=msg,
        disable_web_page_preview=True
    )

# =========================
# LOOP
# =========================
async def run():
    while True:

        for keyword in KEYWORDS:
            items = scrape_mercari(keyword)

            for item in items:
                if already_seen(item["id"]):
                    continue

                mark_seen(item["id"])

                title = translate(item["title"])[:60]

                msg = f"""⚡ COMPRA IMEDIATA

⌚ {title}
💰 {item['price']}

🛒 {item['link']}
"""

                await send(msg)

        await asyncio.sleep(20)

        for keyword in KEYWORDS:
            items = scrape_yahoo(keyword)

            for item in items:
                if already_seen(item["id"]):
                    continue

                mark_seen(item["id"])

                title = translate(item["title"])[:60]

                msg = f"""🔥 LEILÃO

⌚ {title}
💰 {item['price']}

🛒 {item['link']}
"""

                await send(msg)

        await asyncio.sleep(60)

asyncio.run(run())