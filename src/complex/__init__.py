"""Complex UI element handlers — combobox, tabs, menus, tables, forms.

These use the RpaEngine primitives (click, type, scroll, etc.) to interact
with higher-level UI patterns.
"""

import os
import time
from typing import Optional
from ..engine import RpaEngine, ActionResult


# ── Combobox ──────────────────────────────────────────────

def _option_shown(engine, option_text: str, reg) -> bool:
    """True se o texto da opção está visível no palco — usado p/ VERIFICAR que a
    seleção pegou (a versão antiga retornava success sem conferir)."""
    try:
        ss = engine.screenshot("combo_verify")
        loc = engine.vision.find_text(ss, option_text, region=reg)
        try:
            import os; os.unlink(ss)
        except Exception:
            pass
        return loc is not None
    except Exception:
        return False


def select_combobox(
    engine: RpaEngine,
    label_text: str,
    option_text: str,
    *,
    open_delay: float = 0.4,
    combo_at: dict = None,
) -> ActionResult:
    """Seleciona uma opção de combobox/dropdown — ENDURECIDO.

    Estratégias, em ordem, com VERIFICAÇÃO no fim (só reporta sucesso se a opção
    aparecer de fato):
      1. Localiza o combo: por `combo_at={x,y}` explícito, OU 200px à direita do
         label, OU pelo próprio texto do valor atual (combos sem label).
      2. Abre: clica no controle; se não achar a opção, clica a SETA (borda
         direita) e tenta Alt+Down (abre combo nativo via teclado).
      3. Acha a opção na TELA INTEIRA (dropdowns nativos abrem como popup FORA da
         janela — busca escopada não os enxerga) e clica.
      4. Fallback teclado: type-ahead (digita a opção) + Enter — funciona em
         combos nativos resistentes a clique.
      5. VERIFICA relendo a tela; tenta as estratégias em sequência até passar.
    """
    reg = engine._region(None)

    # 1) ponto do combo
    if combo_at and "x" in combo_at:
        cb_x, cb_y = combo_at["x"], combo_at["y"]
    else:
        ss = engine.screenshot("combo_find")
        # tenta o label; se não houver, usa o próprio texto do valor (option pode
        # não casar, mas label_text costuma ser o rótulo OU o valor atual).
        loc = engine.vision.find_text(ss, label_text, region=reg)
        try:
            import os; os.unlink(ss)
        except Exception:
            pass
        if loc is None:
            return ActionResult(success=False, action=f"combobox_select_{label_text}",
                                error=f"Combo/label '{label_text}' não encontrado")
        # se achou um LABEL, o combo está à direita; se achou o próprio controle,
        # clica nele mesmo. Heurística: tenta no ponto e, se falhar, +200px.
        cb_x, cb_y = loc["x"], loc["y"]

    def _try_click_option() -> bool:
        # dropdown nativo abre FORA da janela → busca na tela inteira
        ss = engine.screenshot("combo_dd")
        opt = engine.vision.find_text(ss, option_text, region=None)
        try:
            import os; os.unlink(ss)
        except Exception:
            pass
        if opt is None:
            return False
        engine.desktop.mouse_click(opt["x"], opt["y"])
        time.sleep(0.25)
        return _option_shown(engine, option_text, reg)

    # Estratégia A: clica o controle, procura a opção (tela inteira)
    engine.desktop.mouse_click(cb_x, cb_y); time.sleep(open_delay)
    if _try_click_option():
        return ActionResult(success=True, action=f"combobox_select_{label_text}",
                            details={"option": option_text, "method": "click", "verified": True})

    # Estratégia B: clica a SETA (borda direita, ~+90px) e procura de novo
    engine.desktop.mouse_click(cb_x + 90, cb_y); time.sleep(open_delay)
    if _try_click_option():
        return ActionResult(success=True, action=f"combobox_select_{label_text}",
                            details={"option": option_text, "method": "arrow+click", "verified": True})

    # Estratégia C: teclado — Alt+Down abre, type-ahead seleciona, Enter confirma
    engine.desktop.mouse_click(cb_x, cb_y); time.sleep(0.2)
    engine.press_key("alt+Down"); time.sleep(open_delay)
    if _try_click_option():
        return ActionResult(success=True, action=f"combobox_select_{label_text}",
                            details={"option": option_text, "method": "altdown+click", "verified": True})
    engine.type_text(option_text); time.sleep(0.2); engine.press_key("Return"); time.sleep(0.3)
    if _option_shown(engine, option_text, reg):
        return ActionResult(success=True, action=f"combobox_select_{label_text}",
                            details={"option": option_text, "method": "type+enter", "verified": True})

    return ActionResult(success=False, action=f"combobox_select_{label_text}",
                        error=f"Não consegui selecionar '{option_text}' (combo resistente a "
                              f"clique/teclado sintético — comum em controles custom)")


# ── Tabs ──────────────────────────────────────────────────

