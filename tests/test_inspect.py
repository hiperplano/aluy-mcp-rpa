"""Testes da perceção de conteúdo escondido (ADR-0125) — lógica PURA + loop do
enumerate com engine fake. Roda headless, sem display/OCR/cv2.

Cobre o que a CI honesta pode afirmar: dedup por (texto+coluna, nunca Y), costura
de páginas sobrepostas, curto-circuito do `find`, detecção de fim pelo diff,
paginação por orçamento (cursor) e o IoU/dedup de boxes do fallback de visão.
"""
import src.inspect as ins


# ── Lógica pura: normalização / bucket / dedup / stitching ─────────

def test_norm_colapsa_espacos_e_caixa():
    assert ins._norm("  EUR USD  ") == "eur usd"
    assert ins._norm("\tFoo\nBar ") == "foo bar"
    assert ins._norm(None) == ""


def test_col_bucket_separa_colunas_mesmo_texto():
    # mesmo texto em x distantes = colunas distintas; x próximos = mesma coluna
    assert ins._col_bucket(10, 0) == ins._col_bucket(30, 0)       # dentro do bucket
    assert ins._col_bucket(10, 0) != ins._col_bucket(500, 0)      # colunas distintas


def test_stitch_dedup_por_texto_e_coluna_nao_por_y():
    """A MESMA linha reaparece deslocada no Y após rolar — não pode duplicar.
    Texto igual em coluna diferente CONTA como distinto."""
    items, seen = [], set()
    page1 = [{"text": "EURUSD", "x": 10, "y": 100}, {"text": "1.05", "x": 200, "y": 100}]
    fresh1 = ins.stitch_tokens(items, seen, page1, origin_x=0)
    assert len(fresh1) == 2

    # rolou: 'EURUSD' voltou no mesmo X mas Y diferente → MESMA linha, dedupada
    page2 = [{"text": "EURUSD", "x": 10, "y": 40}, {"text": "GBPUSD", "x": 10, "y": 120}]
    fresh2 = ins.stitch_tokens(items, seen, page2, origin_x=0)
    assert [r["text"] for r in fresh2] == ["GBPUSD"]       # só o novo entrou
    assert len(items) == 3                                  # EURUSD, 1.05, GBPUSD


def test_stitch_ignora_vazio():
    items, seen = [], set()
    ins.stitch_tokens(items, seen, [{"text": "   ", "x": 5, "y": 5}], origin_x=0)
    assert items == []


def test_resolve_anchor_bucket():
    toks = [{"text": "EURUSD", "x": 10, "y": 1}, {"text": "1.05", "x": 200, "y": 1}]
    assert ins.resolve_anchor_bucket(None, toks, 0) is None      # default desligado
    assert ins.resolve_anchor_bucket(False, toks, 0) is None
    # 'auto' = coluna mais à esquerda
    assert ins.resolve_anchor_bucket("auto", toks, 0) == ins._col_bucket(10, 0)
    assert ins.resolve_anchor_bucket(True, toks, 0) == ins._col_bucket(10, 0)
    # int = a coluna que contém aquele x
    assert ins.resolve_anchor_bucket(200, toks, 0) == ins._col_bucket(200, 0)


def test_anchor_dedup_ignora_colunas_volateis():
    """Com âncora na 1ª coluna (ticker), preços que MUDAM a cada frame não inflam
    nem fragmentam a lista — fica só a coluna estável, dedupada."""
    items, seen = [], set()
    anchor = ins._col_bucket(10, 0)        # coluna do ticker (x~10)
    # frame 1: ticker + preço P1
    f1 = [{"text": "EURUSD", "x": 10, "y": 100}, {"text": "1.0500", "x": 200, "y": 100}]
    ins.stitch_tokens(items, seen, f1, 0, anchor_bucket=anchor)
    # frame 2 (rolou de leve): MESMO ticker, preço MUDOU (volátil) + ticker novo
    f2 = [{"text": "EURUSD", "x": 10, "y": 60}, {"text": "1.0599", "x": 200, "y": 60},
          {"text": "GBPUSD", "x": 10, "y": 140}, {"text": "1.2700", "x": 200, "y": 140}]
    ins.stitch_tokens(items, seen, f2, 0, anchor_bucket=anchor)
    textos = [r["text"] for r in items]
    assert textos == ["EURUSD", "GBPUSD"]   # só tickers; preços voláteis ignorados


def test_sem_anchor_mantem_todas_as_colunas():
    """Sem âncora (default), o comportamento NÃO muda: todas as colunas entram."""
    items, seen = [], set()
    f = [{"text": "EURUSD", "x": 10, "y": 100}, {"text": "1.0500", "x": 200, "y": 100}]
    ins.stitch_tokens(items, seen, f, 0)    # anchor_bucket default None
    assert {r["text"] for r in items} == {"EURUSD", "1.0500"}


def test_match_token_contains_normalizado():
    toks = [{"text": "EURUSD", "x": 10, "y": 1}, {"text": "Apple Inc", "x": 10, "y": 2}]
    assert ins.match_token(toks, "apple")["text"] == "Apple Inc"
    assert ins.match_token(toks, "eur")["x"] == 10
    assert ins.match_token(toks, "zzz") is None
    assert ins.match_token(toks, "") is None


