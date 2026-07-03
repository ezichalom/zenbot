import os
import asyncio
import requests
from bs4 import BeautifulSoup
from telegram import Bot
from deep_translator import GoogleTranslator
import re
import sqlite3
from datetime import datetime, timezone
import random

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────
TOKEN      = os.getenv("TOKEN")
CHAT_ID    = os.getenv("CHAT_ID")

bot = Bot(token=TOKEN)

# ─────────────────────────────────────────────
# BANCO DE DADOS
# ─────────────────────────────────────────────
conn   = sqlite3.connect("seen.db")
cursor = conn.cursor()

cursor.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS auctions (
        id        TEXT PRIMARY KEY,
        title     TEXT,
        price     INTEGER,
        end_time  TEXT,
        link      TEXT,
        alerted24 INTEGER DEFAULT 0,
        alerted1  INTEGER DEFAULT 0
    )
""")
cursor.execute("""
    CREATE TABLE IF NOT EXISTS price_history (
        keyword TEXT,
        price   INTEGER,
        source  TEXT,
        ts      TEXT
    )
""")
conn.commit()

def seen(i):
    cursor.execute("SELECT 1 FROM seen WHERE id=?", (i,))
    return cursor.fetchone()

def save(i):
    cursor.execute("INSERT OR IGNORE INTO seen VALUES (?)", (i,))
    conn.commit()

def save_price_history(keyword, price, source):
    cursor.execute(
        "INSERT INTO price_history VALUES (?,?,?,?)",
        (keyword, price, source, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

def get_avg_price(keyword):
    cursor.execute(
        "SELECT AVG(price) FROM price_history WHERE keyword=?",
        (keyword,)
    )
    row = cursor.fetchone()
    return row[0] if row and row[0] else None

def save_auction(id, title, price, end_time, link):
    cursor.execute("""
        INSERT OR IGNORE INTO auctions (id, title, price, end_time, link)
        VALUES (?,?,?,?,?)
    """, (id, title, price, end_time, link))
    conn.commit()

def get_active_auctions():
    cursor.execute("SELECT id, title, price, end_time, link, alerted24, alerted1 FROM auctions")
    return cursor.fetchall()

def mark_alerted(id, field):
    cursor.execute(f"UPDATE auctions SET {field}=1 WHERE id=?", (id,))
    conn.commit()

def remove_auction(id):
    cursor.execute("DELETE FROM auctions WHERE id=?", (id,))
    conn.commit()

# ─────────────────────────────────────────────
# CONFIGURAÇÕES
# ─────────────────────────────────────────────
JPY_TO_BRL = 0.035

# Teto máximo fixo por marca (JPY)
BRAND_MAX_PRICE = {
    "tag heuer":   800_000,
    "タグホイヤー": 800_000,
    "bvlgari":     600_000,
    "ブルガリ":    600_000,
    "omega":       1_200_000,
    "オメガ":      1_200_000,
    "speedmaster": 1_200_000,
}

# Desconto mínimo para alerta de "bom valor" (% abaixo da média histórica)
GOOD_DEAL_THRESHOLD = 0.20   # 20% abaixo da média

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

# ─────────────────────────────────────────────
# HEADERS ROTATIVOS (substitui Zyte)
# ─────────────────────────────────────────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (X11; Linux x86_64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_4) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
]

def random_headers():
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Referer": "https://www.google.com/",
        "DNT": "1",
    }

# ─────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────
def parse_price(text):
    m = re.search(r"¥\s?([\d,]+)", text)
    return int(m.group(1).replace(",", "")) if m else None

def convert(p):
    if not p:
        return "N/A"
    return f"¥{p:,}  (~R$ {int(p * JPY_TO_BRL):,})"

def translate(t):
    try:
        return GoogleTranslator(source="auto", target="pt").translate(t)
    except:
        return t

def get_brand(title):
    t = title.lower()
    for brand in BRAND_MAX_PRICE:
        if brand in t:
            return brand
    return None

def is_above_max_price(title, price):
    brand = get_brand(title)
    if brand and price > BRAND_MAX_PRICE[brand]:
        return True
    return False

def good_deal_flags(keyword, price):
    """Retorna (is_below_avg, is_below_max, pct_below_avg)"""
    avg = get_avg_price(keyword)
    is_below_avg = False
    pct = 0.0
    if avg and avg > 0:
        pct = (avg - price) / avg
        is_below_avg = pct >= GOOD_DEAL_THRESHOLD

    is_below_max = False
    for brand, max_p in BRAND_MAX_PRICE.items():
        if brand in keyword.lower():
            is_below_max = price < max_p
            break

    return is_below_avg, is_below_max, pct

def valid(title, price):
    t = title.lower()
    if any(b in t for b in BAD_WORDS):
        return False
    if not any(x in t for x in ["watch", "時計", "腕時計"]):
        return False
    if not price:
        return False
    if price < 20_000:
        return False
    if is_above_max_price(title, price):
        return False
    return True

def fetch(url, use_headers=False, retries=2):
    for _ in range(retries):
        try:
            headers = random_headers() if use_headers else {}
            r = requests.get(url, headers=headers, timeout=12)
            if r.status_code == 200:
                return r.text
        except:
            pass
        asyncio.get_event_loop().run_until_complete(asyncio.sleep(2))
    return None

# ─────────────────────────────────────────────
# TELEGRAM — MENSAGENS
# ─────────────────────────────────────────────
async def send_new_item(item, src, keyword):
    is_below_avg, is_below_max, pct = good_deal_flags(keyword, item["price"])

    deal_tag = ""
    if is_below_avg and is_below_max:
        deal_tag = f"🟢 BOM NEGÓCIO — {int(pct*100)}% abaixo da média E dentro do teto!\n"
    elif is_below_avg:
        deal_tag = f"🟡 {int(pct*100)}% abaixo da média histórica\n"
    elif is_below_max:
        deal_tag = "🟡 Dentro do teto máximo por marca\n"

    msg = (
        f"🔔 NOVO ANÚNCIO ({src})\n"
        f"{deal_tag}"
        f"\n⌚ {translate(item['title'])[:70]}\n"
        f"💰 {convert(item['price'])}\n"
        f"🔗 {item['link']}\n"
    )
    await bot.send_message(chat_id=CHAT_ID, text=msg)

async def send_auction_ending(item_row, hours_left):
    id_, title, price, end_time, link, *_ = item_row
    emoji = "🚨" if hours_left <= 1 else "⏰"
    msg = (
        f"{emoji} LEILÃO ENCERRANDO EM {hours_left}H\n"
        f"\n⌚ {translate(title)[:70]}\n"
        f"💰 Lance atual: {convert(price)}\n"
        f"🕐 Fim: {end_time}\n"
        f"🔗 {link}\n"
    )
    await bot.send_message(chat_id=CHAT_ID, text=msg)

# ─────────────────────────────────────────────
# SCRAPING — MERCARI (sem Zyte)
# ─────────────────────────────────────────────
def mercari(k, sort):
    url  = f"https://jp.mercari.com/search?keyword={k}&sort={sort}&order=desc"
    html = fetch(url, use_headers=True)
    if not html:
        return []

    soup  = BeautifulSoup(html, "html.parser")
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
                "id":    item_id,
                "title": title,
                "price": price,
                "link":  f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={item_id}"
            })
        except:
            continue

    return items[:5]

# ─────────────────────────────────────────────
# SCRAPING — YAHOO AUCTIONS
# ─────────────────────────────────────────────
def parse_yahoo_end_time(text):
    """Tenta extrair data/hora de fim do bloco de texto do item."""
    # Formato comum: "残り X日" (dias restantes) ou "DD月DD日 HH:MM"
    m = re.search(r"(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})", text)
    if m:
        now  = datetime.now(timezone.utc)
        year = now.year
        month, day, hour, minute = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
        dt   = datetime(year, month, day, hour, minute, tzinfo=timezone.utc)
        return dt.isoformat()

    # Fallback: "残り Xd Yh" → calcula aproximado
    m2 = re.search(r"残り\s*(\d+)\s*日", text)
    if m2:
        from datetime import timedelta
        days = int(m2.group(1))
        dt   = datetime.now(timezone.utc) + timedelta(days=days)
        return dt.isoformat()

    return None

def yahoo(k):
    html = fetch(f"https://auctions.yahoo.co.jp/search/search?p={k}", use_headers=True)
    if not html:
        return []

    soup  = BeautifulSoup(html, "html.parser")
    items = []

    # Tenta seletor principal; fallback para <div class="Product">
    candidates = soup.select("li.Product") or soup.select("div.Product")

    for li in candidates:
        try:
            h3 = li.select_one("h3") or li.select_one(".Product__title")
            if not h3:
                continue
            title = h3.get_text(strip=True)
            text  = li.get_text(" ", strip=True)
            price = parse_price(text)

            if not valid(title, price):
                continue

            a    = li.select_one("a")
            link = a.get("href") if a else ""
            item_id = link.rstrip("/").split("/")[-1]

            end_time = parse_yahoo_end_time(text)

            items.append({
                "id":       item_id,
                "title":    title,
                "price":    price,
                "end_time": end_time,
                "link":     f"https://zenmarket.jp/pt/auction.aspx?itemCode={item_id}"
            })
        except:
            continue

    return items[:5]

# ─────────────────────────────────────────────
# LOOPS PRINCIPAIS
# ─────────────────────────────────────────────
async def mercari_loop():
    while True:
        for k in KEYWORDS:
            for sort in ("created_time", "price"):
                items = mercari(k, sort)
                for i in items:
                    if seen(i["id"]):
                        continue
                    save(i["id"])
                    save_price_history(k, i["price"], "mercari")
                    await send_new_item(i, "MERCARI", k)
                await asyncio.sleep(3)   # pausa entre requests
        await asyncio.sleep(120)


async def yahoo_loop():
    while True:
        for k in KEYWORDS:
            items = yahoo(k)
            for i in items:
                if seen(i["id"]):
                    # Mesmo já visto, atualiza preço no histórico (lances sobem)
                    save_price_history(k, i["price"], "yahoo")
                    continue
                save(i["id"])
                save_price_history(k, i["price"], "yahoo")
                if i.get("end_time"):
                    save_auction(i["id"], i["title"], i["price"], i["end_time"], i["link"])
                await send_new_item(i, "YAHOO", k)
            await asyncio.sleep(3)
        await asyncio.sleep(10_800)


async def auction_monitor_loop():
    """Verifica leilões salvos e alerta faltando 24h e 1h para o fim."""
    while True:
        now      = datetime.now(timezone.utc)
        auctions = get_active_auctions()

        for row in auctions:
            id_, title, price, end_time_str, link, alerted24, alerted1 = row

            if not end_time_str:
                continue

            try:
                end_dt   = datetime.fromisoformat(end_time_str)
                hours_left = (end_dt - now).total_seconds() / 3600

                if hours_left < 0:
                    remove_auction(id_)
                    continue

                if hours_left <= 1 and not alerted1:
                    await send_auction_ending(row, 1)
                    mark_alerted(id_, "alerted1")

                elif hours_left <= 24 and not alerted24:
                    await send_auction_ending(row, 24)
                    mark_alerted(id_, "alerted24")

            except Exception:
                continue

        await asyncio.sleep(1_800)   # verifica a cada 30 min


# ─────────────────────────────────────────────
# ENTRY POINT
# ─────────────────────────────────────────────
async def main():
    await asyncio.gather(
        mercari_loop(),
        yahoo_loop(),
        auction_monitor_loop(),
    )

asyncio.run(main())