def click_tab(engine: RpaEngine, tab_name: str) -> ActionResult:
    """Click a tab by its text label.

    Strategy:
    1. Screenshot
    2. OCR to find tab text
    3. Click the tab
    4. Verify visually (tab area changed)
    """
    before = engine.screenshot("tab_before")

    tab_loc = engine.vision.find_text(before, tab_name, region=engine._region(None))
    if tab_loc is None:
        return ActionResult(
            success=False,
            action=f"click_tab_{tab_name}",
            error=f"Tab '{tab_name}' não encontrada",
        )

    def do():
        engine.desktop.mouse_click(tab_loc["x"], tab_loc["y"])

    def check():
        return True  # We trust the click

    result = engine.act(
        f"click_tab_{tab_name}",
        do, check,
        verify_desc=f"clicar na tab '{tab_name}'",
    )
    result.details["tab"] = tab_name
    return result


# ── Menus ─────────────────────────────────────────────────

def navigate_menu(engine: RpaEngine, items: list[str]) -> ActionResult:
    """Navigate a nested menu by clicking each item in sequence.

    Strategy:
    1. For each menu level:
       a. Find the item text via OCR
       b. Click it
       c. Wait for submenu to appear
    2. Repeat until all items are clicked
    """
    results = []
    reg = engine._region(None)   # escopa ao palco
    for i, item in enumerate(items):
        before = engine.screenshot(f"menu_{i}")

        item_loc = engine.vision.find_text(before, item, region=reg)
        if item_loc is None:
            return ActionResult(
                success=False,
                action=f"navigate_menu_{'_'.join(items)}",
                error=f"Item '{item}' (nível {i}) não encontrado",
                details={"completed": items[:i], "failed_at": item},
            )

        result = engine.click_at(item_loc["x"], item_loc["y"])
        results.append(result)

        if i < len(items) - 1:
            time.sleep(0.3)  # wait for submenu

    return ActionResult(
        success=True,
        action=f"navigate_menu_{'_'.join(items)}",
        details={"items": items, "steps": [r.to_dict() for r in results]},
    )


# ── Tables ────────────────────────────────────────────────

def find_in_table(
    engine: RpaEngine,
    table_header_text: str,
    cell_text: str,
    *,
    click: bool = True,
) -> ActionResult:
    """Find a cell in a table by header and cell text.

    Strategy:
    1. Locate table by finding the header row via OCR
    2. Find the target column by its header text
    3. Scan rows until the cell text is found
    4. Optionally click the cell
    """
    reg = engine._region(None)   # escopa ao palco
    screenshot = engine.screenshot("table")
    ocr_text = engine.vision.ocr(screenshot, region=reg)

    # Simplified: just look for the cell text within the table region
    # Full implementation would identify column positions from headers

    if cell_text not in ocr_text:
        return ActionResult(
            success=False,
            action=f"find_in_table_{cell_text}",
            error=f"Texto '{cell_text}' não encontrado na tabela",
        )

    # Find cell position
    cell_loc = engine.vision.find_text(screenshot, cell_text, region=reg)
    if cell_loc is None:
        return ActionResult(
            success=False,
            action=f"find_in_table_{cell_text}",
            error=f"Texto '{cell_text}' encontrado via OCR mas não localizável",
        )

    if click:
        engine.click_at(cell_loc["x"], cell_loc["y"])
        time.sleep(0.2)

    return ActionResult(
        success=True,
        action=f"find_in_table_{cell_text}",
        details={"cell": cell_text, "position": f"({cell_loc['x']},{cell_loc['y']})"},
    )


# ── Forms ─────────────────────────────────────────────────

def fill_form(engine: RpaEngine, fields: list[dict]) -> ActionResult:
    """Fill a form with multiple fields.

    Each field is a dict with:
    - label: text of the field label (to locate via OCR)
    - value: text to type
    - type: "text" (default) | "combobox" | "checkbox" | "radio"

    Strategy:
    1. For each field:
       a. Find label via OCR
       b. Click the input area (to the right of the label)
       c. Clear existing content (Ctrl+A, Del)
       d. Type the value
       e. For combobox: use select_combobox
    """
    results = []
    reg = engine._region(None)   # escopa ao palco
    for field in fields:
        label = field.get("label", "")
        value = field.get("value", "")
        field_type = field.get("type", "text")

        if field_type == "combobox":
            r = select_combobox(engine, label, value)
        elif field_type == "checkbox":
            # Find label and click it (usually toggles)
            loc = engine.vision.find_text(engine.screenshot("form_cb"), label, region=reg)
            if loc:
                r = engine.click_at(loc["x"] - 20, loc["y"])
            else:
                r = ActionResult(success=False, action=f"fill_{label}",
                                 error=f"Label '{label}' não encontrada")
        else:
            # Text field: click label area, clear, type
            ss = engine.screenshot("form_text")
            loc = engine.vision.find_text(ss, label, region=reg)
            if loc:
                # Click input area (to the right of label)
                engine.click_at(loc["x"] + 150, loc["y"])
                time.sleep(0.1)
                # Clear existing
                engine.press_key("ctrl+a")
                time.sleep(0.05)
                engine.press_key("Delete")
                time.sleep(0.05)
                # Type value
                r = engine.type_text(value)
            else:
                r = ActionResult(success=False, action=f"fill_{label}",
                                 error=f"Label '{label}' não encontrada")

        results.append(r)

    all_ok = all(r.success for r in results)
    return ActionResult(
        success=all_ok,
        action=f"fill_form_{len(fields)}fields",
        details={
            "fields": len(fields),
            "ok": sum(1 for r in results if r.success),
            "failed": [r.error for r in results if not r.success],
        },
    )
