"""
omega.py — Categoria Omega (arbitragem Japão → Brasil)
=======================================================
Speedmaster, Seamaster Professional 300M e Aqua Terra. Tetos de COMPRA por
referência (R$). Aceita as 3 variações da ref: com ponto (3510.50), sem ponto
(351050) e com final .00 (3510.50.00) — resolvido por normalização.

Integra com o main.py: as KEYWORDS de Omega entram na busca; cada produto é
avaliado por omega_evaluate(), que devolve None (descartar) ou um dict com a
classificação e os dados extras pro alerta. Alertas vão pro grupo (CHAT_ID).
"""

import re

JPY_TO_BRL = 0.035

# ── Tetos de COMPRA por referência (R$) — chave = ref SEM pontos ──
# (a normalização remove pontos, então "3510.50" e "3510.50.00" viram "351050")
OMEGA_REF_MAX_BRL = {
    # Speedmaster
    "351050": 9500, "351150": 9000, "351350": 9000, "352050": 9000, "353950": 9500,
    # Seamaster Professional 300M
    "253180": 7500, "254180": 6000, "225450": 8000, "226450": 6500,
    "255180": 5500, "256180": 4500,
    # Aqua Terra
    "250450": 7000, "251750": 5000, "251850": 5000,
}

# ── Faixa/linha por prefixo de ref (pra mostrar no alerta) ──
OMEGA_LINHA = [
    (re.compile(r"^35"), "Speedmaster"),
    (re.compile(r"^(2531|2541|2254|2264|2551|2561)"), "Seamaster Pro 300M"),
    (re.compile(r"^(2504|2517|2518)"), "Aqua Terra"),
]

# ── Teto geral e gatilho de prioridade (R$) ──
OMEGA_MAX_COMPRA_BRL = 10_000   # teto absoluto
# Tetos genéricos por linha (quando a ref não aparece no título)
OMEGA_GEN_SPEED_BRL  = 9000
OMEGA_GEN_SEA_BRL    = 4500
OMEGA_GEN_AQUA_BRL   = 5000

# ── Keywords de busca (amplas — as refs filtram depois) ──
OMEGA_KEYWORDS = [
    "omega speedmaster", "オメガ スピードマスター",
    "omega seamaster", "オメガ シーマスター",
    "omega aqua terra", "オメガ アクアテラ",
]

# ── Detecção de referência (aceita ponto, sem ponto, .00) ──
# Captura sequências tipo 3510.50, 351050, 3510.50.00, 2531.80.00 etc.
_REF_RE = re.compile(r"\b(\d{4})[.\s]?(\d{2})(?:[.\s]?00)?\b")

# ── Exclusões (feminino, falso, só-peças) ──
OMEGA_BAD = [
    "レディース","ladies","lady's","女性用","婦人",
    "社外","aftermarket","レプリカ","replica","偽物","fake","コピー",
    "部品取り","パーツ","文字盤のみ","ケースのみ","針のみ","空箱","箱のみ",
]

# ── Acessório DEFINITIVO (bloqueia sempre) ──
OMEGA_ACESSORIO = [
    "時計バンド","時計ベルト","腕時計バンド","腕時計ベルト",
    "バンド","ベルト","ストラップ","strap","band",
    "ブレス","ブレスレット","bracelet","メタルブレス",
    "尾錠","バックル","dバックル","buckle","clasp","deployant","中留","中留め",
    "駒","コマ","link","links","アジャスト駒","アジャスト用",
    "交換用","替えベルト","替えバンド","社外ベルト","社外バンド","純正ベルト","純正バンド",
    "保護フィルム","フィルム","カバー","保護",
    "工具","ばね棒","バネ棒","spring bar","tool",
    "ウォッチワインダー","winder","スタンド","stand","ホルダー","holder","収納",
    "ボックス","時計用ボックス","純正ボックス","box only","箱のみ","空箱",
    "メンテナンス","メンテナンスキット","キット","kit","クリーニング","時計用",
    "説明書","保証書のみ","冊子","カタログ","catalog",
]

