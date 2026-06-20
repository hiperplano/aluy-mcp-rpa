"""Backend UI Automation (UIA) — **SÓ Windows**.

Localiza controles pela ÁRVORE DE ACESSIBILIDADE (nome + tipo) e age com a AÇÃO
NATIVA do controle — Invoke (botão/menu), SetValue (campo), Select (item de
lista), Expand (combo). É EXATO (retângulo real do elemento), SEMÂNTICO (sabe que
é um Button "Buy" ou um Edit) e dispensa OCR/coordenada/foco. É o **nível 1** do
localizador do orquestrador; OCR e template são o fallback para controles
custom-desenhados (gráfico do MT5, canvas, jogos) que NÃO expõem árvore.

AMBIENTE (importante): este módulo é Windows-only. No Linux/macOS `available()`
retorna False e nada aqui é importado — o caminho de acessibilidade do Linux é o
AT-SPI (`a11y.py`). A dependência (`uiautomation`/`comtypes`) é OPCIONAL e só
instalada no Windows; o import é lazy e protegido, então a ausência degrada para
o OCR sem quebrar. Toda função aqui é best-effort: nunca levanta — retorna
None/False/[] e deixa o chamador cair no fallback.
"""
import sys

_CHECKED = None  # cache do available()


def available() -> bool:
    """True só no Windows COM a lib `uiautomation` instalada e importável."""
    global _CHECKED
    if _CHECKED is not None:
        return _CHECKED
    ok = False
    if sys.platform == "win32":
        try:
            import importlib.util
            ok = importlib.util.find_spec("uiautomation") is not None
        except Exception:
            ok = False
    _CHECKED = ok
    return ok


def _auto():
    """Importa uiautomation sob demanda e garante COM init na thread atual
    (o server roda os handlers em threads do asyncio)."""
    import uiautomation as auto
    try:
        import comtypes
        comtypes.CoInitializeEx()  # idempotente por thread; ignora se já init
    except Exception:
        pass
    return auto


# ── Patterns suportados (para perceber/decidir a ação) ─────────
_PATTERNS = [
    ("value", "GetValuePattern"),         # SetValue — campos de texto/número
    ("invoke", "GetInvokePattern"),       # Invoke — botões/menus
    ("selitem", "GetSelectionItemPattern"),  # Select — item de lista/aba/radio
    ("expand", "GetExpandCollapsePattern"),  # Expand — combos
    ("toggle", "GetTogglePattern"),       # Toggle — checkbox
    ("scroll", "GetScrollPattern"),       # Scroll — listas/painéis/documentos
]


def _supported(ctrl):
    out = []
    for nm, fn in _PATTERNS:
        try:
            if getattr(ctrl, fn)():
                out.append(nm)
        except Exception:
            pass
    return out


