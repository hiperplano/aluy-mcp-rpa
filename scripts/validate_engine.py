#!/usr/bin/env python3
"""Validacao end-to-end do RpaEngine (a API que o MCP/aluy chama).

Carrega os backends UMA vez e exercita os caminhos reais:
  A) target_window + type_text (teclado) + verificacao do resultado
  B) target_window + click_text (OCR-click, mouse) + verificacao
  C) misto: click_text (mouse) + type_text (teclado)

Gera /tmp/rpa_report.txt e crops de evidencia /tmp/ev_*.png.
Roda em DISPLAY=:10.0. NAO usa VLM (OOM na box).
"""
import sys, os, time, subprocess
sys.path.insert(0, "/home/aluy/projects/aluy/aluy-mcp-rpa")
os.environ["DISPLAY"] = os.environ.get("DISPLAY", ":10.0")
from src.backends import detect_backends
from src.engine import RpaEngine
from PIL import Image
from mss import MSS

LOG = []
def log(m):
    print(m, flush=True); LOG.append(m)

def fresh_calc():
    subprocess.run(["pkill", "-x", "xcalc"]); time.sleep(0.7)
    subprocess.Popen(["xcalc"]); time.sleep(1.8)

def read_display(eng, vision, want):
    """OCR do display (faixa superior do palco). Retorna texto lido."""
    reg = eng.stage_region
    shot = eng.screenshot("disp")
    d = {"x": reg["x"], "y": reg["y"] + int(reg["height"] * 0.015),
         "width": reg["width"], "height": int(reg["height"] * 0.13)}
    hit = vision.find_text(shot, want, region=d, min_conf=0.2)
    # salva evidencia
    Image.open(shot).crop((reg["x"], reg["y"], reg["x"]+reg["width"], reg["y"]+reg["height"]))\
        .save(f"/tmp/ev_{want}.png")
    try: os.unlink(shot)
    except Exception: pass
    return hit is not None

def main():
    desktop, vision, vlm = detect_backends()
    eng = RpaEngine(desktop, vision, vlm)
    results = {}

    # ── A: teclado via engine ──
    fresh_calc()
    r = eng.target_window("Calculator")
    eng.type_text("7*8=")
    time.sleep(0.5)
    okA = read_display(eng, vision, "56")
    results["A_teclado_7x8"] = okA
    log(f"A) target+type_text('7*8=') -> display tem '56'? {okA}")

    # ── B: mouse OCR-click via engine ──
    fresh_calc()
    eng.target_window("Calculator")
    rc = eng.click_text("5")
    time.sleep(0.4)
    okB = read_display(eng, vision, "5") and rc.success
    results["B_clicktext_5"] = okB
    log(f"B) target+click_text('5') -> clicou={rc.success} display tem '5'? {okB}")

    # ── C: misto mouse + teclado ──
    fresh_calc()
    eng.target_window("Calculator")
    eng.click_text("9")           # mouse
    eng.type_text("*8=")          # teclado (9*8=72)
    time.sleep(0.5)
    okC = read_display(eng, vision, "72")
    results["C_misto_9x8"] = okC
    log(f"C) click_text('9')+type_text('*8=') -> display tem '72'? {okC}")

    log("")
    log("=== RESUMO ===")
    for k, v in results.items():
        log(f"  {k}: {'OK' if v else 'FALHOU'}")
    allok = all(results.values())
    log(f"TOTAL: {'TODOS VERDES' if allok else 'HA FALHAS'}")

    with open("/tmp/rpa_report.txt", "w") as f:
        f.write("\n".join(LOG) + "\n")
    os._exit(0 if allok else 1)

if __name__ == "__main__":
    main()
