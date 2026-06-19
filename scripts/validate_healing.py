"""Self-healing: aprende template no 1o clique; com OCR 'quebrado', re-localiza por template."""
import sys, os, time, subprocess
sys.path.insert(0, "/home/aluy/projects/aluy/aluy-mcp-rpa")
os.environ["DISPLAY"]=os.environ.get("DISPLAY",":20")
from src.backends import detect_backends
from src.engine import RpaEngine
desktop, vision, vlm = detect_backends()
eng = RpaEngine(desktop, vision, vlm)
subprocess.run(["pkill","-x","xcalc"]); time.sleep(0.7)
subprocess.Popen(["xcalc"]); time.sleep(1.8)
eng.target_window("Calculator"); time.sleep(0.4)

# 1) clique normal: aprende o template de "7"
r1 = eng.click_text("7")
learned = "7" in eng._heal_store
print(f"1) click_text('7') ok={r1.success} healed={r1.details.get('healed')} aprendeu_template={learned}", flush=True)

# 2) QUEBRA o OCR (find_text sempre None) e clica '7' de novo -> deve CURAR
orig = vision.find_text
vision.find_text = lambda *a, **k: None
r2 = eng.click_text("7")
vision.find_text = orig
print(f"2) com OCR quebrado, click_text('7') ok={r2.success} healed={r2.details.get('healed')} em={r2.details.get('clicked_at')}", flush=True)

# 3) confere display (deve ter 7 algo) — restaura OCR p/ ler
time.sleep(0.4)
reg = eng.stage_region
shot = eng.screenshot("heal")
d = {"x": reg["x"], "y": reg["y"]+int(reg["height"]*0.02), "width": reg["width"], "height": int(reg["height"]*0.13)}
got7 = vision.find_text(shot, "7", region=d, min_conf=0.2) is not None
ok = r1.success and r2.success and r2.details.get("healed") and got7
print(f"3) display tem '7'={got7}", flush=True)
print(f"RESULTADO self-healing: {'OK' if ok else 'FALHOU'}", flush=True)
with open("/tmp/heal_report.txt","w") as f: f.write(f"self-healing: {'OK' if ok else 'FALHOU'} (aprendeu={learned}, curou={r2.details.get('healed')}, display7={got7})\n")
subprocess.run(["pkill","-x","xcalc"]); os._exit(0 if ok else 1)
