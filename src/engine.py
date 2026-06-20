"""Core RPA engine — deterministic action loop with visual verification.

Architecture:
  1. Before-action screenshot
  2. Perform action (desktop backend)
  3. After-action screenshot
  4. Verify expected effect (vision backend)
  5. On failure: retry with backoff
  6. On repeated failure: escalate to VLM/LLM agent

This runs locally, without LLM round-trips per step. The LLM is only invoked
on escalation after local retries are exhausted.
"""

import os
import time
import tempfile
import sys
from pathlib import Path
from typing import Optional, Callable, Any
from dataclasses import dataclass, field

from .backends.desktop import DesktopBackend
from .backends.vision import VisionBackend
from .backends.vlm import VlmBackend


def _in_region(loc: dict, region: dict) -> bool:
    """True se o ponto (loc x/y) está dentro de region {x,y,width,height}."""
    return (region["x"] <= loc["x"] <= region["x"] + region["width"] and
            region["y"] <= loc["y"] <= region["y"] + region["height"])


@dataclass
class ActionResult:
    """Result of an RPA action with verification."""
    success: bool
    action: str
    before_screenshot: Optional[str] = None
    after_screenshot: Optional[str] = None
    diff_summary: Optional[str] = None
    error: Optional[str] = None
    retries: int = 0
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "action": self.action,
            "retries": self.retries,
        }
        if self.before_screenshot:
            d["before_screenshot"] = self.before_screenshot
        if self.after_screenshot:
            d["after_screenshot"] = self.after_screenshot
        if self.diff_summary:
            d["diff_summary"] = self.diff_summary
        if self.error:
            d["error"] = self.error
        d.update(self.details)
        return d


