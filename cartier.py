"""
cartier.py — Categoria Cartier (arbitragem Japão → Brasil)
===========================================================
Linhas monitoradas:
  • Panthère de Cartier      — teto R$ 16.000
  • Chronoscaph 21 (Must 21) — teto R$ 6.000

Particularidades:
  - Panthère é majoritariamente FEMININO → aqui NÃO bloqueamos "ladies".
  - "Panthère" também é linha de JOIAS da Cartier → exigimos sinal de
    relógio no título e bloqueamos termos de joia.
  - Piso R$2.000 só para preço fixo (leilão livre, pega os 1円).
"""

import re

JPY_TO_BRL = 0.035

CARTIER_MAX_COMPRA_BRL = 16_000
PANTHERE_MAX_BRL = 16_000
CHRONOSCAPH_MAX_BRL = 6_000
PISO_FIXO_JPY = int(2000 / JPY_TO_BRL)   # ~R$2.000

# ── Keywords de busca (amplas — o filtro faz o resto) ──
CARTIER_KEYWORDS = [
    "cartier panthere", "カルティエ パンテール",
    "cartier chronoscaph", "カルティエ クロノスカフ",
]

# ── Identificação das linhas ──
PANTHERE_TERMS = ["panthere", "panthère", "パンテール", "パンテ－ル"]
CHRONO_TERMS = ["chronoscaph", "クロノスカフ", "クロノスカーフ"]

# ── Sinal de que é RELÓGIO (obrigatório no Panthère, que divide nome com joias) ──
WATCH_SIGNAL = ["腕時計", "時計", "watch", "クォーツ", "クオーツ", "quartz",
                "自動巻", "automatic", "sm", "mm", "lm", "文字盤", "ウォッチ"]

# ── Joias (Panthère de Cartier também é linha de joalheria) ──
JOIA = ["リング", "指輪", "ring", "ネックレス", "necklace", "ペンダント", "pendant",
        "ピアス", "イヤリング", "earring", "バングル", "bangle", "ブローチ", "brooch",
        "ブレスレット", "チャーム", "charm", "カフス"]

# ── Acessório avulso / tranqueira / livro ──
ACESSORIO = [
    "時計バンド", "時計ベルト", "バンドのみ", "ベルトのみ", "ストラップ", "strap",
    "尾錠", "バックル", "dバックル", "buckle", "clasp", "中留",
    "駒", "コマ", "link", "アジャスト",
    "交換用", "替えベルト", "純正ベルト", "純正バンド", "ラバーベルト単体",
    "ボックス", "box only", "箱のみ", "空箱", "保証書のみ", "冊子",
    "工具", "ばね棒", "スタンド", "ホルダー", "収納", "ウォッチワインダー",
    "メンテナンス", "キット", "クリーニング", "時計用",
    "マスターブック", "ブック", "book", "単行本","文庫本", "書籍", "雑誌", "カタログ", "catalog",
    "帽子", "キャップ", "ステッカー", "キーホルダー", "ノベルティ", "ポスター",
    "文字盤のみ", "ケースのみ", "針のみ", "部品取り", "パーツ",
]

FALSO = ["レプリカ", "replica", "偽物", "fake", "コピー", "社外品", "aftermarket"]
FEMININO = ["レディース", "ladies", "lady's", "女性用", "婦人"]
JUNK = ["不動", "動作不良", "要修理", "為修理", "ジャンク", "junk", "故障",
        "不具合", "破損", "難あり", "欠品"]

_REF_RE = re.compile(r"\b(w[0-9a-z]{7}|wspn\d{4}|wjpn\d{4}|w2pn\d{4}|w25\d{3}|1[12]\d{4})\b", re.I)


def _brl(price_jpy):
    return int((price_jpy or 0) * JPY_TO_BRL)


def is_cartier(title):
    t = title.lower()
    return "cartier" in t or "カルティエ" in t


def cartier_evaluate(title, price_jpy, description="", is_auction=False):
    """
    Retorna None (descartar) ou dict:
      { linha, ref, preco_brl, classificacao }
    classificacao ∈ {PRIORIDADE, ANALISAR, JUNK}
    """
    t = (title or "").lower()
    if not is_cartier(t):
        return None

    is_panthere = any(x in t for x in PANTHERE_TERMS)
    is_chrono = any(x in t for x in CHRONO_TERMS)
    if not (is_panthere or is_chrono):
        return None

    # Bloqueios duros
    if any(a in t for a in ACESSORIO):
        return None
    if any(f in t for f in FALSO):
        return None

    if is_panthere:
        # Panthère divide nome com joias: exige sinal de relógio e bloqueia joia.
        if any(j in t for j in JOIA):
            return None
        if not any(w in t for w in WATCH_SIGNAL):
            return None
        linha, teto = "Panthère", PANTHERE_MAX_BRL
        # (feminino NÃO bloqueia no Panthère — é a maior parte do mercado)
    else:
        # Chronoscaph é masculino: bloqueia feminino.
        if any(f in t for f in FEMININO):
            return None
        linha, teto = "Chronoscaph 21", CHRONOSCAPH_MAX_BRL

    brl = _brl(price_jpy)
    if brl <= 0 or brl > min(teto, CARTIER_MAX_COMPRA_BRL):
        return None
    if not is_auction and price_jpy < PISO_FIXO_JPY:
        return None

    m = _REF_RE.search(t)
    ref = m.group(1).upper() if m else None

    if any(j in t for j in JUNK):
        classificacao = "JUNK"
    elif brl <= teto * 0.6:
        classificacao = "PRIORIDADE"
    else:
        classificacao = "ANALISAR"

    return {"linha": linha, "ref": ref, "preco_brl": brl,
            "classificacao": classificacao}
