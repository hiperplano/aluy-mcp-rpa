"""Backend detection and initialization for RPA engine."""

import os
import sys
from .desktop import DesktopBackend
from .vision import VisionBackend
from .vlm import VlmBackend


def detect_backends(caps: dict = None):
    """Detect and initialize all backends, ADAPTANDO aos recursos da máquina
    (GPU/CPU/RAM/teclado). Returns (desktop, vision, vlm)."""
    errors = []

    # Motor de detecção de capacidades (EST-1141) — adapta em vez de fixar p/ box.
    if caps is None:
        try:
            from ..capabilities import detect_capabilities, summary
            caps = detect_capabilities()
            print(summary(caps), file=sys.stderr)
            is_linux = caps.get("os") == "Linux"
            # X/Wine são conceitos do Linux. No Windows/macOS a ação é via
            # PortableBackend (não há X), então estes avisos não se aplicam.
            if is_linux and not caps.get("x_ok", True):
                print(f"[rpa] ERRO: servidor X não acessível em DISPLAY={caps.get('display')} "
                      "— abra um X (xrdp/Xvfb) antes. Sem X o motor não opera.", file=sys.stderr)
            if caps.get("warn_no_swap"):
                print("[rpa] AVISO: memória baixa e SEM swap — EasyOCR/VLM podem "
                      "causar OOM. Configure swap (ver README).", file=sys.stderr)
            if is_linux and not caps.get("wine", True):
                print("[rpa] INFO: Wine não instalado — apps Windows (ex.: MetaTrader) "
                      "não abrirão; apps nativos do Linux funcionam normalmente.", file=sys.stderr)
        except Exception as e:
            print(f"[rpa] detecção de capacidades falhou (defaults): {e}", file=sys.stderr)
            caps = {}

    # Ação: backend por OS (EST-1147). Linux → Xlib (proveado, XTest/XSendEvent);
    # Windows/macOS → PortableBackend (pyautogui+pygetwindow, input nativo do OS).
    os_name = caps.get("os", "Linux")
    try:
        if os_name in ("Windows", "Darwin"):
            from .portable import PortableBackend
            desktop = PortableBackend()
        else:
            desktop = DesktopBackend(keyboard=caps.get("keyboard", "xsendevent"))
    except Exception as e:
        errors.append(f"desktop ({os_name}): {e}")
        desktop = None

    # Vision (GPU + canvas de OCR detectados)
    try:
        vision = VisionBackend(ocr_gpu=caps.get("ocr_gpu", False),
                               ocr_canvas=caps.get("ocr_canvas", 1280))
    except Exception as e:
        errors.append(f"vision: {e}")
        vision = None

    # VLM (opcional)
    vlm = None
    try:
        vlm = VlmBackend()
    except Exception as e:
        errors.append(f"vlm (opcional): {e}")

    if errors:
        print(f"[rpa] Avisos de backend: {'; '.join(errors)}", file=sys.stderr)

    return desktop, vision, vlm