# ── Defeito / junk ──
OMEGA_JUNK = ["不動","動作不良","要修理","為修理","ジャンク","junk","故障","不具合",
              "破損","難あり","ジャン苦","部品","欠品"]

# ── Pulseira não-original (sinaliza, não exclui) ──
OMEGA_STRAP_NAO_ORIG = ["社外ベルト","社外バンド","汎用","替えベルト"]


def _brl(price_jpy):
    return int((price_jpy or 0) * JPY_TO_BRL)


def _norm_ref(a, b):
    """Junta os grupos capturados numa ref sem pontos: ('3510','50') -> '351050'."""
    return f"{a}{b}"


def _detect_ref(title):
    """Detecta a primeira referência Omega conhecida no título."""
    for m in _REF_RE.finditer(title):
        ref = _norm_ref(m.group(1), m.group(2))
        if ref in OMEGA_REF_MAX_BRL:
            return ref
    return None


def _linha(ref):
    if not ref:
        return None
    for rx, nome in OMEGA_LINHA:
        if rx.search(ref):
            return nome
    return None


def _linha_por_texto(t):
    """Quando não há ref, identifica a linha pela palavra."""
    if "speedmaster" in t or "スピードマスター" in t:
        return "Speedmaster", OMEGA_GEN_SPEED_BRL
    if "seamaster" in t or "シーマスター" in t:
        return "Seamaster", OMEGA_GEN_SEA_BRL
    if "aqua terra" in t or "アクアテラ" in t:
        return "Aqua Terra", OMEGA_GEN_AQUA_BRL
    return None, None


def is_omega(title):
    t = title.lower()
    return "omega" in t or "オメガ" in t


def omega_evaluate(title, price_jpy, description=""):
    """
    Avalia um produto Omega. Retorna None (descartar) ou dict:
      { ref, linha, preco_brl, classificacao, strap_nao_orig }
    classificacao ∈ {PRIORIDADE, ANALISAR, JUNK}
    """
    t = (title or "").lower()

    if not is_omega(t):
        return None

    # Acessório avulso → descarta
    if any(a in t for a in OMEGA_ACESSORIO):
        return None

    # Exclusões duras
    if any(b in t for b in OMEGA_BAD):
        so_pecas = any(x in t for x in ["部品取り","パーツ","文字盤のみ","ケースのみ",
                                        "針のみ","空箱","箱のみ"])
        feminino = any(x in t for x in ["レディース","ladies","lady's","女性用","婦人"])
        falso    = any(x in t for x in ["社外品","aftermarket","レプリカ","replica",
                                        "偽物","fake","コピー"])
        if so_pecas or feminino or falso:
            return None

    brl = _brl(price_jpy)
    if brl <= 0 or brl > OMEGA_MAX_COMPRA_BRL:
        return None

    ref = _detect_ref(t)
    linha = _linha(ref)

    # Decide se passa e com qual teto.
    passou = False
    teto = None
    if ref and ref in OMEGA_REF_MAX_BRL:
        teto = OMEGA_REF_MAX_BRL[ref]
        if brl <= teto:
            passou = True
    else:
        # sem ref conhecida: usa teto genérico da linha detectada por texto
        linha_txt, teto_gen = _linha_por_texto(t)
        if linha_txt and teto_gen and brl <= teto_gen:
            passou = True
            linha = linha_txt
            teto = teto_gen

    if not passou:
        return None

    is_junk = any(x in t for x in OMEGA_JUNK)
    strap_nao_orig = any(x in t for x in OMEGA_STRAP_NAO_ORIG)

    # Classificação: JUNK se defeito; PRIORIDADE se bem abaixo do teto (<=60%).
    if is_junk:
        classificacao = "JUNK"
    elif teto and brl <= teto * 0.6:
        classificacao = "PRIORIDADE"
    else:
        classificacao = "ANALISAR"

    # Formata a ref pra exibição (com pontos: 351050 -> 3510.50)
    ref_fmt = None
    if ref:
        ref_fmt = f"{ref[:4]}.{ref[4:]}"

    return {
        "ref": ref_fmt,
        "linha": linha,
        "preco_brl": brl,
        "classificacao": classificacao,
        "strap_nao_orig": strap_nao_orig,
    }
