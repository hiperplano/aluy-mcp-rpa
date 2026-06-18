"""Desktop backend — actions on screen via python3-xlib + XTest + mss.

Dependencies: python3-xlib, mss, Pillow.
No external binaries or pyautogui needed.
"""

import os
import time
import sys
from typing import Optional


class DesktopBackend:
    """Low-level desktop actions (mouse, keyboard, screen) using Xlib + XTest."""

    def __init__(self):
        display = os.environ.get("DISPLAY", ":10")
        os.environ["DISPLAY"] = display

        # Xlib
        from Xlib import display as xdisplay
        from Xlib.ext.xtest import fake_input as xtest_fake

        self._display = xdisplay.Display()
        self._root = self._display.screen().root
        self._xtest = xtest_fake  # function

        self.screen_width = self._display.screen().width_in_pixels
        self.screen_height = self._display.screen().height_in_pixels

        # mss for screenshots
        from mss import mss
        self._mss = mss()

        # Build keycode cache for common keys
        self._keycode_cache = {}
        self._build_keymap()

        print(f"[rpa] Desktop backend: Xlib+XTest+mss "
              f"({self.screen_width}x{self.screen_height})",
              file=sys.stderr)

    def _build_keymap(self):
        """Pre-compute keycodes for common keys."""
        from Xlib import X

        # Map of key name → keysym name for X
        self._key_name_map = {
            "return": "Return", "enter": "Return",
            "escape": "Escape", "esc": "Escape",
            "tab": "Tab",
            "space": "space",
            "backspace": "BackSpace",
            "delete": "Delete", "del": "Delete",
            "up": "Up", "down": "Down", "left": "Left", "right": "Right",
            "home": "Home", "end": "End",
            "pageup": "Page_Up", "pagedown": "Page_Down",
            "insert": "Insert",
            "print": "Print",
            "pause": "Pause",
            "f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
            "f5": "F5", "f6": "F6", "f7": "F7", "f8": "F8",
            "f9": "F9", "f10": "F10", "f11": "F11", "f12": "F12",
        }

        # Pre-cache common keycodes
        common = ["Return", "Escape", "Tab", "space", "BackSpace", "Delete",
                   "Up", "Down", "Left", "Right", "Home", "End",
                   "Page_Up", "Page_Down", "Insert"]
        for i in range(1, 13):
            common.append(f"F{i}")
        # Letters
        for c in "abcdefghijklmnopqrstuvwxyz":
            common.append(c)
        # Digits
        for d in "0123456789":
            common.append(d)
        # Symbols
        for s in "!@#$%^&*()_+-=[]{}|;:',.<>?/`~\" ":
            common.append(s)

        for name in common:
            try:
                kc = self._display.keysym_to_keycode(X.string_to_keysym(name))
                if kc:
                    self._keycode_cache[name] = kc
            except Exception:
                pass

    def _get_keycode(self, key_name: str) -> int:
        """Get X keycode for a key name. Returns 0 if not found."""
        from Xlib import X

        if key_name in self._keycode_cache:
            return self._keycode_cache[key_name]

        try:
            keysym = X.string_to_keysym(key_name)
            if keysym == 0:
                return 0
            kc = self._display.keysym_to_keycode(keysym)
            if kc:
                self._keycode_cache[key_name] = kc
            return kc
        except Exception:
            return 0

    def _fake_key(self, keycode: int, press: bool):
        """Send key press/release via XTest."""
        from Xlib import X
        self._xtest(
            self._display,
            X.KeyPress if press else X.KeyRelease,
            keycode,
        )
        self._display.sync()

    def _fake_button(self, button: int, press: bool):
        """Send mouse button press/release via XTest."""
        from Xlib import X
        self._xtest(
            self._display,
            X.ButtonPress if press else X.ButtonRelease,
            button,
        )
        self._display.sync()

    def _fake_motion(self, x: int, y: int):
        """Move mouse via XTest."""
        # XTest fake_input(display, MotionNotify=6, detail=0 (absolute), x, y)
        self._xtest(self._display, 6, 0, x, y)
        self._display.sync()

    # ── Mouse ──────────────────────────────────────────────

    def mouse_move(self, x: int, y: int):
        self._fake_motion(x, y)

    def mouse_click(self, x: int, y: int, button: str = "left", double: bool = False):
        btn_map = {"left": 1, "middle": 2, "right": 3}
        btn = btn_map.get(button, 1)

        self._fake_motion(x, y)
        time.sleep(0.02)

        self._fake_button(btn, True)
        time.sleep(0.02)
        self._fake_button(btn, False)

        if double:
            time.sleep(0.05)
            self._fake_button(btn, True)
            time.sleep(0.02)
            self._fake_button(btn, False)

    def mouse_drag(self, from_x: int, from_y: int, to_x: int, to_y: int):
        self._fake_motion(from_x, from_y)
        time.sleep(0.02)
        self._fake_button(1, True)  # button down

        # Move in steps
        steps = 20
        for i in range(1, steps + 1):
            t = i / steps
            x = int(from_x + (to_x - from_x) * t)
            y = int(from_y + (to_y - from_y) * t)
            self._fake_motion(x, y)
            time.sleep(0.01)

        self._fake_button(1, False)  # button up

    def mouse_scroll(self, lines: int):
        """Positive = up (button 4), negative = down (button 5)."""
        btn = 4 if lines > 0 else 5
        for _ in range(abs(lines)):
            self._fake_button(btn, True)
            self._fake_button(btn, False)
            time.sleep(0.01)

    # ── Keyboard ───────────────────────────────────────────

    def type_text(self, text: str):
        """Type text character by character."""
        for ch in text:
            kc = self._get_keycode(ch)
            if kc == 0:
                continue
            self._fake_key(kc, True)
            self._fake_key(kc, False)
            time.sleep(0.005)

    def press_key(self, key: str):
        """Press a key or combo (e.g., 'ctrl+c', 'alt+F4', 'Return', 'Escape')."""
        from Xlib import X

        parts = key.split("+")

        # Identify modifier keys
        mod_map = {
            "ctrl": X.ControlMask,
            "alt": X.Mod1Mask,
            "shift": X.ShiftMask,
            "super": X.Mod4Mask,
            "win": X.Mod4Mask,
            "meta": X.Mod4Mask,
        }

        modifiers_to_press = []
        main_key_name = parts[-1]

        for part in parts[:-1]:
            pl = part.lower()
            if pl in mod_map:
                modifiers_to_press.append(pl)

        # Map main key name
        resolved = self._key_name_map.get(main_key_name.lower(), main_key_name)

        # Get keycode
        kc = self._get_keycode(resolved)
        if kc == 0 and len(resolved) == 1:
            kc = self._get_keycode(resolved.lower())

        if kc == 0:
            print(f"[rpa] Aviso: tecla '{key}' não mapeada", file=sys.stderr)
            return

        # Press modifiers
        for mod in modifiers_to_press:
            mod_keysym_name = mod.capitalize() if mod != "ctrl" else "Control_L"
            mod_kc = self._get_keycode(mod_keysym_name)
            if mod_kc:
                self._fake_key(mod_kc, True)

        # Press main key
        self._fake_key(kc, True)
        self._fake_key(kc, False)

        # Release modifiers (reverse order)
        for mod in reversed(modifiers_to_press):
            mod_keysym_name = mod.capitalize() if mod != "ctrl" else "Control_L"
            mod_kc = self._get_keycode(mod_keysym_name)
            if mod_kc:
                self._fake_key(mod_kc, False)

    # ── Screenshot ─────────────────────────────────────────

    def screenshot(self, path: str):
        """Take full-screen screenshot to path (PNG)."""
        self._mss.shot(output=path)

    def get_pixel_color(self, x: int, y: int) -> str:
        """Return #RRGGBB of pixel at (x,y)."""
        img = self._mss.grab({"left": x, "top": y, "width": 1, "height": 1})
        from PIL import Image
        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        r, g, b = pil_img.getpixel((0, 0))
        return f"#{r:02x}{g:02x}{b:02x}"
