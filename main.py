"""
ZenScrapperBot — v2 (API stream do ZenMarket)
==============================================
Substitui o scraping de HTML (Mercari + Yahoo Auctions) pela API interna
de busca do ZenMarket descoberta via engenharia reversa em 03/07/2026:

    POST https://zenmarket.jp/pt/search.aspx?stream=1  →  SSE (store-result)

Vantagens sobre a v1 (preservada em main_legacy.py):
  - JSON estruturado: título, preço, URL, imagem, vendedor — sem BeautifulSoup
  - Dados de leilão NATIVOS do Yahoo: Bids, BuyoutPrice, EndTime exato (+09:00)
    → aposenta o parse_yahoo_end_time() aproximado
  - 1 request cobre Mercari + Yahoo Auctions de uma vez
  - Menos requests/ciclo = menos risco de bloqueio

Toda a lógica de negócio da v1 foi mantida:
  SQLite (seen/auctions/price_history), tetos por marca, alerta de bom
  negócio (% abaixo da média), BAD_WORDS, tradução PT, alertas 24h/1h.
"""

import os
import asyncio
import logging
import re
import sqlite3
from datetime import datetime, timezone

from telegram import Bot
from deep_translator import GoogleTranslator

from zenmarket_stream import search as zen_search, STORE

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("zenbot")

# ─────────────────────────────────────────────
# ENV
# ─────────────────────────────────────────────
TOKEN   = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")

# Intervalo entre ciclos completos de busca (segundos).
# 300s = 5 min. Configurável via variável de ambiente no Railway.
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", "600"))  # 10 min — economiza CPU no Railway

bot = Bot(token=TOKEN)

# ─────────────────────────────────────────────
# BANCO DE DADOS (idêntico à v1)
# ─────────────────────────────────────────────
conn   = sqlite3.connect("seen.db")
cursor = conn.cursor()

