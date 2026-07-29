"""
zenmarket_stream.py — Cliente da API interna de busca do ZenMarket (SSE)
=========================================================================
Engenharia reversa: POST https://zenmarket.jp/pt/search.aspx?stream=1
Resposta: Server-Sent Events, um evento `store-result` por loja,
finalizado por `search-complete` com {"totalFound": N}.

Uso rápido:
    from zenmarket_stream import search, STORE
    produtos = search("bvlgari al38", stores=[STORE["Mercari"], STORE["YahooAuction"]])
    for p in produtos:
        print(p["storeName"], p["price"], p["title"][:60], p["url"])

Dependências:
    pip install requests
    # Se o Cloudflare bloquear no Railway (403/503), instale curl_cffi
    # (já incluído no requirements) — este módulo usa automaticamente.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests

# curl_cffi imita a impressão digital TLS/JA3 do Chrome, o que costuma passar
# pelo Cloudflare (que bloqueia o requests puro com 403). Fallback: requests.
try:
    from curl_cffi import requests as _cffi_requests
    _HAS_CFFI = True
except Exception:
    _HAS_CFFI = False

# Assinaturas TLS a tentar, em ordem. Versões recentes de Chrome/Safari/Edge
# têm handshakes diferentes; alguma pode passar onde outra é bloqueada.
_IMPERSONATE_ROTATION = ["chrome131", "chrome124", "safari17_0", "edge101", "chrome120"]

# ── PROXY (opcional) ──────────────────────────────────────────────────────
# Lê a URL do proxy da variável de ambiente PROXY_URL, no formato:
#   http://usuario:senha@host:porta
# Configurada no Railway (nunca no código). Se vazia, roda sem proxy.
import os as _os
_PROXY_URL = _os.getenv("PROXY_URL", "").strip()
_PROXIES = {"http": _PROXY_URL, "https": _PROXY_URL} if _PROXY_URL else None

log = logging.getLogger("zenmarket_stream")
if _PROXY_URL:
    log.info("Proxy configurado (via PROXY_URL).")

# ---------------------------------------------------------------------------
# Constantes descobertas na engenharia reversa (03/07/2026)
# ---------------------------------------------------------------------------
SEARCH_URL = "https://zenmarket.jp/pt/search.aspx"

STORE = {
    "Rakuten": 0,
    "Amazon": 17,
    "YahooShopping": 18,
    "Rakuma": 25,
    "ZenPlus": 26,
    "Mercari": 27,
    "YahooAuction": 28,
    "SnkrDunk": 53,
    "Ragtag": 57,
    "BrandOff": 63,
}
STORE_BY_ID = {v: k for k, v in STORE.items()}

# Status possíveis de cada evento store-result
STATUS_QUEUED = "QUEUED"            # loja entrou na fila — ignorar
STATUS_HAS_DATA = "HAS_DATA"        # produtos disponíveis — processar
STATUS_FINISHED_EMPTY = "FINISHED_EMPTY"  # loja sem resultados

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/event-stream",
    "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8,ja;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Origin": "https://zenmarket.jp",
    "Referer": "https://zenmarket.jp/pt/search.aspx",
    "X-Requested-With": "XMLHttpRequest",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Connection": "keep-alive",
}


def build_payload(
    query: str,
    stores: Optional[list[int]] = None,
    page: int = 1,
    page_size: int = 20,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    sort_option: Optional[str] = None,
) -> dict:
    """Monta o payload exatamente como o site envia."""
    return {
        "query": query,
        "stores": stores or [],          # [] = todas as lojas
        "page": page,
        "pageSize": page_size,
        "minPrice": min_price,
        "maxPrice": max_price,
        "sortOption": sort_option,
        "showAdultGoods": False,
        "skipQueryProcessing": False,
        "conditionSearchType": 0,
        "conditionSearchNewType": 0,
        "conditionSearchUsedTypes": [],
        "storeFilters": {},
        "sellerType": None,
        "recommendedCategory": None,
        "conditions": None,
    }


def _parse_data_lines(data_lines: list[str]) -> Optional[dict]:
    """
    Tenta reconstruir o JSON de um bloco SSE.

    Eventos grandes (HAS_DATA com dezenas de produtos) podem chegar
    quebrados em várias linhas `data:`. A quebra pode cair NO MEIO de uma
    string do JSON — nesse caso juntar com "\\n" invalida o JSON.
    Estratégia: tenta juntar sem separador primeiro (caso mais comum de
    quebra arbitrária), depois com "\\n" (SSE spec), depois cada linha só.
    """
    candidates = ["".join(data_lines), "\n".join(data_lines)]
    if len(data_lines) == 1:
        candidates = [data_lines[0]]

    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError:
            continue
    return None


def _iter_utf8_lines(response: requests.Response) -> Iterator[str]:
    """
    Itera as linhas do stream em NÍVEL DE BYTES, quebrando apenas em b"\\n"
    e decodificando cada linha explicitamente como UTF-8.

    Por que não usar response.iter_lines()?
      1. O servidor não declara charset no Content-Type do event-stream,
         então o requests decodifica como Latin-1 → texto japonês vira
         mojibake.
      2. iter_lines() usa str.splitlines(), que quebra linhas também em
         U+0085 (NEL), U+2028 etc. — e o mojibake de japonês está CHEIO
         de U+0085, fatiando o JSON no meio e invalidando o parse.
    """
    buf = b""
    for chunk in response.iter_content(chunk_size=8192):
        if not chunk:
            continue
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line.decode("utf-8", errors="replace").rstrip("\r")
    if buf:
        yield buf.decode("utf-8", errors="replace").rstrip("\r")


def iter_sse_events(response: requests.Response) -> Iterator[tuple[str, dict]]:
    """
    Faz o parse manual do fluxo SSE.
    Yields (event_name, data_dict) para cada bloco `event:` + `data:`.
    """
    event_name = None
    data_lines: list[str] = []

    for line in _iter_utf8_lines(response):

        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "":
            # linha em branco = fim do bloco SSE
            if event_name and data_lines:
                payload = _parse_data_lines(data_lines)
                if payload is not None:
                    yield event_name, payload
                else:
                    preview = ("".join(data_lines))[:150]
                    log.warning(
                        "Bloco SSE com JSON inválido, ignorado. "
                        "event=%r linhas=%d inicio=%r",
                        event_name, len(data_lines), preview,
                    )
            event_name, data_lines = None, []

    # flush final (caso o stream termine sem linha em branco)
    if event_name and data_lines:
        payload = _parse_data_lines(data_lines)
        if payload is not None:
            yield event_name, payload


# ── Playwright ASSÍNCRONO ──────────────────────────────────────────────────
# main.py chama search() via asyncio.to_thread, então rodamos o Playwright async
# dentro de um event loop PRÓPRIO e isolado nessa thread. Isso evita os conflitos
# "Sync API inside asyncio loop" e "Cannot switch to a different thread".
import asyncio as _asyncio

_JS_FETCH = """
async (payloadStr) => {
    const resp = await fetch("/pt/search.aspx?stream=1", {
        method: "POST",
        headers: {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Requested-With": "XMLHttpRequest"
        },
        body: payloadStr
    });
    if (!resp.ok) return "HTTP_ERROR_" + resp.status;
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let out = "";
    while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        out += decoder.decode(value, {stream: true});
    }
    return out;
}
"""


def _proxy_dict():
    if not _PROXY_URL:
        return None
    import re
    m = re.match(r"https?://(?:([^:@]+):([^@]+)@)?([^:/]+):(\d+)", _PROXY_URL)
    if not m:
        return None
    user, pw, host, port = m.group(1), m.group(2), m.group(3), m.group(4)
    d = {"server": f"http://{host}:{port}"}
    if user:
        d["username"], d["password"] = user, pw
    return d


async def _fetch_stream_async(payload: dict, timeout: int = 90) -> str:
    """Abre Chromium (async), passa pelo Cloudflare e captura o SSE via fetch()."""
    from playwright.async_api import async_playwright

    body_json = json.dumps(payload)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True,
            proxy=_proxy_dict(),
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled",
                  "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            locale="pt-BR",
            user_agent=DEFAULT_HEADERS["User-Agent"],
            viewport={"width": 1366, "height": 768},
        )
        page = await context.new_page()
        try:
            # Carrega a home primeiro (mais leve) — deixa o Cloudflare resolver.
            await page.goto("https://zenmarket.jp/pt/",
                            wait_until="domcontentloaded", timeout=timeout * 1000)

            # Espera o desafio do Cloudflare terminar de verdade: título deixa de
            # ser "Just a moment" E o cookie cf_clearance aparece no contexto.
            async def _cf_liberado():
                title = (await page.title() or "").lower()
                if "just a moment" in title or "attention" in title:
                    return False
                cookies = await context.cookies()
                return any(c["name"] == "cf_clearance" for c in cookies)

            liberado = False
            for _ in range(40):                 # até 40s esperando o Cloudflare
                if await _cf_liberado():
                    liberado = True
                    break
                await page.wait_for_timeout(1000)

            # Folga extra pra garantir que o cookie propagou antes do fetch.
            await page.wait_for_timeout(1500)

            # Navega para a página de busca já autenticado (mesma origem/cookie).
            await page.goto("https://zenmarket.jp/pt/search.aspx",
                            wait_until="domcontentloaded", timeout=timeout * 1000)
            await page.wait_for_timeout(500)

            # Dispara o fetch() da API SSE. Se vier 403, espera e tenta de novo
            # (o cookie pode levar mais um instante para valer no endpoint).
            for tentativa in range(3):
                result = await page.evaluate(_JS_FETCH, body_json)
                if result and not result.startswith("HTTP_ERROR_403"):
                    return result
                await page.wait_for_timeout(2500)
            return result or ""
        finally:
            await context.close()
            await browser.close()


def _run_stream_via_browser(payload: dict, timeout: int = 90) -> str:
    """
    Ponte sync→async: cria um event loop próprio nesta thread e roda o
    Playwright async dentro dele. Retorna o texto SSE completo.
    """
    loop = _asyncio.new_event_loop()
    try:
        return loop.run_until_complete(_fetch_stream_async(payload, timeout))
    finally:
        loop.close()


def stream_search(
    query: str,
    stores: Optional[list[int]] = None,
    page: int = 1,
    page_size: int = 20,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    session=None,
    timeout: int = 90,
) -> Iterator[tuple[str, dict]]:
    """Executa a busca via navegador (Playwright async) e produz eventos SSE."""
    payload = build_payload(query, stores, page, page_size, min_price, max_price)
    raw = _run_stream_via_browser(payload, timeout=timeout)

    if raw.startswith("HTTP_ERROR_"):
        raise RuntimeError(f"ZenMarket respondeu {raw} (via browser).")
    if not raw.strip():
        raise RuntimeError("Stream vazio via browser (possível bloqueio Cloudflare).")

    event_name = None
    data_lines: list[str] = []
    for line in raw.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("event:"):
            event_name = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data_lines.append(line[len("data:"):].strip())
        elif line == "":
            if event_name and data_lines:
                obj = _parse_data_lines(data_lines)
                if obj is not None:
                    yield event_name, obj
            event_name, data_lines = None, []
    if event_name and data_lines:
        obj = _parse_data_lines(data_lines)
        if obj is not None:
            yield event_name, obj

def _normalize_product(store_name: str, p: dict) -> dict:
    """Extrai e padroniza os campos úteis de um produto."""
    extra = p.get("additionalData") or {}

    end_time = None
    if extra.get("EndTime"):
        try:
            end_time = datetime.fromisoformat(extra["EndTime"])
        except ValueError:
            pass

    bids = None
    if extra.get("Bids") not in (None, ""):
        try:
            bids = int(extra["Bids"])
        except (TypeError, ValueError):
            pass

    buyout = None
    if extra.get("BuyoutPrice") not in (None, ""):
        try:
            buyout = int(extra["BuyoutPrice"])
        except (TypeError, ValueError):
            pass

    return {
        "storeName": store_name,
        "sku": p.get("sku") or p.get("id"),
        "title": p.get("title", ""),
        "price": p.get("price"),
        "url": p.get("url"),
        "image": (p.get("images") or [None])[0],
        "isUsed": p.get("isUsed"),
        "seller": (p.get("seller") or {}).get("name") or (p.get("seller") or {}).get("id"),
        "bids": bids,
        "buyoutPrice": buyout,
        "auctionEndTime": end_time,
        "raw": p,
    }


def search(
    query: str,
    stores: Optional[list[int]] = None,
    page: int = 1,
    page_size: int = 20,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    keyword_filter: Optional[list[str]] = None,
    price_floor: Optional[int] = None,
    session: Optional[requests.Session] = None,
) -> list[dict]:
    """
    Busca completa: consome o stream inteiro e devolve a lista de produtos
    normalizados e deduplicados por SKU.

    keyword_filter: lista de termos (case-insensitive); mantém o produto se
                    QUALQUER termo aparecer no título. Ex.: ["AL38TA", "AL38A"]
                    Útil para descartar pulseiras/fivelas/peças.
    price_floor:    descarta itens abaixo desse preço em JPY (anti-acessório).
    """
    seen: set[str] = set()
    results: list[dict] = []

    for event, data in stream_search(
        query, stores, page, page_size, min_price, max_price, session
    ):
        if event == "search-complete":
            log.info("Busca concluída: totalFound=%s", data.get("totalFound"))
            break
        if event != "store-result" or data.get("status") != STATUS_HAS_DATA:
            continue

        store_name = data.get("storeName") or STORE_BY_ID.get(data.get("store"), "?")
        for p in data.get("products", []):
            item = _normalize_product(store_name, p)

            key = f'{store_name}:{item["sku"]}'
            if key in seen:
                continue
            seen.add(key)

            title_lower = item["title"].lower()
            if keyword_filter and not any(k.lower() in title_lower for k in keyword_filter):
                continue
            if price_floor and (item["price"] or 0) < price_floor:
                # cuidado: leilões 1円 legítimos caem aqui — trate à parte se quiser
                continue

            results.append(item)

    return results


def find_ending_auctions(products: list[dict], hours: float = 24.0) -> list[dict]:
    """Filtra leilões do Yahoo Auctions que terminam nas próximas N horas."""
    now = datetime.now(timezone.utc)
    out = []
    for p in products:
        end = p.get("auctionEndTime")
        if end is None:
            continue
        delta = (end - now).total_seconds() / 3600
        if 0 <= delta <= hours:
            out.append({**p, "hoursLeft": round(delta, 1)})
    return sorted(out, key=lambda x: x["hoursLeft"])


# ---------------------------------------------------------------------------
# Exemplo de uso / teste manual
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    produtos = search(
        "bvlgari al38",
        stores=[STORE["Mercari"], STORE["YahooAuction"]],  # só as que interessam
        keyword_filter=["AL38"],       # descarta acessórios sem a ref
        price_floor=30000,             # descarta pulseiras/fivelas (< ¥30k)
    )

    print(f"\n{len(produtos)} produtos encontrados:\n")
    for p in produtos:
        linha = f'[{p["storeName"]:>12}] ¥{p["price"]:>9,} | {p["title"][:55]}'
        if p["bids"] is not None:
            linha += f' | lances={p["bids"]}'
        if p["auctionEndTime"]:
            linha += f' | termina={p["auctionEndTime"]:%d/%m %H:%M} JST'
        print(linha)
        print(f'              {p["url"]}')

    print("\n--- Leilões terminando em 24h ---")
    for p in find_ending_auctions(produtos, hours=24):
        print(f'  {p["hoursLeft"]}h | ¥{p["price"]:,} | {p["title"][:60]}')