# ── IoU / dedup de boxes (fallback de visão) ───────────────────────

def test_iou_e_dedup_boxes():
    a = [0, 0, 100, 100]
    b = [2, 2, 100, 100]          # quase igual a `a` (contorno aninhado)
    c = [500, 500, 50, 50]        # disjunto
    assert ins._iou(a, b) > 0.85
    assert ins._iou(a, c) == 0.0
    out = ins._dedup_boxes([a, b, c])
    assert len(out) == 2          # a/b colapsam; c sobra
    assert c in out


def test_label_for_box_pega_token_mais_alto():
    box = [0, 0, 100, 100]
    toks = [{"text": "linha", "x": 10, "y": 80}, {"text": "TÍTULO", "x": 10, "y": 5},
            {"text": "fora", "x": 999, "y": 999}]
    assert ins._label_for_box(box, toks, 0, 0) == "TÍTULO"
    assert ins._label_for_box([0, 0, 1, 1], toks, 0, 0) == ""   # nada dentro


# ── Loop do enumerate com engine fake ──────────────────────────────

class _Desktop:
    def __init__(self):
        self.scrolls = []
        self.clicks = []
    def mouse_move(self, x, y):
        pass
    def mouse_scroll(self, lines):
        self.scrolls.append(lines)
    def mouse_click(self, x, y, button="left", double=False):
        self.clicks.append((x, y))


class _Vision:
    def __init__(self, pages, changed):
        self._pages = list(pages)
        self._changed = list(changed)
    def ocr_tokens(self, path, region=None):
        return self._pages.pop(0) if self._pages else []
    def region_changed(self, before, after, region=None):
        return self._changed.pop(0) if self._changed else False


class _Engine:
    """Engine mínimo com a superfície que o enumerate usa (sem display)."""
    def __init__(self, pages, changed, region=None):
        self.desktop = _Desktop()
        self.vision = _Vision(pages, changed)
        self._region_val = region or {"x": 0, "y": 0, "width": 200, "height": 300}
    def _stage_title(self):
        return None            # sem UIA → caminho OCR puro (wheel)
    def _uia(self):
        return None
    def _vorigin(self):
        return (0, 0)
    def _region(self, _):
        return self._region_val
    def screenshot(self, label="x"):
        return "fake.png"
    def _screenshot(self, label):
        return "fake.png"
    def _log(self, *_a):
        pass


def test_enumerate_varre_ate_o_fim_exhausted():
    pages = [
        [{"text": "A", "x": 10, "y": 10}],
        [{"text": "B", "x": 10, "y": 10}],
        [{"text": "C", "x": 10, "y": 10}],
    ]
    # 2 rolagens movem (True), a 3ª não move → fim
    eng = _Engine(pages, changed=[True, True, False])
    out = ins.enumerate_container(eng, max_pages=6)
    assert out["success"] is True
    assert out["exhausted"] is True
    assert [r["text"] for r in out["items"]] == ["A", "B", "C"]
    assert "cursor" not in out


def test_enumerate_find_curto_circuita():
    pages = [
        [{"text": "A", "x": 10, "y": 10}],
        [{"text": "ALVO aqui", "x": 10, "y": 50}],   # casa na 1ª rolagem
        [{"text": "C", "x": 10, "y": 10}],
    ]
    eng = _Engine(pages, changed=[True, True, False])
    out = ins.enumerate_container(eng, find="alvo", click=True)
    assert out["found"] is True
    assert out["click_point"] == {"x": 10, "y": 50}
    assert out["clicked"] is True
    assert eng.desktop.clicks == [(10, 50)]
    # parou ao achar: não chegou a exaurir nem pediu cursor
    assert out.get("exhausted") is False


def test_enumerate_find_ausente_exhausted_da_certeza():
    pages = [[{"text": "A", "x": 10, "y": 10}], [{"text": "B", "x": 10, "y": 10}]]
    eng = _Engine(pages, changed=[True, False])
    out = ins.enumerate_container(eng, find="inexistente")
    assert out["found"] is False
    assert out["exhausted"] is True
    assert "não está na lista" in out["hint"].lower() or "nao esta" in out["hint"].lower()


def test_enumerate_paginacao_devolve_cursor():
    # nunca para de mover e nunca acha → estoura max_pages → cursor de retomada
    pages = [[{"text": f"row{i}", "x": 10, "y": 10}] for i in range(10)]
    eng = _Engine(pages, changed=[True] * 10)
    out = ins.enumerate_container(eng, max_pages=2)
    assert out["exhausted"] is False
    assert out["cursor"] == "continue"
    assert out["pages"] == 2


def test_enumerate_cursor_nao_rola_ao_topo():
    """Retomada (cursor) NÃO deve rolar ao topo (60) — continua de onde está."""
    pages = [[{"text": "X", "x": 10, "y": 10}]]
    eng = _Engine(pages, changed=[False])
    ins.enumerate_container(eng, cursor="continue")
    assert 60 not in eng.desktop.scrolls        # sem o scroll-to-top inicial


def test_enumerate_sem_alvo_erro_limpo():
    eng = _Engine([], [], region=None)
    eng._region_val = None
    out = ins.enumerate_container(eng)
    assert out["success"] is False
    assert "target_window" in out["error"]
