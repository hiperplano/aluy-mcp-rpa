"""Perceção de conteúdo ESCONDIDO (ADR-0125) — inspeção de containers + enumeração
por scroll-and-stitch.

Dois mecanismos, OCR-first (cobrem apps custom-desenhados/Wine E Windows nativo,
sem depender de árvore de a11y; UIA é só aceleração quando existe):

  inspect_containers — árvore ANINHADA de subjanelas/painéis da janela-palco
                       (containment real; corrige a lista plana+truncada do dump).
  enumerate          — revela uma lista/tabela rolável ou virtualizada rolando +
                       relendo por OCR + dedup + costura de ordem; detecta o fim
                       pelo diff (region_changed). PAGINADA por causa do orçamento
                       DURO de 60s do MCP (ADR-0124).

A LÓGICA PURA (normalização, bucket de coluna, dedup, stitching) é função de módulo
e testável sem display. A orquestração (screenshot/scroll) vive nas funções que
recebem o engine. Best-effort: degrada, nunca levanta para o chamador do MCP.
"""
import os
import re
import time

_COL_BUCKET_PX = 40   # largura do bucket de coluna p/ o dedup (px no espaço da imagem)


# ── Lógica pura (testável sem display) ─────────────────────────────

def _norm(text: str) -> str:
    """Texto normalizado p/ comparação: minúsculo, espaços colapsados, sem bordas."""
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def _col_bucket(x: int, origin_x: int) -> int:
    """Coluna aproximada do token (px → bucket). O dedup usa (texto, coluna) e
    NUNCA o Y absoluto, porque o Y muda a cada rolagem (a mesma linha reaparece
    deslocada). Tokens iguais em colunas diferentes (ex.: '0.01' em 2 colunas)
    contam como distintos."""
    return int((int(x) - int(origin_x)) // _COL_BUCKET_PX)


def _row_key(token: dict, origin_x: int) -> tuple:
    """Chave de identidade de um token p/ dedup entre frames sobrepostos."""
    return (_norm(token.get("text", "")), _col_bucket(token.get("x", 0), origin_x))


def stitch_tokens(items: list, seen: set, tokens: list, origin_x: int,
                  anchor_bucket=None) -> list:
    """Costura uma nova página de OCR à lista acumulada: adiciona só os tokens
    AINDA NÃO VISTOS (a sobreposição entre frames cai fora naturalmente). Muta
    `items`/`seen` e devolve os tokens novos desta página.

    anchor_bucket : None (default) = dedup por (texto, coluna), mantém TODAS as
    colunas. Quando setado (dedup OPCIONAL por coluna-âncora), só considera tokens
    DAQUELA coluna e identifica a linha SÓ pelo texto da âncora — colunas voláteis
    (ex.: preços que mudam a cada tick) deixam de inflar/fragmentar a lista.
    """
    fresh = []
    for t in tokens:
        txt = _norm(t.get("text", ""))
        if not txt:
            continue
        bucket = _col_bucket(t.get("x", 0), origin_x)
        if anchor_bucket is not None:
            if bucket != anchor_bucket:
                continue            # fora da coluna-âncora → ignora
            key = txt               # coluna fixa ⇒ identidade = só o texto
        else:
            key = (txt, bucket)
        if key in seen:
            continue
        seen.add(key)
        row = {"text": t.get("text", "").strip(), "x": int(t.get("x", 0)),
               "y": int(t.get("y", 0))}
        items.append(row)
        fresh.append(row)
    return fresh


def resolve_anchor_bucket(anchor_col, tokens: list, origin_x: int):
    """Bucket da coluna-âncora p/ o dedup OPCIONAL por coluna. Default DESLIGADO.

    None/False        → None (desligado; dedup padrão por todas as colunas).
    'auto'/'left'/True → a coluna mais à ESQUERDA com texto (ex.: o ticker numa
                         tabela de cotações — a célula estável da linha).
    int (x em px)      → a coluna que contém aquele x (espaço da imagem).
    """
    if anchor_col is None or anchor_col is False:
        return None
    if anchor_col is True or (isinstance(anchor_col, str)
                              and anchor_col.strip().lower() in ("auto", "left", "esquerda")):
        buckets = [_col_bucket(t.get("x", 0), origin_x)
                   for t in tokens if _norm(t.get("text", ""))]
        return min(buckets) if buckets else None
    if isinstance(anchor_col, (int, float)):
        return _col_bucket(int(anchor_col), origin_x)
    return None


def match_token(tokens: list, find: str):
    """Acha o 1º token cujo texto CONTÉM `find` (normalizado). Devolve
    {x,y,text} ou None. É o curto-circuito do modo busca."""
    f = _norm(find)
    if not f:
        return None
    for t in tokens:
        if f in _norm(t.get("text", "")):
            return {"x": int(t.get("x", 0)), "y": int(t.get("y", 0)),
                    "text": t.get("text", "").strip()}
    return None


# ── Enumeração (scroll-and-stitch) ─────────────────────────────────

def enumerate_container(engine, *, container=None, find=None, click=False,
                        max_pages: int = 6, step: int = 5, cursor: str = None,
                        anchor_col=None, settle: float = 0.28) -> dict:
    """Revela o conteúdo de um container rolável rolando e relendo por OCR.

    container : nome do painel (UIA), rect {x,y,width,height}, ou None = o maior
                rolável / o palco inteiro.
    find      : se setado, PARA assim que casar (barato) e deixa o item rolado p/
                dentro da tela; click=true também clica.
    max_pages : teto de páginas por chamada (orçamento de 60s — OCR ~8s/página).
    cursor    : None = enumeração NOVA (rola ao topo primeiro). "continue" =
                RETOMA da posição atual (lista longa que estourou max_pages).
    anchor_col: dedup OPCIONAL por coluna-âncora (default DESLIGADO). 'auto' =
                coluna mais à esquerda (ex.: ticker) — ignora colunas voláteis
                (preços que mudam a cada tick e inflavam a lista). Ver
                resolve_anchor_bucket.

    Retorno: {items, found, click_point?, exhausted, cursor?, pages, source}.
    `exhausted=true` = chegou ao fim de verdade (o diff zerou). `cursor` presente
    = parou por orçamento; chame de novo com ele p/ continuar.
    """
    region = _resolve_region(engine, container)
    if region is None:
        return {"success": False, "action": "enumerate",
                "error": "sem janela-alvo nem container; chame rpa_target_window primeiro"}

    title = engine._stage_title()
    u = engine._uia()
    cx = region["x"] + region["width"] // 2
    cy = region["y"] + region["height"] // 2
    origin_x = region["x"]

    def _scroll(lines: int):
        """lines<0 = baixo, >0 = cima. UIA ScrollPattern (controle certo) quando
        houver; senão wheel com o cursor SOBRE o container."""
        if u and title:
            try:
                if u.scroll(title, lines):
                    return
            except Exception:
                pass
        try:
            engine.desktop.mouse_move(cx, cy)
        except Exception:
            pass
        engine.desktop.mouse_scroll(lines)

    # Enumeração nova → rola ao topo p/ começar do início. Retomada (cursor) →
    # continua de onde a app está (o scroll position persiste entre chamadas).
    if not cursor:
        _scroll(60)
        time.sleep(settle)

    items, seen = [], set()
    found = None
    pages = 0

    def _ocr_page() -> list:
        ss = engine.screenshot("enum")
        try:
            return engine.vision.ocr_tokens(ss, region=region) or []
        except Exception:
            return []
        finally:
            _unlink(ss)

    # Página corrente (sem rolar ainda). A coluna-âncora (se pedida) é fixada
    # AQUI, na 1ª página, e vale p/ todas — não pode mudar a cada scroll.
    toks = _ocr_page()
    anchor_bucket = resolve_anchor_bucket(anchor_col, toks, origin_x)
    stitch_tokens(items, seen, toks, origin_x, anchor_bucket)
    if find:
        found = match_token(toks, find)

    exhausted = False
    while found is None and pages < max_pages:
        before = engine._screenshot("enum_pre")
        _scroll(-step)
        time.sleep(settle)
        after = engine._screenshot("enum_post")
        try:
            changed = engine.vision.region_changed(before, after, region=region)
        except Exception:
            changed = True  # fail-open: não trava a varredura num erro de diff
        _unlink(before); _unlink(after)
        if not changed:
            exhausted = True   # a lista parou de rolar = fim real
            break
        pages += 1
        toks = _ocr_page()
        stitch_tokens(items, seen, toks, origin_x, anchor_bucket)
        if find:
            found = match_token(toks, find)

    out = {"action": "enumerate", "source": "uia" if (u and title) else "ocr",
           "items": items, "pages": pages, "exhausted": exhausted,
           "count": len(items), "anchored": anchor_bucket is not None}
    if find:
        out["found"] = found is not None
        if found:
            out["click_point"] = {"x": found["x"], "y": found["y"]}
            out["matched_text"] = found["text"]
            if click:
                try:
                    engine.desktop.mouse_click(found["x"], found["y"])
                    out["clicked"] = True
                except Exception as e:
                    out["clicked"] = False
                    out["click_error"] = str(e)
    # Parou por orçamento (não pelo fim) → devolve cursor de retomada.
    if found is None and not exhausted and pages >= max_pages:
        out["cursor"] = "continue"
        out["hint"] = ("Parou no teto de páginas (orçamento de 60s), NÃO no fim da "
                       "lista. Chame de novo com cursor='continue' p/ seguir rolando.")
    elif find and found is None and exhausted:
        out["hint"] = (f"'{find}' NÃO está na lista — varredura exaurida "
                       f"(rolou até o fim, {len(items)} itens vistos).")
    out["success"] = True
    return out


# ── Inspeção de containers (árvore aninhada) ───────────────────────

def inspect_containers(engine, *, min_area: int = 8000, with_ocr_labels: bool = True) -> dict:
    """Árvore ANINHADA de subjanelas/painéis da janela-palco.

    UIA (Win nativo) → containment real, com scrollable/collapsed. Sem árvore
    (canvas/custom-desenhado) → fallback de visão: detecta retângulos de painel
    por bordas (cv2) e rotula pelo OCR. `source` diz de onde veio.
    """
    title = engine._stage_title()
    if title is None:
        return {"success": False, "action": "inspect_containers",
                "error": "sem janela-alvo; chame rpa_target_window primeiro"}
    u = engine._uia()
    if u and title:
        try:
            vl, vt = engine._vorigin()
            tree = u.tree(title, vleft=vl, vtop=vt)
            if tree:
                return {"success": True, "action": "inspect_containers",
                        "source": "uia", "containers": tree,
                        "hint": "Árvore de containers (acessibilidade). `scrollable:true` "
                                "→ use rpa_enumerate nesse painel p/ revelar o que está fora "
                                "da tela; `collapsed:true` → rpa_click_text no nó p/ expandir."}
        except Exception as e:
            engine._log(f"inspect_containers UIA falhou: {e}")
    boxes = _vision_containers(engine, min_area, with_ocr_labels)
    return {"success": True, "action": "inspect_containers", "source": "vision",
            "containers": boxes,
            "hint": "Painéis detectados por VISÃO (aproximado — app sem árvore de "
                    "acessibilidade, ex.: canvas/custom-desenhado). Use o rect de um "
                    "painel como `container` do rpa_enumerate p/ varrer seu conteúdo."}


def _vision_containers(engine, min_area: int, with_labels: bool) -> list:
    """Fallback CV: retângulos de painel por detecção de bordas, rotulados pelo
    token OCR mais forte dentro de cada um. Best-effort → [] se cv2 falhar."""
    region = engine._region(None)
    ss = engine.screenshot("inspect")
    try:
        import cv2
        import numpy as np
        img = cv2.imread(ss)
        if img is None:
            return []
        ox = oy = 0
        if region:
            ox, oy = region["x"], region["y"]
            x2 = ox + region["width"]; y2 = oy + region["height"]
            img = img[oy:y2, ox:x2]
        H, W = img.shape[:2]
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 40, 120)
        # fecha bordas finas/tracejadas p/ os contornos virarem retângulos inteiros
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        closed = cv2.dilate(edges, kernel, iterations=2)
        cnts, _ = cv2.findContours(closed, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        boxes = []
        for c in cnts:
            x, y, w, h = cv2.boundingRect(c)
            if w * h < min_area or w < 60 or h < 40:
                continue
            if w >= W - 4 and h >= H - 4:       # a janela inteira não é "painel"
                continue
            boxes.append([x, y, w, h])
        boxes = _dedup_boxes(boxes)
        # rótulos por OCR (1 chamada na janela toda, atribuída por contenção)
        labels = {}
        if with_labels and boxes:
            try:
                toks = engine.vision.ocr_tokens(ss, region=region) or []
                for b in boxes:
                    labels[tuple(b)] = _label_for_box(b, toks, ox, oy)
            except Exception:
                pass
        out = []
        for b in boxes:
            x, y, w, h = b
            out.append({"role": "Panel",
                        "label": labels.get(tuple(b), ""),
                        "rect": {"x": x + ox, "y": y + oy, "width": w, "height": h},
                        "scrollable": None, "collapsed": None})
        return out
    except Exception as e:
        engine._log(f"_vision_containers falhou: {e}")
        return []
    finally:
        _unlink(ss)


def _dedup_boxes(boxes: list, *, iou: float = 0.85) -> list:
    """Remove retângulos quase-duplicados (contornos aninhados da mesma borda).
    Mantém o maior de cada cluster."""
    out = []
    for b in sorted(boxes, key=lambda r: -r[2] * r[3]):
        if all(_iou(b, k) < iou for k in out):
            out.append(b)
    return out


def _iou(a: list, b: list) -> float:
    ax, ay, aw, ah = a; bx, by, bw, bh = b
    ix1, iy1 = max(ax, bx), max(ay, by)
    ix2, iy2 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    iw, ih = max(0, ix2 - ix1), max(0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    return inter / union if union else 0.0


def _label_for_box(box: list, tokens: list, ox: int, oy: int) -> str:
    """Rótulo do painel = token OCR mais ALTO (topo) contido na box — costuma ser
    o título/cabeçalho do painel."""
    x, y, w, h = box
    inside = [t for t in tokens
              if x <= (t.get("x", 0) - ox) <= x + w and y <= (t.get("y", 0) - oy) <= y + h]
    if not inside:
        return ""
    inside.sort(key=lambda t: t.get("y", 0))
    return inside[0].get("text", "").strip()[:40]


# ── helpers ────────────────────────────────────────────────────────

def _resolve_region(engine, container):
    """Resolve o `container` em um rect {x,y,width,height}. Aceita rect explícito,
    nome (via UIA), ou None = maior rolável (UIA) → senão o palco."""
    if isinstance(container, dict) and "width" in container:
        return container
    u = engine._uia()
    title = engine._stage_title()
    if u and title:
        try:
            vl, vt = engine._vorigin()
            if isinstance(container, str) and container.strip():
                el = u.find(title, container, vleft=vl, vtop=vt)
                if el and el.get("rect"):
                    r = el["rect"]
                    return {"x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"]}
            sc = u.scrollable_rect(title, vleft=vl, vtop=vt)
            if sc:
                return sc
        except Exception:
            pass
    return engine._region(None)


def _unlink(path):
    try:
        if path:
            os.unlink(path)
    except Exception:
        pass
