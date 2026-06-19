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

    def __init__(self, *, keyboard: str = "xsendevent"):
        display = os.environ.get("DISPLAY", ":10")
        os.environ["DISPLAY"] = display

        # Método de teclado detectado: 'xtest' (universal, cobre Wine — onde o
        # servidor X aceita injeção de tecla) ou 'xsendevent' (xrdp, que descarta
        # XTest-tecla; funciona em Athena/GTK mas não em Wine).
        self._keyboard = keyboard

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

        # Window we last gave focus to (XSendEvent target for the keyboard).
        self._focused_win = None

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
        """Warp the pointer to absolute (x, y).

        NOTE: fake_input's signature is (display, event_type, detail, time, root,
        x, y) — passing x,y positionally after detail lands them in `time`/`root`,
        so the pointer never moves and clicks fire at the stale cursor position.
        x and y MUST be keyword args. We also warp_pointer as a belt-and-braces
        fallback so the cursor is truly at the target before the button event.
        """
        from Xlib import X
        self._xtest(self._display, X.MotionNotify, x=int(x), y=int(y), root=self._root)
        self._display.sync()
        try:
            self._root.warp_pointer(int(x), int(y))
            self._display.sync()
        except Exception:
            pass

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

    # XTest key injection is silently dropped by this Xorg/xrdp server (mouse
    # works, keys don't), even with focus correct. XSendEvent IS delivered, so
    # the keyboard goes through XSendEvent to the focused window instead.

    def _key_target(self):
        """Window to receive synthetic key events: the live input focus, falling
        back to the last window we activated, then the root."""
        from Xlib import X
        try:
            f = self._display.get_input_focus().focus
            if f not in (X.NONE, X.PointerRoot) and not isinstance(f, int):
                return f
        except Exception:
            pass
        return self._focused_win or self._root

    def _xkey(self, w, keycode, press, state):
        from Xlib import X
        from Xlib.protocol import event as xe
        klass = xe.KeyPress if press else xe.KeyRelease
        ev = klass(time=X.CurrentTime, root=self._root, window=w, same_screen=1,
                   child=X.NONE, root_x=0, root_y=0, event_x=0, event_y=0,
                   state=state, detail=keycode)
        try:
            w.send_event(ev, propagate=True)
        except Exception:
            pass

    def _send_key(self, keycode: int, state: int = 0, mods=()):
        """Send KeyPress+KeyRelease to the focused window via XSendEvent.

        `state` is the modifier mask; `mods` are modifier KEYCODES to actually
        hold down around the key — some toolkits (Athena/xcalc) re-derive the
        modifier from real Shift/Ctrl key events and ignore the `state` field.
        """
        w = self._key_target()
        for mkc in mods:
            self._xkey(w, mkc, True, state)
        self._xkey(w, keycode, True, state)
        self._xkey(w, keycode, False, state)
        for mkc in reversed(mods):
            self._xkey(w, mkc, False, state)
        self._display.sync()

    def _modcode(self, name: str) -> int:
        from Xlib import XK
        return self._display.keysym_to_keycode(XK.string_to_keysym(name)) or 0

    def _char_to_keycode_state(self, ch: str):
        """Resolve a character to (keycode, state) — state carries Shift when the
        glyph sits on the shifted level of its key (e.g. '*', '+', '=' vary)."""
        from Xlib import X
        # For a printable ASCII char the X keysym IS its code point (Latin-1).
        # NB: XK.string_to_keysym expects a keysym NAME ("minus","asterisk",...),
        # not the literal glyph, so it returns 0 for '-','*','=' — hence ord().
        keysym = ord(ch) if len(ch) == 1 and 0x20 <= ord(ch) <= 0xff else 0
        if keysym == 0:
            return None
        kc = self._display.keysym_to_keycode(keysym)
        if not kc:
            return None
        # Shift needed if the unshifted level isn't this keysym but the shifted is.
        lvl0 = self._display.keycode_to_keysym(kc, 0)
        lvl1 = self._display.keycode_to_keysym(kc, 1)
        state = X.ShiftMask if (keysym != lvl0 and keysym == lvl1) else 0
        return kc, state

    def type_text(self, text: str):
        """Type text into the focused window (XSendEvent, per-char shift).

        Re-asserts input focus on the target window and settles briefly BEFORE
        the first key — otherwise the leading characters are dropped while the
        widget (e.g. a just-focused GtkSourceView) finishes grabbing focus.
        """
        from Xlib import X
        if self._focused_win is not None:
            try:
                self._focused_win.set_input_focus(X.RevertToParent, X.CurrentTime)
                self._display.sync()
            except Exception:
                pass
        time.sleep(0.6)  # let focus settle so the first chars aren't lost
        for ch in text:
            res = self._char_to_keycode_state(ch)
            if not res:
                continue
            kc, state = res
            shift = bool(state & X.ShiftMask)
            self._emit_key(kc, shift)
            time.sleep(0.02)

    def _emit_key(self, kc: int, shift: bool):
        """Emite UMA tecla pelo método detectado (XTest universal, ou XSendEvent
        no xrdp). Aplica Shift de verdade quando preciso."""
        from Xlib import X
        if self._keyboard == "xtest":
            sk = self._modcode("Shift_L") if shift else 0
            if sk:
                self._fake_key(sk, True)
            self._fake_key(kc, True)
            self._fake_key(kc, False)
            if sk:
                self._fake_key(sk, False)
        else:
            mods = (self._modcode("Shift_L"),) if shift else ()
            mods = tuple(m for m in mods if m)
            self._send_key(kc, (X.ShiftMask if shift else 0), mods=mods)

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

        # Modifier state mask + the real modifier keycodes to hold down.
        mod_keysym = {"ctrl": "Control_L", "alt": "Alt_L", "shift": "Shift_L",
                      "super": "Super_L", "win": "Super_L", "meta": "Super_L"}
        state = 0
        mod_codes = []
        for mod in modifiers_to_press:
            state |= mod_map[mod]
            mc = self._modcode(mod_keysym[mod])
            if mc:
                mod_codes.append(mc)

        # Resolve the main key to a keycode (named key, else single char).
        resolved = self._key_name_map.get(main_key_name.lower(), main_key_name)
        kc = self._get_keycode(resolved)
        if kc == 0 and len(main_key_name) == 1:
            res = self._char_to_keycode_state(main_key_name)
            if res:
                kc, ch_state = res
                state |= ch_state
        if kc == 0:
            print(f"[rpa] Aviso: tecla '{key}' não mapeada", file=sys.stderr)
            return

        if self._keyboard == "xtest":
            for mc in mod_codes:
                self._fake_key(mc, True)
            self._fake_key(kc, True)
            self._fake_key(kc, False)
            for mc in reversed(mod_codes):
                self._fake_key(mc, False)
        else:
            self._send_key(kc, state, mods=tuple(mod_codes))

    # ── Screenshot ─────────────────────────────────────────

    def screenshot(self, path: str):
        """Take a FRESH full-screen screenshot to path (PNG).

        A long-lived mss instance returns stale frames for rapid successive
        grabs on this Xorg/xrdp server (a before/after diff then sees no change,
        breaking visual verification). Grabbing with a fresh context each call
        forces an up-to-date frame.
        """
        from mss import mss as _mss
        with _mss() as m:
            m.shot(mon=-1, output=path)

    def get_pixel_color(self, x: int, y: int) -> str:
        """Return #RRGGBB of pixel at (x,y)."""
        img = self._mss.grab({"left": x, "top": y, "width": 1, "height": 1})
        from PIL import Image
        pil_img = Image.frombytes("RGB", img.size, img.bgra, "raw", "BGRX")
        r, g, b = pil_img.getpixel((0, 0))
        return f"#{r:02x}{g:02x}{b:02x}"

    # ── Window targeting (the "stage") ─────────────────────
    # RPA must act on ONE window, not the whole shared desktop. Otherwise OCR
    # matches text in other windows (e.g. terminals) and clicks land off-target.

    def list_windows(self) -> list:
        """Enumerate viewable, named top-level windows with absolute geometry."""
        from Xlib import X
        out, seen = [], set()

        def walk(w):
            try:
                children = w.query_tree().children
            except Exception:
                return
            for c in children:
                try:
                    nm = c.get_wm_name()
                    g = c.get_geometry()
                    a = c.get_attributes()
                    if (nm and a.map_state == X.IsViewable
                            and g.width > 40 and g.height > 40 and c.id not in seen):
                        co = self._root.translate_coords(c, 0, 0)
                        seen.add(c.id)
                        out.append({
                            "id": c.id, "name": nm,
                            "x": co.x, "y": co.y,
                            "width": g.width, "height": g.height,
                            "_win": c,
                        })
                except Exception:
                    pass
                walk(c)

        walk(self._root)
        return out

    def find_window(self, title: str) -> Optional[dict]:
        """Find the largest viewable window whose title contains `title`."""
        t = title.lower()
        matches = [w for w in self.list_windows() if t in w["name"].lower()]
        if not matches:
            return None
        return sorted(matches, key=lambda w: -w["width"] * w["height"])[0]

    def activate_window(self, win) -> bool:
        """Raise, focus and keep-above a window so it owns the stage (EWMH)."""
        from Xlib import X
        from Xlib.protocol import event as xe
        w = win["_win"] if isinstance(win, dict) else win
        net_active = self._display.intern_atom("_NET_ACTIVE_WINDOW")
        net_state = self._display.intern_atom("_NET_WM_STATE")
        above = self._display.intern_atom("_NET_WM_STATE_ABOVE")
        try:
            for atom, data in ((net_state, [1, above, 0, 1, 0]),
                               (net_active, [1, X.CurrentTime, 0, 0, 0])):
                ev = xe.ClientMessage(window=w, client_type=atom, data=(32, data))
                self._root.send_event(
                    ev, event_mask=X.SubstructureRedirectMask | X.SubstructureNotifyMask)
            w.configure(stack_mode=X.Above)
            self._display.sync()
            # XTest key/button events go to the INPUT-FOCUSED window. Raising is
            # not enough — explicitly grab keyboard focus so typed keys land here.
            try:
                w.set_input_focus(X.RevertToParent, X.CurrentTime)
                self._display.sync()
            except Exception:
                pass
            self._focused_win = w  # XSendEvent keyboard target
            return True
        except Exception as e:
            print(f"[rpa] activate_window falhou: {e}", file=sys.stderr)
            return False

    def window_geometry(self, win) -> dict:
        """Return current absolute {x, y, width, height} of a window."""
        w = win["_win"] if isinstance(win, dict) else win
        g = w.get_geometry()
        co = self._root.translate_coords(w, 0, 0)
        return {"x": co.x, "y": co.y, "width": g.width, "height": g.height}
