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
        # Origem do desktop VIRTUAL (canto da bounding-box de todos os monitores).
        # O screenshot é `mss mon=-1`, cujo pixel(0,0) = virtual(vleft,vtop). Pode
        # ser NEGATIVO se houver monitor à esquerda/acima do primário. Mantemos
        # engine/vision/OCR em espaço de PIXEL-DA-IMAGEM (consistente com a
        # captura) e traduzimos só na fronteira do mouse e da geometria de janela.
        try:
            vb = self._mss.monitors[0]
            self._vleft, self._vtop = int(vb["left"]), int(vb["top"])
        except Exception:
            self._vleft = self._vtop = 0
        self._focused_win = None
        self._keyboard = keyboard  # compat de interface (pyautogui resolve shift sozinho)
        w, h = self._pg.size()
        self.screen_width, self.screen_height = int(w), int(h)
        print(f"[rpa] Portable backend: pyautogui+pygetwindow+mss "
              f"({self.screen_width}x{self.screen_height}, "
              f"virtual_origin=({self._vleft},{self._vtop}))", file=sys.stderr)

    # ── Mouse ──────────────────────────────────────────────
    # pyautogui opera em coords VIRTUAIS; nosso espaço interno é PIXEL-DA-IMAGEM.
    # Converte img→virtual somando a origem virtual (no-op quando origem=(0,0)).
    def _to_virtual(self, x, y):
        return int(x) + self._vleft, int(y) + self._vtop

    def mouse_move(self, x, y):
        self._pg.moveTo(*self._to_virtual(x, y))

    def mouse_click(self, x, y, button="left", double=False):
        vx, vy = self._to_virtual(x, y)
        self._pg.click(vx, vy, clicks=2 if double else 1,
                       interval=0.05, button=button)

    def mouse_drag(self, fx, fy, tx, ty):
        self._pg.moveTo(*self._to_virtual(fx, fy))
        self._pg.dragTo(*self._to_virtual(tx, ty), duration=0.3)

    def mouse_scroll(self, lines):
        self._pg.scroll(int(lines))

    # ── Teclado (pyautogui resolve shift/maiúsculas sozinho) ──
    def _refocus(self):
        """Garante que o palco está em foreground ANTES de digitar — senão o
        teclado cai na janela errada. Usa o caminho Win32 robusto (mesmo do
        activate_window), não o activate() flaky do pygetwindow."""
        w = self._focused_win
        if w is None:
            return
        hwnd = getattr(w, "_hWnd", None)
        if sys.platform == "win32" and hwnd and self._win32_foreground(int(hwnd)):
            return
        try:
            w.activate()
        except Exception:
            pass

    def type_text(self, text):
        self._refocus()
        time.sleep(0.15)
        # pyautogui.typewrite no Windows só mapeia ASCII (via layout do teclado):
        # acentos do PT-BR (ç ã é í õ ...) são DESCARTADOS silenciosamente. Quando
        # houver não-ASCII, injeta via SendInput/KEYEVENTF_UNICODE — digita
        # qualquer caractere BMP independe do layout. ASCII puro segue pelo
        # pyautogui (mais simples, lida com \n/\t).
        if sys.platform == "win32" and any(ord(c) > 127 for c in text):
            if self._type_unicode_win(text):
                return
        self._pg.typewrite(text, interval=0.02)

    @staticmethod
    def _type_unicode_win(text) -> bool:
        """Digita texto arbitrário (Unicode BMP) no Windows via SendInput com
        KEYEVENTF_UNICODE — não depende do layout do teclado, então acentos
        PT-BR entram certo. \\n/\\t/\\r viram as teclas correspondentes."""
        try:
            import ctypes
            from ctypes import wintypes
            INPUT_KEYBOARD = 1
            KEYEVENTF_KEYUP = 0x0002
            KEYEVENTF_UNICODE = 0x0004
            VK = {"\n": 0x0D, "\r": 0x0D, "\t": 0x09}

            class KEYBDINPUT(ctypes.Structure):
                _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                            ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                            ("dwExtraInfo", ctypes.c_size_t)]

            class MOUSEINPUT(ctypes.Structure):
                # Presente só p/ DIMENSIONAR a union: o INPUT real tem a union
                # do tamanho do MOUSEINPUT (o maior membro). Sem ela sizeof(INPUT)
                # vinha 32 em vez de 40 (x64) e o SendInput REJEITAVA (retornava 0).
                _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                            ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                            ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

            class INPUT(ctypes.Structure):
                class _U(ctypes.Union):
                    _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]
                _anonymous_ = ("u",)
                _fields_ = [("type", wintypes.DWORD), ("u", _U)]

            send = ctypes.windll.user32.SendInput
            send.argtypes = [wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
            send.restype = wintypes.UINT

            def _emit(wVk, wScan, flags):
                inp = INPUT()
                inp.type = INPUT_KEYBOARD
                inp.ki = KEYBDINPUT(wVk, wScan, flags, 0, 0)
                send(1, ctypes.byref(inp), ctypes.sizeof(inp))

            for ch in text:
                if ch in VK:
                    vk = VK[ch]
                    _emit(vk, 0, 0)
                    _emit(vk, 0, KEYEVENTF_KEYUP)
                else:
                    code = ord(ch)
                    _emit(0, code, KEYEVENTF_UNICODE)
                    _emit(0, code, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP)
                time.sleep(0.005)
            return True
        except Exception as e:
            print(f"[rpa] _type_unicode_win falhou: {e}", file=sys.stderr)
            return False

    def press_key(self, key):
        self._refocus()
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
            r, g, b = self._pg.pixel(*self._to_virtual(x, y))
            return f"#{r:02x}{g:02x}{b:02x}"
        except Exception:
            return "#000000"

    # ── Janelas (pygetwindow — Win/Mac) ────────────────────
    @staticmethod
    def _is_cloaked(hwnd) -> bool:
        """True se a janela está 'cloaked' pelo DWM — janelas-fantasma de apps
        UWP (Calculadora, Configurações) que o pygetwindow lista com geometria
        falsa. Filtrá-las evita o find_window mirar um fantasma."""
        if sys.platform != "win32" or not hwnd:
            return False
        try:
            import ctypes
            DWMWA_CLOAKED = 14
            val = ctypes.c_int(0)
            ctypes.windll.dwmapi.DwmGetWindowAttribute(
                int(hwnd), DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val))
            return val.value != 0
        except Exception:
            return False

    def list_windows(self):
        out = []
        if not self._gw:
            return out
        try:
            for w in self._gw.getAllWindows():
                try:
                    if not w.title:
                        continue
                    is_min = bool(getattr(w, "isMinimized", False))
                    # Janela minimizada vem como -32000/-32000 e ~160x28: NÃO
                    # filtrar por tamanho (senão o target_window não acha p/
                    # restaurar). activate_window→_win32_foreground a restaura
                    # (ShowWindow) e a geometria é re-lida válida depois.
                    if not is_min and (w.width < 40 or w.height < 40):
                        continue
                    hwnd = getattr(w, "_hWnd", None)
                    if self._is_cloaked(hwnd):
                        continue
                    # id ESTÁVEL = HWND (id(w) muda a cada enumeração, pois o
                    # pygetwindow cria objetos novos → launch_app via diff de ids
                    # achava que TODA janela era nova). Fallback id(w) sem HWND.
                    # x,y em PIXEL-DA-IMAGEM (subtrai a origem virtual) p/ casar
                    # com o screenshot mss; o mouse re-soma a origem ao clicar.
                    out.append({"id": int(hwnd) if hwnd else id(w),
                                "name": w.title, "x": int(w.left) - self._vleft,
                                "y": int(w.top) - self._vtop, "width": int(w.width),
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

    def blocking_popup(self, win):
        """Se um MODAL (popup) está bloqueando `win`, devolve a janela do popup
        (mesmo formato de list_windows); senão None.

        Sinal do Windows: ao abrir um diálogo modal, o SO DESABILITA a janela-dona
        (IsWindowEnabled==False) e `GW_ENABLEDPOPUP` aponta o popup ativo. Sem isso
        as tools tentam clicar/digitar na janela de baixo (bloqueada) e nada
        acontece — é o que trava o agente. Segue a cadeia (popup de popup)."""
        if sys.platform != "win32":
            return None
        try:
            import ctypes
            u = ctypes.windll.user32
            GW_ENABLEDPOPUP = 6
            w = win["_win"] if isinstance(win, dict) else win
            hwnd = getattr(w, "_hWnd", None)
            if not hwnd:
                return None
            hwnd = int(hwnd)
            seen = set()
            # desce a cadeia de popups até chegar no que está habilitado (o ativo)
            for _ in range(8):
                if u.IsWindowEnabled(hwnd):
                    break
                pop = u.GetWindow(hwnd, GW_ENABLEDPOPUP)
                if not pop or pop == hwnd or pop in seen:
                    break
                seen.add(pop)
                hwnd = int(pop)
            target = win["_win"] if isinstance(win, dict) else win
            target_hwnd = int(getattr(target, "_hWnd", 0))
            if hwnd == target_hwnd:
                # IsWindowEnabled não pegou (diálogo que NÃO desabilita a dona,
                # ex.: janela de ordem do MT5). Fallback: janela VISÍVEL, com
                # título, cujo DONO (GW_OWNER) é o alvo e que sobrepõe a área dele.
                GW_OWNER = 4
                tr = self.window_geometry(target)
                best = None
                for cw in (self._gw.getAllWindows() if self._gw else []):
                    h = getattr(cw, "_hWnd", None)
                    if not h or int(h) == target_hwnd:
                        continue
                    try:
                        if not (cw.title and u.IsWindowVisible(int(h))):
                            continue
                        if u.GetWindow(int(h), GW_OWNER) != target_hwnd:
                            continue
                        # sobrepõe o alvo?
                        ox, oy = int(cw.left) - self._vleft, int(cw.top) - self._vtop
                        if (ox < tr["x"] + tr["width"] and ox + int(cw.width) > tr["x"] and
                                oy < tr["y"] + tr["height"] and oy + int(cw.height) > tr["y"]):
                            best = {"id": int(h), "name": cw.title, "x": ox, "y": oy,
                                    "width": int(cw.width), "height": int(cw.height), "_win": cw}
                            break
                    except Exception:
                        continue
                return best
            # acha o dict da janela do popup (já enumerada)
            for cand in self.list_windows():
                if cand["id"] == hwnd:
                    return cand
            # popup não enumerado (sem título/pequeno): monta pelo hwnd
            from ctypes import wintypes as _wt
            buf = ctypes.create_unicode_buffer(256); u.GetWindowTextW(hwnd, buf, 256)
            rect = _wt.RECT(); u.GetWindowRect(hwnd, ctypes.byref(rect))
            for cw in self._gw.getAllWindows() if self._gw else []:
                if getattr(cw, "_hWnd", None) == hwnd:
                    return {"id": hwnd, "name": buf.value or "(popup)",
                            "x": int(rect.left) - self._vleft, "y": int(rect.top) - self._vtop,
                            "width": int(rect.right - rect.left),
                            "height": int(rect.bottom - rect.top), "_win": cw}
            return None
        except Exception as e:
            print(f"[rpa] blocking_popup falhou: {e}", file=sys.stderr)
            return None

    def activate_window(self, win):
        w = win["_win"] if isinstance(win, dict) else win
        self._focused_win = w
        # No Windows, pygetwindow.activate() usa SetForegroundWindow, que o SO
        # bloqueia quando o processo chamador não está em foreground — a janela
        # NÃO sobe e o screenshot escopado captura o que estiver por cima (bug
        # observado: OCR lia o IDE em vez do app-alvo). O caminho confiável é a
        # API Win32 com AttachThreadInput, e VERIFICAR que subiu de fato.
        hwnd = getattr(w, "_hWnd", None)
        if sys.platform == "win32" and hwnd:
            if self._win32_foreground(int(hwnd)):
                time.sleep(0.25)
                return True
            print("[rpa] activate_window: Win32 foreground não confirmou; "
                  "tentando pygetwindow.activate()", file=sys.stderr)
        try:
            w.activate()
            time.sleep(0.25)
            return True
        except Exception as e:
            print(f"[rpa] activate_window (portable) falhou: {e}", file=sys.stderr)
            return False

    @staticmethod
    def _win32_foreground(hwnd, *, tries: int = 3) -> bool:
        """Traz `hwnd` para frente de forma robusta e CONFIRMA (GetForegroundWindow).

        Restaura se minimizada, anexa a fila de input da thread do foreground à
        nossa (AttachThreadInput) — único jeito de o SO permitir o
        SetForegroundWindow — e verifica. Retorna False se não confirmar."""
        try:
            import ctypes
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            # Early-out: se já está em foreground e não está minimizada, NÃO faz
            # o ritual (ALT-nudge etc.) — o ALT ativaria a barra de menu e a 1ª
            # tecla de um type/press iria pro menu. Caso comum em type/press
            # repetidos na mesma janela.
            if u.GetForegroundWindow() == hwnd and not u.IsIconic(hwnd):
                return True
            SW_RESTORE = 9
            SPI_SETFOREGROUNDLOCKTIMEOUT = 0x2001
            SPIF_SENDCHANGE = 0x2
            KEYEVENTF_KEYUP = 0x2
            VK_MENU = 0x12  # ALT
            # 1) Zera o ForegroundLockTimeout: por padrão o Windows IGNORA
            #    SetForegroundWindow de um processo que não está em foco (o caso
            #    do server MCP, dirigido pelo aluy). Com timeout=0 ele passa a
            #    permitir. (documentado; usado por automações.)
            try:
                u.SystemParametersInfoW(SPI_SETFOREGROUNDLOCKTIMEOUT, 0, 0, SPIF_SENDCHANGE)
            except Exception:
                pass
            for _ in range(tries):
                if u.IsIconic(hwnd):
                    u.ShowWindow(hwnd, SW_RESTORE)
                # 2) Um toque de ALT conta como "input do usuário" e destrava o
                #    direito de trazer janela ao foreground neste mesmo tick.
                u.keybd_event(VK_MENU, 0, 0, 0)
                u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
                fg = u.GetForegroundWindow()
                cur = k.GetCurrentThreadId()
                fg_thr = u.GetWindowThreadProcessId(fg, None)
                tgt_thr = u.GetWindowThreadProcessId(hwnd, None)
                u.AttachThreadInput(cur, tgt_thr, True)
                u.AttachThreadInput(cur, fg_thr, True)
                try:
                    u.BringWindowToTop(hwnd)
                    u.SetForegroundWindow(hwnd)
                finally:
                    u.AttachThreadInput(cur, tgt_thr, False)
                    u.AttachThreadInput(cur, fg_thr, False)
                time.sleep(0.2)
                if u.GetForegroundWindow() == hwnd:
                    return True
            return u.GetForegroundWindow() == hwnd
        except Exception as e:
            print(f"[rpa] _win32_foreground falhou: {e}", file=sys.stderr)
            return False

    def window_geometry(self, win):
        w = win["_win"] if isinstance(win, dict) else win
        # PIXEL-DA-IMAGEM (subtrai origem virtual) — ver list_windows.
        return {"x": int(w.left) - self._vleft, "y": int(w.top) - self._vtop,
                "width": int(w.width), "height": int(w.height)}
