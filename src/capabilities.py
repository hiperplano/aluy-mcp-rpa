"""Motor de detecção de capacidades (EST-1141).

Sonda a máquina e devolve os parâmetros que o motor RPA deve usar — em vez de
valores fixos pra uma box específica. Adapta:
  - GPU presente?           -> EasyOCR em GPU (rápido) + canvas de OCR maior.
  - CPU-only?               -> canvas de OCR capado (caber no orçamento de 60s).
  - RAM + swap?             -> libera/bloqueia VLM; sinaliza falta de swap.
  - Servidor X é xrdp?      -> teclado por XSendEvent (XTest-tecla é descartado);
                               senão XTest (universal, cobre apps Wine).

Tudo best-effort: nunca levanta exceção; degrada para defaults conservadores.
"""
import os
import sys
import glob


def _has_gpu() -> bool:
    try:
        import torch
        if torch.cuda.is_available():
            return True
    except Exception:
        pass
    # fallback: nvidia-smi presente e responde
    try:
        import subprocess
        r = subprocess.run(["nvidia-smi", "-L"], capture_output=True, timeout=4)
        return r.returncode == 0 and b"GPU" in r.stdout
    except Exception:
        return False


def _mem_info():
    """Retorna (MemAvailable_MB, SwapTotal_MB)."""
    try:
        info = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            info[k.strip()] = int(v.strip().split()[0])  # kB
        return info.get("MemAvailable", 0) // 1024, info.get("SwapTotal", 0) // 1024
    except Exception:
        return 0, 0


def _display_is_xrdp(display: str) -> bool:
    """True se o servidor X do `display` (ex.: ':10') é um Xorg do xrdp —
    onde a injeção de TECLA via XTest é descartada (mouse XTest funciona)."""
    num = (display or os.environ.get("DISPLAY", ":0")).lstrip(":").split(".")[0]
    for cmdpath in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            argv = open(cmdpath, "rb").read().replace(b"\x00", b" ").decode("utf-8", "ignore")
        except Exception:
            continue
        low = argv.lower()
        if "xorg" not in low and "xvfb" not in low:
            continue
        if f":{num} " not in argv and not argv.rstrip().endswith(f":{num}"):
            continue
        if "xvfb" in low:
            return False           # Xvfb: X normal, XTest-tecla funciona
        if "xrdp" in low:
            return True            # Xorg do xrdp: XTest-tecla quebrado
    return False                   # desconhecido: assume X normal (XTest)


def _x_reachable(display: str) -> bool:
    """O servidor X do `display` aceita conexão? (sem ele o motor não roda)."""
    try:
        from Xlib import display as _xd
        d = _xd.Display(display)
        d.close()
        return True
    except Exception:
        return False


def _has_wine() -> bool:
    """Wine instalado? (necessário só p/ automatizar apps Windows, ex.: MetaTrader)."""
    import shutil
    return shutil.which("wine") is not None or shutil.which("wine64") is not None


def detect_capabilities(display: str = None) -> dict:
    display = display or os.environ.get("DISPLAY", ":0")
    gpu = _has_gpu()
    avail_mb, swap_mb = _mem_info()
    xrdp = _display_is_xrdp(display)
    x_ok = _x_reachable(display)
    wine = _has_wine()

    # a11y (AT-SPI) disponível? — caminho rápido/preciso opcional (EST-1146).
    try:
        from .a11y import available as _a11y_available
        a11y = _a11y_available()
    except Exception:
        a11y = False

    caps = {
        "gpu": gpu,
        "mem_available_mb": avail_mb,
        "swap_mb": swap_mb,
        "display": display,
        "xrdp": xrdp,
        "a11y": a11y,
        "x_ok": x_ok,
        "wine": wine,
        # EasyOCR: usa GPU se houver; canvas maior em GPU, capado em CPU.
        "ocr_gpu": gpu,
        "ocr_canvas": 2560 if gpu else 1280,
        # VLM só com GPU + folga de memória (senão OOM/>60s ⇒ derruba o server).
        "vlm_allowed": gpu and (avail_mb + swap_mb) >= 6000,
        # Teclado: XSendEvent no xrdp (XTest-tecla descartado); XTest senão (cobre Wine).
        "keyboard": "xsendevent" if xrdp else "xtest",
        # Avisos de SO (o server não conserta — só sinaliza).
        "warn_no_swap": swap_mb == 0 and avail_mb < 4096,
        "warn_low_mem": (avail_mb + swap_mb) < 3072,
    }
    return caps


def summary(caps: dict) -> str:
    return ("[rpa] capacidades: gpu={gpu} mem={mem_available_mb}MB swap={swap_mb}MB "
            "ocr_canvas={ocr_canvas} vlm={vlm_allowed} teclado={keyboard} a11y={a11y} "
            "x_ok={x_ok} wine={wine} (display={display}, xrdp={xrdp})").format(**caps)
