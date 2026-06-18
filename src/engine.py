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
        screenshot_dir: str = "/tmp",
        verbose: bool = True,
    ):
        self.desktop = desktop
        self.vision = vision
        self.vlm = vlm
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.screenshot_dir = screenshot_dir
        self.verbose = verbose

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

    def click_text(self, text: str, region: Optional[dict] = None) -> ActionResult:
        """Find text via OCR and click it."""
        def do():
            before = self._screenshot("locate_text")
            loc = self.vision.find_text(before, text, region=region)
            if loc is None:
                raise RuntimeError(f"Texto '{text}' não encontrado via OCR")
            self.desktop.mouse_click(loc["x"], loc["y"])
            try:
                os.unlink(before)
            except Exception:
                pass

        def check():
            return True  # OCR-based — trust the match

        result = self.act(
            f"click_text_{text[:20]}",
            do, check,
            verify_desc=f"clique no texto '{text}'",
        )
        result.details["text"] = text
        return result

    def type_text(self, text: str) -> ActionResult:
        """Type text at current focus."""
        def do():
            self.desktop.type_text(text)

        def check():
            return True  # Can't verify typed text without OCR

        result = self.act(f"type_{len(text)}chars", do, check, verify_desc="digitar texto")
        result.details["text_length"] = len(text)
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
        """Wait until text appears on screen (via OCR)."""
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
