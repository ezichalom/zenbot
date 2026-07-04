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
    # Se o Cloudflare bloquear no Railway (403/503), troque para:
    # pip install curl_cffi   e use   from curl_cffi import requests
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests

log = logging.getLogger("zenmarket_stream")

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
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/event-stream",
    "Content-Type": "application/json",
    "Origin": "https://zenmarket.jp",
    "Referer": "https://zenmarket.jp/pt/search.aspx",
    "X-Requested-With": "XMLHttpRequest",
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


def stream_search(
    query: str,
    stores: Optional[list[int]] = None,
    page: int = 1,
    page_size: int = 20,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    session: Optional[requests.Session] = None,
    timeout: int = 60,
) -> Iterator[tuple[str, dict]]:
    """
    Executa a busca e produz eventos SSE crus (event_name, data).
    Útil se você quiser reagir loja por loja em tempo real.
    """
    sess = session or requests.Session()
    payload = build_payload(query, stores, page, page_size, min_price, max_price)

    resp = sess.post(
        SEARCH_URL,
        params={"stream": "1"},
        json=payload,
        headers=DEFAULT_HEADERS,
        stream=True,
        timeout=timeout,
    )
    resp.raise_for_status()

    ctype = resp.headers.get("Content-Type", "")
    if "text/event-stream" not in ctype:
        # Cloudflare provavelmente devolveu HTML de challenge
        raise RuntimeError(
            f"Resposta não é SSE (Content-Type={ctype!r}). "
            "Possível bloqueio Cloudflare — tente curl_cffi ou cookies de sessão."
        )

    yield from iter_sse_events(resp)


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
        "price": p.get("price"),          # em ienes (JPY)
        "url": p.get("url"),
        "image": (p.get("images") or [None])[0],
        "isUsed": p.get("isUsed"),
        "seller": (p.get("seller") or {}).get("name") or (p.get("seller") or {}).get("id"),
        # Campos de leilão (Yahoo Auctions)
        "bids": bids,
        "buyoutPrice": buyout,
        "auctionEndTime": end_time,       # datetime com tz +09:00 (Japão)
        "raw": p,                         # objeto original completo
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
