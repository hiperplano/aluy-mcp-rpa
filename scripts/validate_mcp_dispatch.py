#!/usr/bin/env python3
"""Valida a CAMADA MCP: chama server.handle_call exatamente como o cliente MCP
(o aluy) faz — mesmo dispatch, mesmos schemas — sem o transporte stdio.

Sequencia: rpa_target_window -> rpa_type_text('7*8=') -> rpa_screenshot,
e confere via OCR que o display mostra 56.
"""
import sys, os, time, subprocess, asyncio, json
sys.path.insert(0, "/home/aluy/projects/aluy/aluy-mcp-rpa")
os.environ["DISPLAY"] = os.environ.get("DISPLAY", ":10.0")
from src import server
from src.engine import RpaEngine
from PIL import Image

def fresh_calc():
    subprocess.run(["pkill", "-x", "xcalc"]); time.sleep(0.7)
    subprocess.Popen(["xcalc"]); time.sleep(1.8)

async def call(name, args):
    res = await server.handle_call(name, args)
    txt = res[0].text if res else ""
    print(f">> {name}({args}) -> {txt[:160]}", flush=True)
    return txt

async def main():
    fresh_calc()
    await call("rpa_target_window", {"title": "Calculator"})
    await call("rpa_type_text", {"text": "7*8="})
    await asyncio.sleep(0.5)
    eng = server.get_engine()
    reg = eng.stage_region
    shot = eng.screenshot("mcp")
    d = {"x": reg["x"], "y": reg["y"] + int(reg["height"] * 0.015),
         "width": reg["width"], "height": int(reg["height"] * 0.13)}
    ok = eng.vision.find_text(shot, "56", region=d, min_conf=0.2) is not None
    Image.open(shot).crop((reg["x"], reg["y"], reg["x"]+reg["width"], reg["y"]+reg["height"]))\
        .save("/tmp/ev_mcp_56.png")
    print(f"\nMCP dispatch: target_window + type_text via handle_call -> display '56'? {ok}", flush=True)
    with open("/tmp/rpa_mcp_report.txt", "w") as f:
        f.write(f"MCP dispatch 7*8=56: {'OK' if ok else 'FALHOU'}\n")
    os._exit(0 if ok else 1)

if __name__ == "__main__":
    asyncio.run(main())
