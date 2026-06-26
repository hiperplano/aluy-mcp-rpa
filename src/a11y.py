"""a11y-first como FALLBACK rápido/preciso.

Quando a árvore de acessibilidade (AT-SPI) está disponível, localizar um elemento
por nome é exato e instantâneo — sem OCR. É o caminho preferido do UiPath
(selectors). Aqui é um FALLBACK opcional: o motor continua CV-first (surface-agnostic,
cobre Wine/canvas onde NÃO há a11y); o a11y só acelera/precisa quando o app expõe a
árvore (GTK/Qt nativos com acessibilidade ligada).

Tudo best-effort: qualquer falha ⇒ retorna None e o caminho CV (OCR) assume.
"""
import sys

_ACTIONABLE = {
    "push button", "toggle button", "check box", "radio button", "radio menu item",
    "menu item", "menu", "link", "page tab", "list item", "table cell", "entry", "text",
}


def _atspi():
    import gi
    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi
    return Atspi


def available() -> bool:
    """True se o registro AT-SPI está vivo e os bindings importam."""
    try:
        A = _atspi()
        A.get_desktop(0)
        return True
    except Exception:
        return False


def _extents(A, node):
    try:
        ext = A.Component.get_extents(node, A.CoordType.SCREEN)
        return int(ext.x), int(ext.y), int(ext.width), int(ext.height)
    except Exception:
        return None


def find_text(text: str, *, max_nodes: int = 6000):
    """Acha um widget cujo NOME contém `text` e devolve seu ponto de clique
    absoluto {x, y, role, name}, preferindo elementos acionáveis e match exato.
    None se a11y indisponível ou nada encontrado (⇒ cai pro OCR)."""
    try:
        A = _atspi()
        desk = A.get_desktop(0)
    except Exception:
        return None

    t = (text or "").strip().lower()
    if not t:
        return None
    state = {"best": None, "best_score": -1, "seen": 0}

    def score(role, name):
        s = 0
        if name.lower() == t:
            s += 4
        if role in _ACTIONABLE:
            s += 2
        return s

    def walk(node):
        if state["seen"] >= max_nodes or state["best_score"] >= 6:
            return
        state["seen"] += 1
        try:
            nm = (node.get_name() or "").strip()
            role = node.get_role_name() or ""
        except Exception:
            nm, role = "", ""
        if nm and t in nm.lower():
            e = _extents(A, node)
            if e and e[2] > 0 and e[3] > 0 and e[0] >= 0 and e[1] >= 0:
                sc = score(role, nm)
                if sc > state["best_score"]:
                    state["best"] = {"x": e[0] + e[2] // 2, "y": e[1] + e[3] // 2,
                                     "role": role, "name": nm}
                    state["best_score"] = sc
        try:
            n = node.get_child_count()
        except Exception:
            n = 0
        for i in range(n):
            try:
                walk(node.get_child_at_index(i))
            except Exception:
                pass

    try:
        for i in range(desk.get_child_count()):
            try:
                walk(desk.get_child_at_index(i))
            except Exception:
                pass
    except Exception:
        return None
    return state["best"]
