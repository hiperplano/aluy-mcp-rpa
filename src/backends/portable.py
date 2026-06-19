"""Backend de ação PORTÁVEL — Windows / macOS (e Linux como proxy de teste).

Implementa a MESMA interface do DesktopBackend (Xlib), mas usando bibliotecas
cross-OS: pyautogui (mouse/teclado), pygetwindow (janelas — Win/Mac) e mss
(screenshot, já cross-OS). No Linux o backend padrão continua sendo o Xlib
(proveado, com XTest/XSendEvent); este aqui entra quando o OS é Windows/macOS.

ATENÇÃO: o caminho de JANELA (pygetwindow) só existe no Win/Mac — no Linux o
pygetwindow não importa. O input/screenshot (pyautogui/mss) é testável no Linux.
"""
import sys
import time
from typing import Optional

# Mapeamento de nomes de tecla "nossos" → nomes do pyautogui (minúsculos).
_KEY_MAP = {
    "return": "enter", "enter": "enter", "escape": "esc", "esc": "esc",
    "tab": "tab", "space": "space", "backspace": "backspace",
    "delete": "delete", "del": "delete", "up": "up", "down": "down",
    "left": "left", "right": "right", "home": "home", "end": "end",
    "pageup": "pageup", "pagedown": "pagedown", "insert": "insert",
    "ctrl": "ctrl", "control": "ctrl", "alt": "alt", "shift": "shift",
    "super": "win", "win": "win", "meta": "win", "cmd": "command",
}


class PortableBackend:
    """Ação cross-OS (Windows/macOS) via pyautogui + pygetwindow + mss."""

    def __init__(self, *, keyboard: str = "native"):
        import pyautogui
        self._pg = pyautogui
        self._pg.FAILSAFE = False
        try:
            import pygetwindow as gw
            self._gw = gw
        except Exception as e:
            self._gw = None
            print(f"[rpa] PortableBackend: janelas indisponíveis ({e}) — "
                  "pygetwindow só Win/Mac.", file=sys.stderr)
        from mss import mss
        self._mss = mss()
        self._focused_win = None
        self._keyboard = keyboard  # compat de interface (pyautogui resolve shift sozinho)
        w, h = self._pg.size()
        self.screen_width, self.screen_height = int(w), int(h)
        print(f"[rpa] Portable backend: pyautogui+pygetwindow+mss "
              f"({self.screen_width}x{self.screen_height})", file=sys.stderr)

    # ── Mouse ──────────────────────────────────────────────
    def mouse_move(self, x, y):
        self._pg.moveTo(int(x), int(y))

    def mouse_click(self, x, y, button="left", double=False):
        self._pg.click(int(x), int(y), clicks=2 if double else 1,
                       interval=0.05, button=button)

    def mouse_drag(self, fx, fy, tx, ty):
        self._pg.moveTo(int(fx), int(fy)); self._pg.dragTo(int(tx), int(ty), duration=0.3)

    def mouse_scroll(self, lines):
        self._pg.scroll(int(lines))

    # ── Teclado (pyautogui resolve shift/maiúsculas sozinho) ──
    def type_text(self, text):
        if self._focused_win is not None:
            try:
                self._focused_win.activate()
            except Exception:
                pass
        time.sleep(0.2)
        self._pg.typewrite(text, interval=0.02)

    def press_key(self, key):
        parts = [p.strip() for p in key.split("+")]
        keys = [_KEY_MAP.get(p.lower(), p.lower()) for p in parts]
        if len(keys) == 1:
            self._pg.press(keys[0])
        else:
            self._pg.hotkey(*keys)

    # ── Screenshot / pixel (mss = cross-OS) ────────────────
    def screenshot(self, path):
        from mss import mss as _mss
        with _mss() as m:
            m.shot(mon=-1, output=path)

    def get_pixel_color(self, x, y):
        try:
            r, g, b = self._pg.pixel(int(x), int(y))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return "#000000"

    # ── Janelas (pygetwindow — Win/Mac) ────────────────────
    def list_windows(self):
        out = []
        if not self._gw:
            return out
        try:
            for w in self._gw.getAllWindows():
                try:
                    if not w.title or w.width < 40 or w.height < 40:
                        continue
                    out.append({"id": id(w), "name": w.title, "x": int(w.left),
                                "y": int(w.top), "width": int(w.width),
                                "height": int(w.height), "_win": w})
                except Exception:
                    pass
        except Exception:
            pass
        return out

    def find_window(self, title):
        t = title.lower()
        matches = [w for w in self.list_windows() if t in w["name"].lower()]
        if not matches:
            return None
        return sorted(matches, key=lambda w: -w["width"] * w["height"])[0]

    def activate_window(self, win):
        w = win["_win"] if isinstance(win, dict) else win
        try:
            w.activate()
            self._focused_win = w
            time.sleep(0.2)
            return True
        except Exception as e:
            print(f"[rpa] activate_window (portable) falhou: {e}", file=sys.stderr)
            return False

    def window_geometry(self, win):
        w = win["_win"] if isinstance(win, dict) else win
        return {"x": int(w.left), "y": int(w.top),
                "width": int(w.width), "height": int(w.height)}
