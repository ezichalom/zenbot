"""
grand_seiko.py — Categoria Grand Seiko (arbitragem Japão → Brasil)
===================================================================
Lógica dedicada de Grand Seiko: referências monitoradas, tetos de compra por
modelo (em R$), classificação (PRIORIDADE / ANALISAR / JUNK), detecção de
"pode não funcionar" e faixas de venda BR para triagem.

Integra com o main.py: as KEYWORDS de GS entram na busca; cada produto é
avaliado por gs_evaluate(), que devolve None (descartar) ou um dict com a
classificação e os dados extras pro alerta.
"""

import re

JPY_TO_BRL = 0.035

# ── Tetos de COMPRA por referência (R$) — valores indicados pelo Ezi ──
GS_REF_MAX_BRL = {
    "sbgx005": 3500, "sbgx009": 3500,
    "sbgx061": 4000, "sbgx063": 4000, "sbgx065": 4300,
    "sbgx201": 4500, "sbgx203": 4500,
    "sbgx261": 5000, "sbgx263": 5000, "sbgx265": 5300,
    "sbgv005": 5000, "sbgv007": 5000, "sbgv205": 5000,
    "sbgv207": 5000, "sbgv221": 5000, "sbgv223": 5000,
    "sbgp001": 5500, "sbgp003": 5500, "sbgp009": 5500,
    "sbgp011": 5500, "sbgp013": 5500,
    "sbgn003": 6500, "sbgn005": 6500,
    "sbgr001": 5000, "sbgr023": 5000, "sbgr051": 5500, "sbgr053": 5500,
}

# ── Faixas de VENDA BR (R$) para triagem — só referência, não garantia ──
# Mapeadas por padrão de referência.
GS_SELL_RANGE = [
    (re.compile(r"sbgx(00[59]|20[13])"),        "R$ 7.500–8.500"),   # SBGX antigos 36-37mm
    (re.compile(r"sbgx(06[135])"),               "R$ 8.500–9.500"),   # SBGX061/063/065
    (re.compile(r"sbgx(26[135])"),               "R$ 10.000–11.500"), # geração moderna
    (re.compile(r"sbgv\d{3}"),                    "R$ 10.000–12.000"), # SBGV 40mm
    (re.compile(r"sbgn\d{3}"),                    "R$ 13.000–15.000"), # SBGN GMT
]

# ── Teto geral e gatilhos de classificação (R$) ──
GS_MAX_COMPRA_BRL   = 10_000   # nunca acima disso
GS_CATCHALL_BRL     = 4_000    # qualquer GS masculino até aqui, sem ref conhecida
GS_PRIORIDADE_BRL   = 3_000    # abaixo disso = oportunidade prioritária

# ── Keywords de busca (referências + genéricas + calibres) ──
GS_KEYWORDS = [
    # Prioridade A
    "grand seiko sbgx", "grand seiko sbgv",
    # Prioridade B
    "grand seiko sbgp", "grand seiko sbgn", "grand seiko sbgr",
    # Genéricas
    "grand seiko", "グランドセイコー",
    # Calibres/caixa
    "グランドセイコー 9f", "grand seiko 9f",
    "grand seiko 9s", "9f61", "9f62", "9f82", "9f83", "9f86", "9s55", "9s65",
]

# Todas as referências conhecidas (pra detectar no título)
_ALL_REFS = list(GS_REF_MAX_BRL.keys())
_REF_RE = re.compile(r"\b(sbg[xvpnr]\d{3})\b", re.I)
_CAL_RE = re.compile(r"\b(9[fs]\d{2}(?:-\d\w+)?)\b", re.I)

# ── Termos de exclusão (peças, falsos, femininos) ──
GS_BAD = [
    "レディース","ladies","lady's","女性用","婦人",  # feminino
    "社外","aftermarket","レプリカ","replica","偽物","fake","コピー",  # falso/réplica
    "部品取り","パーツ","ジャンク品のみ",  # só peças
    "文字盤のみ","ケースのみ","ベルトのみ","針のみ",  # só mostrador/caixa/pulseira/ponteiro
    "空箱","箱のみ",  # só caixa vazia
]

# ── ACESSÓRIO DEFINITIVO: bloqueia SEMPRE (não existe "relógio" que seja isto) ──
# Estes termos identificam pulseira/elo/fivela/ferramenta avulsa. Se aparecem,
# é acessório — mesmo que o título também diga "時計" ou "腕時計" (pulseira DE
# relógio também usa essas palavras). Bloqueio incondicional.
GS_ACESSORIO_HARD = [
    "時計バンド","時計ベルト","腕時計バンド","腕時計ベルト",  # "pulseira de relógio"
    "バンド","ベルト","ストラップ","strap","band",            # band/belt/strap
    "ブレス","ブレスレット","bracelet","メタルブレス",         # bracelet/metal
    "尾錠","バックル","dバックル","buckle","clasp","deployant","中留","中留め",  # fivela/fecho
    "駒","コマ","link","links","アジャスト駒","アジャスト用",   # elos / ajuste
    "交換用","替えベルト","替えバンド","社外ベルト","社外バンド","純正ベルト","純正バンド",  # reposição
    "保護フィルム","フィルム","カバー","保護",                 # película/capa
    "工具","ばね棒","バネ棒","spring bar","tool",              # ferramentas
    "ウォッチワインダー","winder","スタンド","stand","ホルダー","holder","収納",  # winder/suporte
    "box only","箱のみ","空箱",                               # só caixa
]