def _rect(ctrl, vleft=0, vtop=0):
    """Retângulo do controle em PIXEL-DA-IMAGEM (subtrai a origem virtual, igual
    o PortableBackend) e o ponto central p/ clique se preciso."""
    try:
        r = ctrl.BoundingRectangle
        x, y, w, h = int(r.left) - vleft, int(r.top) - vtop, int(r.width()), int(r.height())
        return {"x": x, "y": y, "width": w, "height": h,
                "cx": x + w // 2, "cy": y + h // 2}
    except Exception:
        return None


def _windows(title):
    """Itera as janelas (top-level E modais aninhados) cujo nome contém `title`."""
    auto = _auto()
    t = (title or "").lower()
    root = auto.GetRootControl()
    out = []

    def visit(c, depth):
        if depth > 3:
            return
        try:
            for ch in c.GetChildren():
                tn = ch.ControlTypeName
                if tn in ("WindowControl", "PaneControl"):
                    if t in (ch.Name or "").lower():
                        out.append(ch)
                    visit(ch, depth + 1)
        except Exception:
            pass

    visit(root, 0)
    return out


def find(title, name=None, *, control_type=None, vleft=0, vtop=0):
    """Acha um controle dentro da janela `title`, por NOME (substring, exato
    primeiro) e opcionalmente TIPO (ex.: 'Edit', 'Button', 'ComboBox'). Devolve
    um dict {name, type, patterns, rect, _ctrl} ou None.
    """
    if not available():
        return None
    try:
        wins = _windows(title)
        if not wins:
            return None
        nlow = (name or "").lower()
        ct = (control_type or "").lower()
        exact = partial = None
        seen = [0]

        def walk(c, depth):
            if seen[0] > 400 or depth > 8:
                return
            seen[0] += 1
            try:
                cname = c.Name or ""
                ctname = c.ControlTypeName or ""  # ex.: 'EditControl'
                type_ok = (not ct) or ct in ctname.lower()
                if type_ok and (name is None or nlow in cname.lower()):
                    hit = {"name": cname, "type": ctname,
                           "patterns": _supported(c), "rect": _rect(c, vleft, vtop),
                           "_ctrl": c}
                    nonlocal_assign(hit, cname.lower() == nlow)
                for ch in c.GetChildren():
                    walk(ch, depth + 1)
            except Exception:
                pass

        def nonlocal_assign(hit, is_exact):
            nonlocal exact, partial
            if is_exact:
                if exact is None:
                    exact = hit
            elif partial is None:
                partial = hit

        for w in wins:
            walk(w, 0)
            if exact:
                break
        return exact or partial
    except Exception as e:
        print(f"[rpa] uia.find falhou: {e}", file=sys.stderr)
        return None


def invoke(title, name, *, control_type=None) -> bool:
    """Aciona (clica) um controle pela ação nativa Invoke — botões, menus, itens.
    Exato, sem coordenada nem foco."""
    el = find(title, name, control_type=control_type)
    if not el:
        return False
    try:
        ip = el["_ctrl"].GetInvokePattern()
        if ip:
            ip.Invoke()
            return True
    except Exception as e:
        print(f"[rpa] uia.invoke falhou: {e}", file=sys.stderr)
    return False


def set_value(title, name, value, *, control_type="Edit") -> bool:
    """Seta o valor de um campo via ValuePattern.SetValue — instantâneo e EXATO,
    sem digitar/limpar (resolve spinbox de preço/volume do MT5)."""
    el = find(title, name, control_type=control_type)
    if not el:
        return False
    try:
        vp = el["_ctrl"].GetValuePattern()
        if vp:
            vp.SetValue(str(value))
            return True
    except Exception as e:
        print(f"[rpa] uia.set_value falhou: {e}", file=sys.stderr)
    return False


def click_point(title, name, *, control_type=None, vleft=0, vtop=0):
    """Localiza um controle por nome e devolve o ponto de clique (centro do rect
    real) + se tem Invoke. O orquestrador usa: tem Invoke → invoke(); senão clica
    o ponto. É o caminho UIA do click_text (exato, sem OCR)."""
    el = find(title, name, control_type=control_type, vleft=vleft, vtop=vtop)
    if not el or not el.get("rect"):
        return None
    r = el["rect"]
    return {"x": r["cx"], "y": r["cy"], "name": el["name"],
            "can_invoke": "invoke" in el["patterns"],
            "can_expand": "expand" in el["patterns"], "_ctrl": el["_ctrl"]}


def _find_scrollable(title):
    """Melhor controle VERTICALMENTE rolável da janela (maior área = o conteúdo
    principal)."""
    if not available():
        return None
    try:
        wins = _windows(title)
        best = [None, -1]
        n = [0]

        def walk(c, depth):
            if depth > 8 or n[0] > 400:
                return
            n[0] += 1
            try:
                sp = c.GetScrollPattern()
                if sp:
                    try:
                        vert = sp.VerticallyScrollable
                    except Exception:
                        vert = True
                    if vert:
                        r = c.BoundingRectangle
                        a = int(r.width()) * int(r.height())
                        if a > best[1]:
                            best[0], best[1] = c, a
                for ch in c.GetChildren():
                    walk(ch, depth + 1)
            except Exception:
                pass

        for w in wins:
            walk(w, 0)
        return best[0]
    except Exception:
        return None


def scroll(title, lines) -> bool:
    """Rola o controle rolável da janela via ScrollPattern — rola o controle
    CERTO, não 'o que estiver sob o cursor' (problema do wheel do pyautogui).
    lines>0 = cima, <0 = baixo. Best-effort; False se não houver rolável."""
    if not available():
        return False
    auto = _auto()
    c = _find_scrollable(title)
    if not c:
        return False
    try:
        sp = c.GetScrollPattern()
        # Increment = em direção ao fim (baixo); Decrement = início (cima).
        amount = auto.ScrollAmount.SmallDecrement if lines > 0 else auto.ScrollAmount.SmallIncrement
        for _ in range(min(abs(int(lines)), 60)):
            sp.Scroll(auto.ScrollAmount.NoAmount, amount)
        return True
    except Exception as e:
        print(f"[rpa] uia.scroll falhou: {e}", file=sys.stderr)
        return False


def set_focused_value(value) -> bool:
    """Se o controle FOCADO for um campo com Value (Edit), SetValue direto — sem
    teclado. Caminho UIA do type_text quando há um campo sob foco."""
    if not available():
        return False
    auto = _auto()
    try:
        c = auto.GetFocusedControl()
        if c:
            vp = c.GetValuePattern()
            if vp:
                try:
                    if vp.IsReadOnly:
                        return False
                except Exception:
                    pass
                vp.SetValue(str(value))
                return True
    except Exception as e:
        print(f"[rpa] uia.set_focused_value falhou: {e}", file=sys.stderr)
    return False


def dump(title, *, maxd=6, limit=120, vleft=0, vtop=0):
    """Lista os controles da janela (nome/tipo/patterns/rect) — serve de
    'perceber por acessibilidade': é o que o agente leria SEM OCR."""
    if not available():
        return []
    out = []
    try:
        wins = _windows(title)
        n = [0]

        def walk(c, depth):
            if depth > maxd or n[0] >= limit:
                return
            n[0] += 1
            try:
                nm, ct = c.Name or "", c.ControlTypeName or ""
                pats = _supported(c)
                if nm or pats:  # ignora nós vazios sem ação
                    out.append({"name": nm, "type": ct, "patterns": pats,
                                "rect": _rect(c, vleft, vtop)})
                for ch in c.GetChildren():
                    walk(ch, depth + 1)
            except Exception:
                pass

        for w in wins:
            walk(w, 0)
    except Exception as e:
        print(f"[rpa] uia.dump falhou: {e}", file=sys.stderr)
    return out