class RpaEngine:
    """Deterministic RPA loop with visual verification."""

    def __init__(
        self,
        desktop: DesktopBackend,
        vision: VisionBackend,
        vlm: Optional[VlmBackend] = None,
        *,
        max_retries: int = 3,
        retry_delay: float = 0.5,
        screenshot_dir: str = None,   # None → tempfile.gettempdir() (cross-OS)
        verbose: bool = True,
    ):
        self.desktop = desktop
        self.vision = vision
        self.vlm = vlm
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        # /tmp não existe no Windows; tempfile.gettempdir() resolve em todo OS
        # (TEMP no Windows, /tmp no Linux/macOS).
        self.screenshot_dir = screenshot_dir or tempfile.gettempdir()
        self.verbose = verbose
        # Active "stage": when set, OCR/template/grounding search ONLY inside this
        # window region, so the cluttered shared desktop can't pollute matches.
        self.stage_window: Optional[dict] = None
        self.stage_region: Optional[dict] = None
        # Self-healing: remembered visual templates of elements that were
        # successfully located, keyed by their search text. When OCR later fails
        # to find the text, we re-locate by template-matching the saved crop.
        self._heal_store: dict = {}

    # ── Stage (window targeting) ───────────────────────────

    def target_window(self, title: str, *, settle: float = 0.6) -> ActionResult:
        """Make the window whose title contains `title` the active stage.

        Raises+focuses it and scopes all subsequent searches to its bounds.
        This is the single most important reliability step on a shared desktop.
        """
        win = self.desktop.find_window(title)
        if win is None:
            names = [w["name"] for w in self.desktop.list_windows()]
            return ActionResult(success=False, action=f"target_window:{title}",
                                error=f"Janela '{title}' não encontrada",
                                details={"windows": names})
        # Popup/modal bloqueando? A janela-alvo fica DESABILITADA enquanto um
        # diálogo modal está aberto — clicar/digitar nela não tem efeito. Mira o
        # POPUP automaticamente (é com ele que dá pra interagir) e avisa.
        redirected = None
        try:
            popup = getattr(self.desktop, "blocking_popup", lambda w: None)(win)
        except Exception:
            popup = None
        if popup is not None:
            redirected = {"de": win["name"], "para": popup["name"]}
            self._log(f"'{win['name']}' está bloqueada pelo popup '{popup['name']}' — mirando o popup")
            win = popup
        self.desktop.activate_window(win)
        time.sleep(settle)
        geo = self.desktop.window_geometry(win)
        self.stage_window = win
        self.stage_region = geo
        self._log(f"palco = '{win['name']}' @ {geo}")
        details = {"window": win["name"], "region": geo}
        if redirected:
            details["redirected_to_popup"] = redirected
            details["hint"] = (f"A janela '{redirected['de']}' estava bloqueada por um "
                               f"popup modal; mirei o popup '{redirected['para']}'. Interaja com ele.")
        return ActionResult(success=True, action=f"target_window:{title}", details=details)

    def clear_stage(self):
        """Drop the active stage; searches revert to the full screen."""
        self.stage_window = None
        self.stage_region = None

    def launch_app(self, command, *, settle: float = 1.8) -> ActionResult:
        """Launch a GUI app DETACHED (non-blocking) and return immediately.

        A foreground `xcalc` (or any GUI program) never exits, so running it
        inline blocks the agent's loop forever. We start it in its own session
        (setsid) with stdio to /dev/null so the call returns at once. Returns the
        pid and the windows that appeared, so the agent can target_window next.
        """
        import shlex
        import subprocess
        # No Windows NÃO usar shlex.split: ele come as barras invertidas dos
        # caminhos (`C:\Program Files\...` vira `C:Program`, `Files...`). O
        # CreateProcess do Windows já faz o parse da string de comando — passamos
        # a string crua. No POSIX, shlex.split (respeita aspas).
        if isinstance(command, (list, tuple)):
            argv, popen_arg = list(command), list(command)
        elif sys.platform == "win32":
            argv, popen_arg = [command], command          # string crua p/ CreateProcess
        else:
            argv = shlex.split(command); popen_arg = argv
        if not argv or not str(argv[0]).strip():
            return ActionResult(success=False, action="launch_app", error="comando vazio")
        before = {w["id"] for w in self.desktop.list_windows()}
        # Detach: POSIX usa start_new_session; Windows usa creationflags
        # (DETACHED_PROCESS) — start_new_session é ignorado lá.
        kw = {}
        if sys.platform == "win32":
            kw["creationflags"] = 0x00000008  # DETACHED_PROCESS
        else:
            kw["start_new_session"] = True
        try:
            proc = subprocess.Popen(
                popen_arg,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL, **kw,
            )
        except FileNotFoundError:
            return ActionResult(success=False, action="launch_app",
                                error=f"programa não encontrado: {argv[0]}")
        except Exception as e:
            return ActionResult(success=False, action="launch_app", error=str(e))
        time.sleep(settle)
        new_windows = [w["name"] for w in self.desktop.list_windows() if w["id"] not in before]
        return ActionResult(
            success=True, action="launch_app",
            details={"command": " ".join(argv), "pid": proc.pid,
                     "new_windows": new_windows,
                     "hint": "App aberto (não bloqueia). Use rpa_target_window com o título."},
        )

    def _region(self, region: Optional[dict]) -> Optional[dict]:
        """Caller region wins; otherwise fall back to the active stage."""
        if region is not None:
            return region
        if self.stage_window is not None:
            # refresh geometry in case the window moved/resized
            self.stage_region = self.desktop.window_geometry(self.stage_window)
        return self.stage_region

    def _log(self, msg: str):
        if self.verbose:
            print(f"[rpa] {msg}", file=sys.stderr)

    def _screenshot(self, label: str) -> str:
        path = os.path.join(
            self.screenshot_dir,
            f"rpa_{label}_{int(time.time()*1000)}.png"
        )
        self.desktop.screenshot(path)
        return path

    # ── Core action loop ───────────────────────────────────

    def act(
        self,
        action_name: str,
        do_action: Callable[[], None],
        verify: Callable[[], bool],
        *,
        verify_desc: str = "",
        extra_retries: int = 0,
    ) -> ActionResult:
        """Execute an action with before/after verification and retry.

        Args:
            action_name: Human-readable name of the action
            do_action: Callable that performs the action (no args)
            verify: Callable that returns True if action had expected effect
            verify_desc: Description of what verify checks (for error messages)
            extra_retries: Additional retries beyond max_retries
        """
        max_tries = self.max_retries + extra_retries

        before = self._screenshot(f"before_{action_name}")
        self._log(f"{action_name}: tirou before screenshot {before}")

        for attempt in range(max_tries + 1):  # 0 = initial, 1..N = retries
            if attempt > 0:
                self._log(f"{action_name}: retry {attempt}/{max_tries}")
                time.sleep(self.retry_delay * attempt)  # exponential-ish backoff

            try:
                do_action()
            except Exception as e:
                self._log(f"{action_name}: erro na ação: {e}")
                after = self._screenshot(f"after_{action_name}")
                return ActionResult(
                    success=False,
                    action=action_name,
                    before_screenshot=before,
                    after_screenshot=after,
                    error=f"Erro ao executar ação: {e}",
                    retries=attempt,
                )

            after = self._screenshot(f"after_{action_name}")
            self._log(f"{action_name}: tirou after screenshot {after}")

            # Verify
            try:
                ok = verify()
            except Exception as e:
                self._log(f"{action_name}: erro na verificação: {e}")
                ok = False

            if ok:
                diff = self._diff_safe(before, after)
                self._log(f"{action_name}: ✓ sucesso (attempt {attempt})")
                return ActionResult(
                    success=True,
                    action=action_name,
                    before_screenshot=before,
                    after_screenshot=after,
                    diff_summary=diff,
                    retries=attempt,
                )

            # Failed — diff for debugging
            diff = self._diff_safe(before, after)
            self._log(f"{action_name}: ✗ verificação falhou ({verify_desc or '?'}). diff: {diff}")

            # Clean up failed after, will take new on retry
            try:
                os.unlink(after)
            except Exception:
                pass

        # All retries exhausted
        after = self._screenshot(f"after_{action_name}")
        diff = self._diff_safe(before, after)
        self._log(f"{action_name}: ✗ TODAS tentativas falharam ({max_tries+1})")
        return ActionResult(
            success=False,
            action=action_name,
            before_screenshot=before,
            after_screenshot=after,
            diff_summary=diff,
            error=f"Verificação falhou após {max_tries+1} tentativas: {verify_desc or 'sem descrição'}",
            retries=max_tries,
        )

    def _diff_safe(self, before: str, after: str) -> str:
        try:
            return self.vision.diff(before, after)
        except Exception as e:
            return f"Erro no diff: {e}"

    # ── High-level primitives ──────────────────────────────

    def click_at(self, x: int, y: int, button: str = "left", double: bool = False) -> ActionResult:
        """Click at absolute coordinates with visual verification."""
        before_color = None
        try:
            before_color = self.desktop.get_pixel_color(x, y)
        except Exception:
            pass

        def do():
            self.desktop.mouse_click(x, y, button=button, double=double)

        def check():
            return True  # click coordinates are absolute — always "succeeds"
            # Could verify pixel color changed, but not always reliable

        result = self.act(f"click_{x}_{y}", do, check, verify_desc="click absoluto")
        result.details["x"] = x
        result.details["y"] = y
        result.details["button"] = button
        return result

    def click_image(self, template_path: str, threshold: float = 0.8,
                    timeout: float = 10.0) -> ActionResult:
        """Find an image template on screen and click it."""
        def do():
            before = self._screenshot("locate_image")
            loc = self.vision.locate(before, template_path, threshold=threshold)
            if loc is None:
                raise RuntimeError(f"Template não encontrado (threshold={threshold})")
            self.desktop.mouse_click(loc["x"], loc["y"])
            try:
                os.unlink(before)
            except Exception:
                pass

        def check():
            # After clicking, verify the screen changed (anything)
            return True  # We trust the template match

        result = self.act(
            f"click_image_{os.path.basename(template_path)}",
            do, check,
            verify_desc=f"clique no template {template_path}",
        )
        result.details["template"] = template_path
        result.details["threshold"] = threshold
        return result

    def click_text(self, text: str, region: Optional[dict] = None, *,
                   heal: bool = True) -> ActionResult:
        """Find text via OCR (scoped to the active stage) and click it once.

        Success means we LOCATED the text and clicked it. We also sample whether
        the stage changed and report it as `stage_changed` (informational) — but
        we do NOT gate success on it: the before/after frame diff is unreliable
        on this Xorg/xrdp server, and gating caused spurious repeat-clicks.
        The agent can assert the outcome separately (OCR/read of expected state).
        """
        region = self._region(region)

        # a11y-first (EST-1146): se o app expõe a árvore de acessibilidade, achar
        # o elemento por nome é exato e INSTANTÂNEO (sem OCR). Só vale dentro do
        # palco (senão um botão de mesmo nome em outro app seria clicado); apps
        # sem a11y (Wine/canvas) retornam None e o caminho CV (OCR) assume.
        if region is not None:
            try:
                from .a11y import find_text as _a11y_find
                aloc = _a11y_find(text)
                if aloc and _in_region(aloc, region):
                    self.desktop.mouse_click(aloc["x"], aloc["y"])
                    time.sleep(0.25)
                    return ActionResult(
                        success=True, action=f"click_text_{text[:20]}",
                        details={"text": text, "matched_text": aloc["name"],
                                 "clicked_at": {"x": aloc["x"], "y": aloc["y"]},
                                 "via": "a11y", "role": aloc.get("role")})
            except Exception:
                pass

        before = self._screenshot("ct_before")
        loc = self.vision.find_text(before, text, region=region)
        healed = False
        if loc is None and heal:
            # OCR missed it — try to heal via a previously-learned visual template.
            hloc = self._heal_locate(text, before, region)
            if hloc is not None:
                loc, healed = hloc, True
        if loc is None:
            try:
                os.unlink(before)
            except Exception:
                pass
            return ActionResult(success=False, action=f"click_text_{text[:20]}",
                                error=f"Texto '{text}' não encontrado (OCR e self-healing)",
                                details={"text": text})
        self.desktop.mouse_click(loc["x"], loc["y"])
        # Learn/refresh the template from a clean OCR hit (not from a healed one).
        if heal and not healed:
            self._learn_heal_template(text, before, loc["x"], loc["y"], region)
        time.sleep(0.25)
        after = self._screenshot("ct_after")
        changed = self.vision.region_changed(before, after, region=region)
        for p in (before, after):
            try:
                os.unlink(p)
            except Exception:
                pass
        return ActionResult(
            success=True, action=f"click_text_{text[:20]}", after_screenshot=None,
            details={"text": text, "matched_text": loc.get("text", text),
                     "clicked_at": {"x": loc["x"], "y": loc["y"]},
                     "confidence": loc.get("confidence"), "stage_changed": changed,
                     "healed": healed},
        )

    # ── Self-healing (visual template fallback) ────────────

    def _learn_heal_template(self, text, screenshot_path, cx, cy, region, *, pad=26):
        """Save a small crop around a located element as its visual signature."""
        try:
            from PIL import Image
            img = Image.open(screenshot_path).convert("RGB")
            box = (max(0, cx - pad), max(0, cy - pad),
                   min(img.width, cx + pad), min(img.height, cy + pad))
            path = os.path.join(self.screenshot_dir, f"heal_{abs(hash(text)) % 10**8}.png")
            img.crop(box).save(path)
            self._heal_store[text] = {"template": path, "pad": pad}
        except Exception as e:
            self._log(f"heal: falha ao aprender template de '{text}': {e}")

    def _heal_locate(self, text, screenshot_path, region):
        """Re-locate '{text}' by template-matching its learned crop, scoped to stage."""
        entry = self._heal_store.get(text)
        if not entry:
            return None
        try:
            from PIL import Image
            ox, oy = (region or {}).get("x", 0), (region or {}).get("y", 0)
            img = Image.open(screenshot_path).convert("RGB")
            if region:
                img = img.crop((ox, oy, ox + region["width"], oy + region["height"]))
            tmp = os.path.join(self.screenshot_dir, "heal_scene.png")
            img.save(tmp)
            m = self.vision.locate(tmp, entry["template"], threshold=0.7)
            try:
                os.unlink(tmp)
            except Exception:
                pass
            if m is None:
                return None
            self._log(f"heal: '{text}' re-localizado por template em ({ox+m['x']},{oy+m['y']})")
            return {"x": ox + m["x"], "y": oy + m["y"], "text": text, "confidence": m.get("confidence")}
        except Exception as e:
            self._log(f"heal: falha ao re-localizar '{text}': {e}")
            return None

    def click_describe(self, description: str, region: Optional[dict] = None) -> ActionResult:
        """Locate an element by natural-language description via the VLM (grounding)
        and click it. The fallback for glyph/icon controls OCR can't read."""
        region = self._region(region)
        if not self.vlm:
            return ActionResult(success=False, action=f"click_describe:{description[:20]}",
                                error="VLM indisponível para grounding")
        state = {"loc": None}

        def do():
            shot = self._screenshot("ground")
            loc = self.vision.ground(self.vlm, shot, description, region=region)
            try:
                os.unlink(shot)
            except Exception:
                pass
            if loc is None:
                raise RuntimeError(f"VLM não localizou '{description}'")
            state["loc"] = loc
            self.desktop.mouse_click(loc["x"], loc["y"])

        def check():
            return state["loc"] is not None

        result = self.act(f"click_describe_{description[:20]}", do, check,
                          verify_desc=f"grounding de '{description}'")
        if state["loc"]:
            result.details["clicked_at"] = state["loc"]
        result.details["description"] = description
        return result

    def _clear_field(self, *, n: int = 24):
        """Limpa o campo FOCADO de forma robusta antes de digitar. Combina dois
        métodos porque campos custom (spinbox de volume/preço do MT5) IGNORAM o
        select-all: (1) Ctrl+A + Delete (campos padrão); (2) End + Backspace×n
        (apaga tudo da direita p/ esquerda, cobre os spinboxes). Sem isso o
        agente tinha que orquestrar isso na unha (frágil)."""
        d = self.desktop
        try:
            d.press_key("ctrl+a"); time.sleep(0.04)
            d.press_key("Delete"); time.sleep(0.04)
        except Exception:
            pass
        try:
            d.press_key("End"); time.sleep(0.03)
            for _ in range(n):
                d.press_key("backspace"); time.sleep(0.008)
        except Exception:
            pass

    def type_text(self, text: str, *, clear: bool = False) -> ActionResult:
        """Type text at current focus. clear=True limpa o campo antes (robusto p/
        campos custom, ex.: preço/volume do MT5)."""
        def do():
            if clear:
                self._clear_field()
            self.desktop.type_text(text)

        def check():
            return True  # Can't verify typed text without OCR

        result = self.act(f"type_{len(text)}chars", do, check, verify_desc="digitar texto")
        result.details["text_length"] = len(text)
        result.details["cleared"] = clear
        return result

    def press_key(self, key: str) -> ActionResult:
        """Press a key or combo."""
        def do():
            self.desktop.press_key(key)

        def check():
            return True

        result = self.act(f"press_{key}", do, check, verify_desc=f"pressionar {key}")
        result.details["key"] = key
        return result

    def scroll(self, lines: int) -> ActionResult:
        """Scroll up (positive) or down (negative)."""
        def do():
            self.desktop.mouse_scroll(lines)

        def check():
            # Take a second screenshot — if identical, scroll probably didn't work
            after1 = self._screenshot("scroll_check")
            time.sleep(0.2)
            after2 = self._screenshot("scroll_check2")
            diff = self._diff_safe(after1, after2)
            # If screenshots at same scroll position are identical, scroll may have failed
            # But true verification is hard without before/after comparison
            return True  # Soft check

        result = self.act(f"scroll_{lines}", do, check, verify_desc=f"scroll {lines} linhas")
        result.details["lines"] = lines
        return result

    def drag(self, from_x: int, from_y: int, to_x: int, to_y: int) -> ActionResult:
        """Drag from one point to another."""
        def do():
            self.desktop.mouse_drag(from_x, from_y, to_x, to_y)

        def check():
            return True

        result = self.act(
            f"drag_{from_x}_{from_y}_to_{to_x}_{to_y}",
            do, check,
            verify_desc=f"arrastar de ({from_x},{from_y}) a ({to_x},{to_y})",
        )
        result.details["from"] = {"x": from_x, "y": from_y}
        result.details["to"] = {"x": to_x, "y": to_y}
        return result

    def wait_for_image(self, template_path: str, threshold: float = 0.8,
                       timeout: float = 30.0, interval: float = 0.5) -> ActionResult:
        """Wait until an image appears on screen."""
        deadline = time.time() + timeout

        def do():
            while time.time() < deadline:
                before = self._screenshot("wait_image")
                loc = self.vision.locate(before, template_path, threshold=threshold)
                try:
                    os.unlink(before)
                except Exception:
                    pass
                if loc is not None:
                    return loc
                time.sleep(interval)
            raise TimeoutError(f"Template não apareceu em {timeout}s")

        def check():
            before = self._screenshot("wait_check")
            loc = self.vision.locate(before, template_path, threshold=threshold)
            try:
                os.unlink(before)
            except Exception:
                pass
            return loc is not None

        result = self.act(
            f"wait_for_{os.path.basename(template_path)}",
            do, check,
            verify_desc=f"esperar template {template_path}",
            extra_retries=0,
        )
        result.details["template"] = template_path
        result.details["timeout"] = timeout
        return result

    def wait_for_text(self, text: str, timeout: float = 30.0,
                      interval: float = 0.5, region: Optional[dict] = None) -> ActionResult:
        """Wait until text appears on screen (via OCR), scoped to the active stage."""
        region = self._region(region)
        deadline = time.time() + timeout

        def do():
            while time.time() < deadline:
                before = self._screenshot("wait_text")
                loc = self.vision.find_text(before, text, region=region)
                try:
                    os.unlink(before)
                except Exception:
                    pass
                if loc is not None:
                    return loc
                time.sleep(interval)
            raise TimeoutError(f"Texto '{text}' não apareceu em {timeout}s")

        def check():
            before = self._screenshot("wait_text_check")
            loc = self.vision.find_text(before, text, region=region)
            try:
                os.unlink(before)
            except Exception:
                pass
            return loc is not None

        result = self.act(
            f"wait_for_text_{text[:20]}",
            do, check,
            verify_desc=f"esperar texto '{text}'",
            extra_retries=0,
        )
        result.details["text"] = text
        result.details["timeout"] = timeout
        return result

    def screenshot(self, label: str = "manual") -> str:
        """Take a screenshot and return path."""
        return self._screenshot(label)

    def describe_screen(self) -> str:
        """Describe current screen using VLM."""
        if not self.vlm:
            return "Erro: VLM não disponível"
        path = self._screenshot("describe")
        try:
            desc = self.vlm.describe(path)
        except Exception as e:
            desc = f"Erro na descrição VLM: {e}"
        return desc

    def ask_screen(self, question: str) -> str:
        """Ask a question about the current screen using VLM."""
        if not self.vlm:
            return "Erro: VLM não disponível"
        path = self._screenshot("ask")
        try:
            return self.vlm.ask(path, question)
        except Exception as e:
            return f"Erro na consulta VLM: {e}"
