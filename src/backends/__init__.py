"""Backend detection and initialization for RPA engine."""

import os
import sys
from .desktop import DesktopBackend
from .vision import VisionBackend
from .vlm import VlmBackend


def detect_backends():
    """Detect and initialize all backends. Returns (desktop, vision, vlm)."""
    errors = []

    # Desktop
    try:
        desktop = DesktopBackend()
    except Exception as e:
        errors.append(f"desktop: {e}")
        desktop = None

    # Vision
    try:
        vision = VisionBackend()
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