# Mantida por compatibilidade (não mais usada com exceção — tudo é hard agora).
GS_ACESSORIO = GS_ACESSORIO_HARD
GS_RELOGIO_COMPLETO = ["腕時計","本体","稼働","動作","クオーツ","quartz","自動巻"]

# ── Sinais de "pode não funcionar" (sinalizar, não excluir) ──
GS_NAO_FUNCIONA = ["不動","動作不良","要修理","為修理","ジャンク","junk","故障","不具合"]

# ── Sinal de que é relógio de verdade (não acessório avulso) ──
GS_WATCH_SIGNAL = ["grand seiko","グランドセイコー","sbg","gs ","腕時計",
                   "9f","9s","クオーツ","quartz","自動巻"]


def _brl(price_jpy):
    return int((price_jpy or 0) * JPY_TO_BRL)


def _detect_ref(title):
    m = _REF_RE.search(title.lower())
    return m.group(1).lower() if m else None


def _detect_calibre(title):
    m = _CAL_RE.search(title.lower())
    return m.group(1).upper() if m else None


def _sell_range(ref):
    if not ref:
        return None
    for rx, faixa in GS_SELL_RANGE:
        if rx.search(ref):
            return faixa
    return None


def is_grand_seiko(title):
    """Título parece ser de um Grand Seiko?"""
    t = title.lower()
    return "grand seiko" in t or "グランドセイコー" in t or _REF_RE.search(t) is not None


def gs_evaluate(title, price_jpy, description=""):
    """
    Avalia um produto Grand Seiko. Retorna None (descartar) ou um dict:
      { ref, calibre, preco_brl, classificacao, sell_range, nao_funciona }
    classificacao ∈ {PRIORIDADE, ANALISAR, JUNK}
    """
    t = (title or "").lower()

    if not is_grand_seiko(t):
        return None

    # Bloqueio INCONDICIONAL de pulseira/elo/fivela/ferramenta/caixa avulsa.
    # Estes termos nunca aparecem no relógio completo como palavra principal —
    # "時計バンド", "駒", "ブレス" avulso etc. são sempre acessório.
    if any(a in t for a in GS_ACESSORIO_HARD):
        return None

    # Exclusões duras: feminino, falso, só-peças.
    if any(b in t for b in GS_BAD):
        # exceção: ジャンク sozinho NÃO exclui (vira JUNK). Só exclui se for
        # claramente "só peças / mostrador / caixa avulsa".
        so_pecas = any(x in t for x in ["部品取り","パーツ","文字盤のみ",
                                        "ケースのみ","ベルトのみ","針のみ",
                                        "空箱","箱のみ","ジャンク品のみ"])
        feminino = any(x in t for x in ["レディース","ladies","lady's","女性用","婦人"])
        falso    = any(x in t for x in ["社外","aftermarket","レプリカ","replica",
                                        "偽物","fake","コピー"])
        if so_pecas or feminino or falso:
            return None

    brl = _brl(price_jpy)
    if brl <= 0 or brl > GS_MAX_COMPRA_BRL:
        return None

    ref = _detect_ref(t)
    calibre = _detect_calibre(t)
    sell = _sell_range(ref)
    nao_funciona = any(x in t for x in GS_NAO_FUNCIONA)
    is_junk = any(x in t for x in ["ジャンク","junk"])

    # Decide se passa e com qual teto.
    passou = False
    if ref and ref in GS_REF_MAX_BRL:
        # referência monitorada: usa o teto específico dela
        if brl <= GS_REF_MAX_BRL[ref]:
            passou = True
    else:
        # sem referência conhecida: catch-all até R$ 4.000
        if brl <= GS_CATCHALL_BRL:
            passou = True

    if not passou:
        return None

    # Classificação
    if is_junk or nao_funciona:
        classificacao = "JUNK"
    elif brl < GS_PRIORIDADE_BRL:
        classificacao = "PRIORIDADE"
    else:
        classificacao = "ANALISAR"

    return {
        "ref": (ref or "").upper() or None,
        "calibre": calibre,
        "preco_brl": brl,
        "classificacao": classificacao,
        "sell_range": sell,
        "nao_funciona": nao_funciona,
    }