cursor.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS seen_content (fp TEXT PRIMARY KEY)")
cursor.execute("CREATE TABLE IF NOT EXISTS sku_prices (uid TEXT PRIMARY KEY, price INTEGER)")
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
    CREATE TABLE IF NOT EXISTS watch_prices (
        sku        TEXT PRIMARY KEY,
        last_price INTEGER
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

import hashlib as _hashlib

def content_fingerprint(title, price):
    """Impressão digital do anúncio para deduplicar o MESMO relógio
    anunciado em várias lojas (Rakuten/Rakuma/Mercari costumam repetir).

    - título normalizado: minúsculo, só letras/números (remove espaços,
      pontuação e quebras que variam entre plataformas)
    - preço arredondado pra faixa de ¥1.000 (pequenas diferenças de taxa
      entre lojas não quebram o match)
    """
    t = "".join(ch for ch in (title or "").lower() if ch.isalnum())
    faixa = (price or 0) // 1000
    base = f"{t}|{faixa}"
    return _hashlib.md5(base.encode("utf-8")).hexdigest()

def seen(i):
    cursor.execute("SELECT 1 FROM seen WHERE id=?", (i,))
    return cursor.fetchone()

def save(i):
    cursor.execute("INSERT OR IGNORE INTO seen VALUES (?)", (i,))
    conn.commit()

def seen_content_fp(fp):
    cursor.execute("SELECT 1 FROM seen_content WHERE fp=?", (fp,))
    return cursor.fetchone()

def save_content_fp(fp):
    cursor.execute("INSERT OR IGNORE INTO seen_content VALUES (?)", (fp,))
    conn.commit()

def get_sku_price(uid):
    cursor.execute("SELECT price FROM sku_prices WHERE uid=?", (uid,))
    row = cursor.fetchone()
    return row[0] if row else None

def set_sku_price(uid, price):
    cursor.execute("INSERT OR REPLACE INTO sku_prices VALUES (?,?)", (uid, price))
    conn.commit()

def save_price_history(keyword, price, source):
    cursor.execute(
        "INSERT INTO price_history VALUES (?,?,?,?)",
        (keyword, price, source, datetime.now(timezone.utc).isoformat())
    )
    conn.commit()

def get_avg_price(keyword):
    cursor.execute("SELECT AVG(price) FROM price_history WHERE keyword=?", (keyword,))
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
# CONFIGURAÇÕES (idênticas à v1)
# ─────────────────────────────────────────────
JPY_TO_BRL = 0.035

# Tetos definidos em REAIS (R$) e convertidos para ienes automaticamente.
# Para ajustar no futuro: mude só o valor em R$ aqui embaixo.
BRAND_MAX_PRICE_BRL = {
    "tag heuer":   3_950,
    "タグホイヤー": 3_950,
    "bvlgari":     4_500,
    "ブルガリ":    4_500,
    "omega":       8_000,
    "オメガ":      8_000,
    "speedmaster": 8_000,
}
BRAND_MAX_PRICE = {b: int(v / JPY_TO_BRL) for b, v in BRAND_MAX_PRICE_BRL.items()}

GOOD_DEAL_THRESHOLD = 0.20   # 20% abaixo da média histórica

KEYWORDS = [
    # Tag Heuer — só Formula 1
    "タグホイヤー フォーミュラ1","tag heuer formula 1",
    "tag heuer waz","tag heuer caz","waz1112","waz1110","caz1010",
    # Bvlgari — sempre MARCA + referência (evita código de peça e porcaria)
    "bvlgari al38","bvlgari ac38","bvlgari sd38",
    "bvlgari diagono","bvlgari aluminium","ブルガリ アルミニウム",
    # Omega — DESATIVADO a pedido do Ezi (remova os # para reativar)
    # "omega","オメガ","speedmaster","3513",
]

BAD_WORDS = [
    "parts","部品","box","case","empty","箱のみ",
    "manual","冊子","only","のみ",
    "pen","seed","card","book","reading",
    "pokemon","yugioh","toy","figure",
    "ムーブメント","movement","リューズ",
    "al29","al32",
    # Relógios femininos / boys (títulos originais em japonês — "feminino"
    # só aparece depois da tradução, por isso bloqueamos os termos de origem):
    "レディース","レディス","ladies","lady's","ladys","女性用","婦人",
    "ボーイズ","boys",
    # Não-relógios / linhas indesejadas que colavam pela marca:
    "óculos","oculos","sunglass","sunglasses","メガネ","眼鏡","サングラス",
    "conectado","connected","スマートウォッチ","smartwatch",
    "strass","ネックレス","necklace","指輪","ring","earring","ピアス","イヤリング",
    "bag","バッグ","財布","wallet","香水","perfume","キーホルダー",   # tamanhos Bvlgari que o Ezi não trabalha (feminino/boys)
    "omega","オメガ","speedmaster",  # Omega 100% DESATIVADO — apague esta linha para reativar
]

# Lojas monitoradas via API stream
# Todas as lojas disponíveis (IDs descobertos na engenharia reversa).
# Para parar de monitorar alguma, comente a linha correspondente.
MONITORED_STORES = [
    STORE["Rakuten"],       # 0
    STORE["YahooShopping"], # 18
    STORE["Rakuma"],        # 25
    STORE["ZenPlus"],       # 26
    STORE["Mercari"],       # 27
    STORE["YahooAuction"],  # 28
    STORE["SnkrDunk"],      # 53
    STORE["Ragtag"],        # 57
    STORE["BrandOff"],      # 63
]

# ─────────────────────────────────────────────
# UTILITÁRIOS
# ─────────────────────────────────────────────
def convert(p):
    if not p:
        return "N/A"
    return f"¥{p:,}  (~R$ {int(p * JPY_TO_BRL):,})"

def translate(t):
    try:
        return GoogleTranslator(source="auto", target="pt").translate(t)
    except Exception:
        return t

# Referência no título já identifica a marca — assim o teto se aplica
# mesmo quando o vendedor não escreve o nome da marca (ex.: "CAZ1010 クロノ").
BRAND_PATTERNS = {
    "tag heuer": ["タグホイヤー","waz","caz","formula","フォーミュラ"],
    "bvlgari":   ["ブルガリ","al38","ac38","sd38",
                  "aluminium","アルミニウム","diagono","ディアゴノ","dg"],
}

def get_brand(title):
    for brand, tokens in BRAND_PATTERNS.items():
        if token_in(title, tokens):
            return brand
    return None

def is_above_max_price(title, price):
    brand = get_brand(title)
    return bool(brand and price > BRAND_MAX_PRICE[brand])

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

# O título PRECISA conter pelo menos um destes termos (marca ou referência
# do radar do Ezi). Substitui o antigo filtro de "palavra de relógio", que
# barrava Tags legítimos (títulos japoneses sem 時計) e deixava passar
# relógio aleatório de outras marcas.

import re as _re

def _token_regex(tok):
    """Casa token respeitando fronteiras: nada de letra/número latino antes;
    se o token termina em dígito, nada de dígito depois (letras OK: AL38TA)."""
    esc = _re.escape(tok.lower())
    prefix = r"(?<![a-z0-9])"
    suffix = r"(?![0-9])" if tok[-1].isdigit() else ""
    return _re.compile(prefix + esc + suffix)

_MATCH_CACHE = {}

def token_in(text, tokens):
    t = text.lower()
    for tok in tokens:
        rx = _MATCH_CACHE.get(tok)
        if rx is None:
            rx = _MATCH_CACHE[tok] = _token_regex(tok)
        if rx.search(t):
            return True
    return False

MUST_HAVE = [
    # Tag Heuer — SOMENTE Formula 1 (WAZ/CAZ). "tag heuer" sozinho não basta,
    # para cortar Carrera/Aquaracer/Professional(WG)/Connected.
    "waz","caz","formula 1","formula1","フォーミュラ",
    # Bvlgari
    "bvlgari","ブルガリ","al38","ac38","sd38",
    "aluminium","アルミニウム","diagono","ディアゴノ",
]

# Termos de PULSEIRA/acessório de pulso: só bloqueiam se o anúncio NÃO tiver
# sinal forte de relógio. O Bvlgari Aluminium cita "ラバーベルト" (pulseira de
# borracha) no título — não podemos barrar o relógio por causa disso.
STRAP_WORDS = ["belt","strap","ベルト","band","バンド","尾錠","バックル","buckle"]

# Sinal forte de relógio real (marca+ref ou a palavra "relógio" em japonês):
WATCH_SIGNAL = ["al38","ac38","sd38","waz","caz","aluminium","アルミニウム",
                "diagono","ディアゴノ","腕時計","自動巻","クォーツ","デイト"]

def valid(title, description, price):
    """Filtro de qualidade: bloqueios + termo do radar + faixa de preço."""
    t = (title or "").lower()

    if any(b in t for b in BAD_WORDS):
        return False
    # Pulseira/acessório: bloqueia só se NÃO houver sinal de relógio real.
    if any(sw in t for sw in STRAP_WORDS) and not any(ws in t for ws in WATCH_SIGNAL):
        return False
    if not token_in(t, MUST_HAVE):
        return False
    if not price:
        return False
    if price < 20_000:
        return False
    if is_above_max_price(title, price):
        return False
    return True

def build_link(product):
    """Converte o produto para o link de compra dentro do ZenMarket."""
    sku = product["sku"]
    if product["storeName"] == "Mercari":
        return f"https://zenmarket.jp/pt/mercariProduct.aspx?itemCode={sku}"
    if product["storeName"] == "YahooAuction":
        return f"https://zenmarket.jp/pt/auction.aspx?itemCode={sku}"
    return product.get("url") or ""

# ─────────────────────────────────────────────
# TELEGRAM — MENSAGENS
# ─────────────────────────────────────────────
async def send_price_drop(product, old_price):
    """Alerta quando um relógio JÁ VISTO baixa de preço (qualquer queda >= 1)."""
    new_price = product["price"]
    diff = old_price - new_price
    pct = (diff / old_price * 100) if old_price else 0
    link = build_link(product)
    caption = (
        f"💸 BAIXOU DE PREÇO ({product['storeName'].upper()})\n"
        f"\n⌚ {translate(product['title'])[:70]}\n"
        f"📉 De ¥{old_price:,} por ¥{new_price:,} "
        f"(−¥{diff:,} · −{pct:.1f}%)\n"
        f"💰 Agora: {convert(new_price)}\n"
        f"🔗 {link}\n"
        f"\n━━━━━━━━━━\n"
    )
    image_url = product.get("image")
    if image_url:
        try:
            await bot.send_photo(chat_id=CHAT_ID, photo=image_url, caption=caption)
            return
        except Exception as e:
            log.warning("Falha ao enviar foto da queda (%s); só texto.", e)
    await bot.send_message(chat_id=CHAT_ID, text=caption)


async def send_new_item(product, keyword):
    price = product["price"]
    # Cálculo mantido por baixo (histórico/uso futuro), mas NÃO exibido na mensagem.
    good_deal_flags(keyword, price)

    auction_info = ""
    if product.get("bids") is not None:
        auction_info = f"🔨 Lances: {product['bids']}"
        if product.get("buyoutPrice"):
            auction_info += f" | Compra imediata: ¥{product['buyoutPrice']:,}"
        auction_info += "\n"
    if product.get("auctionEndTime"):
        auction_info += f"🕐 Fim: {product['auctionEndTime']:%d/%m %H:%M} (JST)\n"

    link = build_link(product)
    caption = (
        f"🔔 NOVO ANÚNCIO ({product['storeName'].upper()})\n"
        
        f"\n⌚ {translate(product['title'])[:70]}\n"
        
        f"💰 {convert(price)}\n"
        
        f"{auction_info}"
        
        f"🔗 {link}\n"
        f"\n━━━━━━━━━━\n"   # separador entre anúncios
    )

    # Tenta enviar com a foto do produto; se a imagem falhar, cai pra texto.
    image_url = product.get("image")
    if image_url:
        try:
            await bot.send_photo(chat_id=CHAT_ID, photo=image_url, caption=caption)
            return
        except Exception as e:
            log.warning("Falha ao enviar foto (%s); enviando só texto.", e)

    await bot.send_message(chat_id=CHAT_ID, text=caption)

async def send_auction_ending(item_row, hours_left):
    id_, title, price, end_time, link, *_ = item_row
    emoji = "🚨" if hours_left <= 1 else "⏰"
    msg = (
        f"{emoji} LEILÃO ENCERRANDO EM {hours_left}H\n"
        
        f"\n {translate(title)[:70]}\n"
        
        f"💰 Lance atual: {convert(price)}\n"
        
        f"🕐 Fim: {end_time}\n"
        
        f"🔗 {link}\n"
        f"\n━━━━━━━━━━\n"   # separador entre anúncios
    )
    await bot.send_message(chat_id=CHAT_ID, text=msg)

# ─────────────────────────────────────────────
# BUSCA VIA API STREAM (substitui mercari() e yahoo())
# ─────────────────────────────────────────────
def fetch_keyword(keyword):
    """Chamada síncrona à API stream — roda em thread pra não travar o loop.

    O filtro de preço vai DIRETO na API (minPrice/maxPrice): as 50 vagas de
    resultado voltam 100% dentro da faixa do Ezi, sem desperdiçar slot com
    relógio caro demais ou acessório barato.
    """
    brand = get_brand(keyword)
    max_p = BRAND_MAX_PRICE.get(brand) if brand else None
    return zen_search(
        keyword,
        stores=MONITORED_STORES,
        page_size=50,
        min_price=20_000,
        max_price=max_p,
    )

async def search_loop():
    """Loop unificado: 1 request por keyword cobre Mercari + Yahoo Auctions."""
    consecutive_errors = 0

    while True:
        for k in KEYWORDS:
            try:
                products = await asyncio.to_thread(fetch_keyword, k)
                consecutive_errors = 0
            except Exception as e:
                consecutive_errors += 1
                log.warning("Busca falhou para %r: %s", k, e)
                # Backoff progressivo se o Cloudflare começar a bloquear
                if consecutive_errors >= 3:
                    log.error("3 falhas seguidas — pausando 15 min (possível bloqueio).")
                    await asyncio.sleep(900)
                    consecutive_errors = 0
                continue

            for p in products:
                if not valid(p["title"], p["raw"].get("description", ""), p["price"]):
                    continue

                uid    = f'{p["storeName"]}:{p["sku"]}'
                source = p["storeName"].lower()

                if seen(uid):
                    # Leilões: lance sobe, não é "queda"; só atualiza histórico.
                    if source == "yahooauction":
                        save_price_history(k, p["price"], source)
                        continue
                    # Preço fixo já visto: compara com o último preço registrado.
                    prev = get_sku_price(uid)
                    if prev is not None and p["price"] < prev:
                        await send_price_drop(p, prev)
                        set_sku_price(uid, p["price"])
                        save_price_history(k, p["price"], source)
                        await asyncio.sleep(1)
                    elif prev is None or p["price"] != prev:
                        set_sku_price(uid, p["price"])  # registra/atualiza base
                    continue

                # Dedup por CONTEÚDO: mesmo relógio em outra loja = não repete.
                # (Leilões do Yahoo escapam da dedup: cada leilão é único e o
                #  lance muda, então continuam alertando normalmente.)
                fp = content_fingerprint(p["title"], p["price"])
                if source != "yahooauction" and seen_content_fp(fp):
                    save(uid)  # marca o sku pra não reprocessar, mas não alerta
                    continue

                save(uid)
                save_content_fp(fp)
                set_sku_price(uid, p["price"])   # base para detectar quedas futuras
                save_price_history(k, p["price"], source)

                # EndTime nativo da API → salva pro monitor de leilões
                if p.get("auctionEndTime"):
                    save_auction(
                        uid, p["title"], p["price"],
                        p["auctionEndTime"].isoformat(),
                        build_link(p),
                    )

                await send_new_item(p, k)
                await asyncio.sleep(1)   # respiro entre mensagens Telegram

            await asyncio.sleep(5)       # respiro entre keywords

        log.info("Ciclo completo. Próximo em %ss.", POLL_INTERVAL)
        await asyncio.sleep(POLL_INTERVAL)


# ─────────────────────────────────────────────
# WATCHLIST — itens específicos vigiados p/ QUEDA DE PREÇO
# (fora do funil normal: sem filtros de marca/teto; qualquer queda alerta)
# ─────────────────────────────────────────────
WATCHLIST = [
    {
        "label": "Tudor Hydronaut (Rakuma)",
        "query": "tudor hydronaut",
        "store": STORE["Rakuma"],
        "sku":   "c490e86d913735b1c55b826278c95e75",
    },
]

def get_watch_price(sku):
    cursor.execute("SELECT last_price FROM watch_prices WHERE sku=?", (sku,))
    row = cursor.fetchone()
    return row[0] if row else None

def set_watch_price(sku, price):
    cursor.execute("INSERT OR REPLACE INTO watch_prices VALUES (?,?)", (sku, price))
    conn.commit()

async def watchlist_loop():
    """Vigia os itens da WATCHLIST a cada 30 min e alerta em QUALQUER queda."""
    while True:
        for item in WATCHLIST:
            try:
                produtos = await asyncio.to_thread(
                    zen_search, item["query"],
                    stores=[item["store"]], page_size=50,
                )
            except Exception as e:
                log.warning("Watchlist: busca falhou para %r: %s", item["label"], e)
                continue

            achado = next((p for p in produtos if p["sku"] == item["sku"]), None)
            if achado is None:
                log.info("Watchlist: %s não apareceu na busca (vendido? fora do top 50?).",
                         item["label"])
                continue

            preco_atual  = achado["price"]
            preco_antigo = get_watch_price(item["sku"])

            if preco_antigo is None:
                set_watch_price(item["sku"], preco_atual)
                log.info("Watchlist: %s registrado a ¥%s.", item["label"], f"{preco_atual:,}")
            elif preco_atual < preco_antigo:
                msg = (
                    f"💸 BAIXOU DE PREÇO — {item['label']}\n"
                    f"\n⌚ {translate(achado['title'])[:70]}\n"
                    f"📉 De ¥{preco_antigo:,} por ¥{preco_atual:,} "
                    f"(−¥{preco_antigo - preco_atual:,})\n"
                    f"💰 Agora: {convert(preco_atual)}\n"
                    f"🔗 {achado.get('url') or ''}\n"
                )
                await bot.send_message(chat_id=CHAT_ID, text=msg)
                set_watch_price(item["sku"], preco_atual)
            elif preco_atual > preco_antigo:
                set_watch_price(item["sku"], preco_atual)  # subiu: atualiza base

        await asyncio.sleep(1_800)  # 30 min


# ─────────────────────────────────────────────
# MONITOR DE LEILÕES (idêntico à v1)
# ─────────────────────────────────────────────
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
                end_dt = datetime.fromisoformat(end_time_str)
                if end_dt.tzinfo is None:
                    end_dt = end_dt.replace(tzinfo=timezone.utc)
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
    log.info("ZenScrapperBot v2 iniciado — API stream | %s keywords | poll=%ss",
             len(KEYWORDS), POLL_INTERVAL)
    await asyncio.gather(
        search_loop(),
        auction_monitor_loop(),
        watchlist_loop(),
    )

asyncio.run(main())
