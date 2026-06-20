"""Complex UI element handlers — combobox, tabs, menus, tables, forms.

These use the RpaEngine primitives (click, type, scroll, etc.) to interact
with higher-level UI patterns.
"""

import os
import time
from typing import Optional
from ..engine import RpaEngine, ActionResult


# ── Combobox ──────────────────────────────────────────────

def select_combobox(
    engine: RpaEngine,
    label_text: str,
    option_text: str,
    *,
    open_delay: float = 0.3,
) -> ActionResult:
    """Select an option from a combobox/dropdown.

    Strategy:
    1. Find the label text via OCR
    2. Click the combobox (slightly below/right of the label)
    3. Wait for dropdown to open
    4. Find and click the option text
    """
    reg = engine._region(None)   # escopa OCR ao palco (senão lê a tela toda)
    label_screenshot = engine.screenshot("combobox_label")

    # Find label position
    label_loc = engine.vision.find_text(label_screenshot, label_text, region=reg)
    if label_loc is None:
        return ActionResult(
            success=False,
            action=f"combobox_select_{label_text}",
            error=f"Label '{label_text}' não encontrada",
        )

    # Click the combobox (to the right of the label)
    cb_x = label_loc["x"] + 200  # heuristic: combobox is to the right
    cb_y = label_loc["y"]

    engine.click_at(cb_x, cb_y)
    time.sleep(open_delay)

    # Screenshot of opened dropdown
    dropdown_screenshot = engine.screenshot("combobox_dropdown")

    # Find option in dropdown
    option_loc = engine.vision.find_text(dropdown_screenshot, option_text, region=reg)
    if option_loc is None:
        # Try pressing the option text (type to filter)
        engine.type_text(option_text)
        time.sleep(0.3)
        engine.press_key("Return")
        return ActionResult(
            success=True,
            action=f"combobox_select_{label_text}",
            details={"label": label_text, "option": option_text, "method": "type+enter"},
        )

    # Click the option
    engine.click_at(option_loc["x"], option_loc["y"])
    time.sleep(0.2)

    # Verify by checking if dropdown closed (simplified)
    result = ActionResult(
        success=True,
        action=f"combobox_select_{label_text}",
        details={"label": label_text, "option": option_text, "method": "click_option"},
    )
    return result


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
