"""
zenmarket_stream.py — Cliente da API interna de busca do ZenMarket (SSE)
=========================================================================
Motor: CapSolver resolve o desafio Cloudflare (usando proxy sticky) e devolve
o cookie cf_clearance; curl_cffi então chama a API SSE com esse cookie + o
mesmo proxy + TLS de navegador. O cookie é cacheado ~20 min para economizar.

Variáveis de ambiente necessárias (no Railway):
    CAPSOLVER_KEY  — chave da API do CapSolver
    PROXY_URL      — proxy STICKY (porta 10000+): http://user:pass@host:porta
"""

from __future__ import annotations

import os as _os
import json
import time as _time
import logging
from datetime import datetime, timezone
from typing import Iterator, Optional

import requests

log = logging.getLogger("zenmarket_stream")

# curl_cffi (TLS de navegador) — necessário para o cf_clearance ser aceito.
try:
    from curl_cffi import requests as _cffi
    _HAS_CFFI = True
except Exception:
    _HAS_CFFI = False

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


# ═══════════════════════════════════════════════════════════════════════════
# MOTOR: CapSolver (resolve Cloudflare) + curl_cffi (usa o cookie) + cache
# ═══════════════════════════════════════════════════════════════════════════
_CAPSOLVER_KEY = _os.getenv("CAPSOLVER_KEY", "").strip()
_PROXY_URL     = _os.getenv("PROXY_URL", "").strip()   # sticky! porta 10000+

_CF_CACHE = {"cookie": None, "user_agent": None, "ts": 0.0}
_CF_TTL = 20 * 60   # 20 minutos

# User-Agent FIXO: o mesmo é enviado ao CapSolver (pra ele resolver com ele) e
# usado no curl_cffi. Se o UA do solve != UA do uso, o Cloudflare rejeita.
_FIXED_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# Session ID estável: força o DataImpulse a devolver SEMPRE o mesmo IP (sticky
# de verdade), garantindo que o IP do solve == IP do uso.
_SESSID = "zenbot1"


def _user_with_session(user: str) -> str:
    """Sessid DESATIVADO: o CapSolver não aceita ;sessid. no username (timeout).
    Username puro — o IP fixo virá do proxy estático (não do pool)."""
    return user


def _proxy_parts():
    """Extrai (user, pw, host, port) da PROXY_URL."""
    import re
    m = re.match(r"https?://(?:([^:@]+):([^@]+)@)?([^:/]+):(\d+)", _PROXY_URL)
    if not m:
        return None
    return m.group(1), m.group(2), m.group(3), m.group(4)


def _proxy_fields_for_capsolver() -> dict:
    """Campos separados do proxy pro CapSolver (formato mais robusto que string).
    Evita ambiguidade de parsing que causa ERROR_PROXY_CONNECT_REFUSED."""
    parts = _proxy_parts()
    if not parts:
        return {}
    user, pw, host, port = parts
    return {
        "proxyType": "http",
        "proxyAddress": host,
        "proxyPort": int(port),
        "proxyLogin": user,
        "proxyPassword": pw,
    }


def _proxy_for_capsolver() -> str:
    """CapSolver quer 'host:porta:usuario:senha' — com sessid pra travar o IP."""
    parts = _proxy_parts()
    if not parts:
        return ""
    user, pw, host, port = parts
    user = _user_with_session(user)
    return f"{host}:{port}:{user}:{pw}" if user else f"{host}:{port}"


def _proxy_for_cffi() -> Optional[dict]:
    parts = _proxy_parts()
    if not parts:
        return None
    user, pw, host, port = parts
    user = _user_with_session(user)
    url = f"http://{user}:{pw}@{host}:{port}"
    return {"http": url, "https": url}


def _solve_cloudflare() -> tuple[Optional[str], Optional[str]]:
    """Resolve o Cloudflare via CapSolver (com nosso proxy). Cacheia ~20min."""
    now = _time.time()
    if _CF_CACHE["cookie"] and (now - _CF_CACHE["ts"] < _CF_TTL):
        return _CF_CACHE["cookie"], _CF_CACHE["user_agent"]

    if not _CAPSOLVER_KEY:
        raise RuntimeError("CAPSOLVER_KEY não configurada no Railway.")
    proxy = _proxy_for_capsolver()
    if not proxy:
        raise RuntimeError("PROXY_URL inválida/ausente para o CapSolver.")

    task = {
        "type": "AntiCloudflareTask",
        "websiteURL": "https://zenmarket.jp/pt/",
        "userAgent": _FIXED_UA,
    }
    task.update(_proxy_fields_for_capsolver())   # proxyType/Address/Port/Login/Password
    create = requests.post("https://api.capsolver.com/createTask", json={
        "clientKey": _CAPSOLVER_KEY,
        "task": task,
    }, timeout=30).json()

    task_id = create.get("taskId")
    if not task_id:
        raise RuntimeError(f"CapSolver createTask falhou: {create}")

    for _ in range(40):
        _time.sleep(2)
        res = requests.post("https://api.capsolver.com/getTaskResult", json={
            "clientKey": _CAPSOLVER_KEY, "taskId": task_id,
        }, timeout=30).json()
        status = res.get("status")
        if status == "ready":
            sol = res.get("solution", {})
            cookie = (sol.get("cookies") or {}).get("cf_clearance") or sol.get("token")
            ua = sol.get("userAgent")
            _CF_CACHE.update({"cookie": cookie, "user_agent": ua, "ts": _time.time()})
            log.info("CapSolver: Cloudflare resolvido (cache %dmin).", _CF_TTL // 60)
            return cookie, ua
        if status == "failed" or res.get("errorId"):
            raise RuntimeError(f"CapSolver falhou: {res}")
    raise RuntimeError("CapSolver: timeout aguardando solução.")


def _invalidate_cf_cache():
    _CF_CACHE.update({"cookie": None, "user_agent": None, "ts": 0.0})


def stream_search(
    query: str,
    stores: Optional[list[int]] = None,
    page: int = 1,
    page_size: int = 20,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    session=None,
    timeout: int = 60,
) -> Iterator[tuple[str, dict]]:
    """Busca via API SSE, autenticada com cf_clearance (CapSolver) + curl_cffi."""
    if not _HAS_CFFI:
        raise RuntimeError("curl_cffi não instalado.")

    payload = build_payload(query, stores, page, page_size, min_price, max_price)
    raw = ""
    for tentativa in (1, 2):
        cookie, ua = _solve_cloudflare()
        headers = dict(DEFAULT_HEADERS)
        headers["User-Agent"] = _FIXED_UA   # mesmo UA do solve
        cookies = {"cf_clearance": cookie} if cookie else {}

        resp = _cffi.post(
            SEARCH_URL, params={"stream": "1"}, json=payload,
            headers=headers, cookies=cookies, proxies=_proxy_for_cffi(),
            impersonate="chrome", timeout=timeout,
        )
        if resp.status_code == 403 and tentativa == 1:
            log.warning("403 com cookie em cache — resolvendo de novo.")
            _invalidate_cf_cache()
            continue
        resp.raise_for_status()
        raw = resp.text
        break

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
