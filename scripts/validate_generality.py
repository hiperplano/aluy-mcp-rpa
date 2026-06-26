"""Generalidade: editor GTK (mousepad) — target + type_text + OCR confere o texto."""
import sys, os, time, subprocess
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["DISPLAY"]=":0"
from src.backends import detect_backends
from src.engine import RpaEngine
from PIL import Image
desktop, vision, vlm = detect_backends()
eng = RpaEngine(desktop, vision, vlm)
subprocess.run(["pkill","-x","mousepad"]); time.sleep(0.7)
subprocess.Popen(["mousepad"]); time.sleep(2.5)
r = eng.target_window("Mousepad")
print("target:", r.success, r.details.get("region"), flush=True)
FRASE = "aluy rpa funciona"
eng.type_text(FRASE)
time.sleep(0.6)
reg = eng.stage_region
shot = eng.screenshot("mp")
# corpo do editor (abaixo da barra de menu)
body = {"x": reg["x"], "y": reg["y"]+int(reg["height"]*0.18),
        "width": reg["width"], "height": int(reg["height"]*0.5)}
hit_aluy = vision.find_text(shot, "aluy", region=body, min_conf=0.3)
hit_func = vision.find_text(shot, "funciona", region=body, min_conf=0.3)
Image.open(shot).crop((reg["x"],reg["y"],reg["x"]+reg["width"],reg["y"]+reg["height"])).save("/tmp/ev_mousepad.png")
ok = bool(hit_aluy or hit_func)
print(f"digitou '{FRASE}' no mousepad; OCR acha 'aluy'={bool(hit_aluy)} 'funciona'={bool(hit_func)} -> {'OK' if ok else 'FALHOU'}", flush=True)
subprocess.run(["pkill","-x","mousepad"])
os._exit(0 if ok else 1)
