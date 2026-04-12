import os
import asyncio
import logging
import requests
from io import BytesIO
from bs4 import BeautifulSoup
from telegram import Bot
from deep_translator import GoogleTranslator
from urllib.parse import quote_plus, urljoin, urlparse

TOKEN = os.getenv("TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
SEND_PHOTOS = os.getenv("SEND_PHOTOS", "false").lower() == "true"

if not TOKEN or not CHAT_ID:
    raise RuntimeError("As variáveis de ambiente TOKEN e CHAT_ID são obrigatórias.")

bot = Bot(token=TOKEN)
seen = set()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

KEYWORDS = [
    # BVLGARI
    "Bvlgari al38",
    "bvlgari aluminium al38",
    "bvlgari al38ta",
    "bvlgari aluminium",
    "ブルガリ アルミニウム",

    # TAG
    "tag heuer formula 1 WAZ1110",
    "tag heuer formula 1 WAZ1112",
    "tag heuer formula 1 CAZ1010",
    "tag heuer formula 1",
    "タグホイヤー フォーミュラ1",

    # CARTIER
    "Cartier chronoscaph",
    "カルティエ クロノスカフ"
]

HEADERS = {"User-Agent": "Mozilla/5.0"}
REQUEST_TIMEOUT = 20


def fetch_soup(url):
    response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def safe_scrape(scraper, keyword):
    try:
        return scraper(keyword)
    except Exception as e:
        logging.warning("falha no scraper %s para '%s': %s", scraper.__name__, keyword, e)
        return []


def normalize_image_url(image_url, base_url):
    if not image_url:
        return None

    image_url = image_url.strip()
    if image_url.startswith("//"):
        image_url = f"https:{image_url}"
    elif image_url.startswith("/"):
        image_url = urljoin(base_url, image_url)

    parsed = urlparse(image_url)
    if parsed.scheme not in ("http", "https"):
        return None

    return image_url


def fetch_image_bytes(image_url):
    response = requests.get(image_url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    content_type = response.headers.get("Content-Type", "").lower()
    if not content_type.startswith("image/"):
        raise ValueError(f"conteúdo não é imagem: {content_type or 'desconhecido'}")

    image_data = BytesIO(response.content)
    image_data.name = "item.jpg"
    image_data.seek(0)
    return image_data


# -------- MERCARI --------
def scrape_mercari(keyword):
    url = f"https://jp.mercari.com/search?keyword={quote_plus(keyword)}"
    soup = fetch_soup(url)

    items = []

    for a in soup.select("a[href*='/item/']"):
        href = a.get("href")
        if not href:
            continue

        full_url = f"https://jp.mercari.com{href}"
        item_id = "mercari_" + href.split("/")[-1]

        title = a.get_text(strip=True)

        img = a.find("img")
        image = normalize_image_url(img["src"], "https://jp.mercari.com") if img and img.get("src") else None

        price = None
        for s in a.find_all("span"):
            if "¥" in s.text:
                price = s.text
                break

        items.append({
            "id": item_id,
            "title": title,
            "price": price,
            "url": full_url,
            "image": image,
            "type": "Buy Now",
            "source": "Mercari"
        })

    return items[:5]


# -------- YAHOO AUCTIONS --------
def scrape_yahoo(keyword):
    url = f"https://auctions.yahoo.co.jp/search/search?p={quote_plus(keyword)}"
    soup = fetch_soup(url)

    items = []

    for a in soup.select("a[href*='/auction/']"):
        href = a.get("href")
        if not href:
            continue

        item_id = "yahoo_" + href.split("/")[-1]

        title = a.get_text(strip=True)

        img = a.find("img")
        image = normalize_image_url(img["src"], "https://auctions.yahoo.co.jp") if img and img.get("src") else None

        price = None
        for s in a.find_all("span"):
            if "円" in s.text:
                price = s.text
                break

        items.append({
            "id": item_id,
            "title": title,
            "price": price,
            "url": href,
            "image": image,
            "type": "Auction",
            "source": "Yahoo"
        })

    return items[:5]


# -------- RAKUMA --------
def scrape_rakuma(keyword):
    url = f"https://fril.jp/s?query={quote_plus(keyword)}"
    soup = fetch_soup(url)

    items = []

    for a in soup.select("a[href*='/item/']"):
        href = a.get("href")
        if not href:
            continue

        full_url = f"https://fril.jp{href}"
        item_id = "rakuma_" + href.split("/")[-1]

        title = a.get_text(strip=True)

        img = a.find("img")
        image = normalize_image_url(img["src"], "https://fril.jp") if img and img.get("src") else None

        items.append({
            "id": item_id,
            "title": title,
            "price": "N/A",
            "url": full_url,
            "image": image,
            "type": "Buy Now",
            "source": "Rakuma"
        })

    return items[:5]


# -------- ZENMARKET --------
def to_zen(url):
    return f"https://zenmarket.jp/pt/auction.aspx?itemCode={url}"


def translate(text):
    try:
        return GoogleTranslator(source='auto', target='pt').translate(text)
    except Exception as e:
        logging.warning("falha ao traduzir texto '%s': %s", text, e)
        return text


async def send_item(item, message):
    if SEND_PHOTOS and item["image"]:
        try:
            image_data = fetch_image_bytes(item["image"])
            await bot.send_photo(
                chat_id=CHAT_ID,
                photo=image_data,
                caption=message
            )
            return
        except Exception as e:
            logging.warning("falha ao enviar foto (%s): %s", item["image"], e)

    try:
        await bot.send_message(
            chat_id=CHAT_ID,
            text=message
        )
    except Exception as e:
        logging.error("falha ao enviar mensagem de texto: %s", e)


# -------- MAIN LOOP --------
async def run():
    while True:
        for keyword in KEYWORDS:
            try:
                items = []
                items += safe_scrape(scrape_mercari, keyword)
                items += safe_scrape(scrape_yahoo, keyword)
                items += safe_scrape(scrape_rakuma, keyword)

                for item in items:
                    if item["id"] in seen:
                        continue

                    seen.add(item["id"])

                    translated = translate(item["title"])
                    zen_url = to_zen(item["url"])

                    msg = f"""
🔥 OPORTUNIDADE

📦 {item['source']}
🔍 {keyword}

📝 {translated}
💴 {item['price']}
⚡ {item['type']}

🔗 ZenMarket:
{zen_url}

🔗 Original:
{item['url']}
"""

                    await send_item(item, msg)

            except Exception as e:
                logging.error("erro no loop principal para '%s': %s", keyword, e)

        await asyncio.sleep(30)


asyncio.run(run())
