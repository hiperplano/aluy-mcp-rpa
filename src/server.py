#!/usr/bin/env python3
"""aluy-mcp-rpa — MCP server de automação visual RPA.

Ferramentas:
  rpa_screenshot         — Screenshot da tela
  rpa_click_at           — Clique em coordenadas (x,y)
  rpa_click_text         — Encontra texto via OCR e clica
  rpa_click_image        — Encontra template e clica
  rpa_type_text          — Digita texto
  rpa_press_key          — Pressiona tecla/combo
  rpa_scroll             — Scroll vertical
  rpa_drag               — Arrasta de um ponto a outro
  rpa_wait_for_image     — Espera template aparecer (polling)
  rpa_wait_for_text      — Espera texto aparecer (OCR polling)
  rpa_describe_screen    — Descreve tela com VLM
  rpa_ask_screen         — Pergunta sobre a tela (VLM)
  rpa_select_combobox    — Seleciona opção de combobox
  rpa_click_tab          — Clica em tab
  rpa_navigate_menu      — Navega menu hierárquico
  rpa_fill_form          — Preenche formulário

Todas as ações têm:
  - Screenshot antes/depois
  - Verificação visual
  - Retry automático (configurável)
"""

import os
import sys
import json
import asyncio
from pathlib import Path

# Garante DISPLAY (só no Linux/X11 — no Windows/macOS não há servidor X e a ação
# é via PortableBackend; setar DISPLAY ali não tem efeito).
import platform as _platform
if _platform.system() == "Linux" and not os.environ.get("DISPLAY"):
    os.environ["DISPLAY"] = ":10"

# Warm-up do EasyOCR roda em BACKGROUND por padrão (carrega torch/modelo + calibra
# logo após o boot), p/ a PRIMEIRA tool de OCR não pagar o load frio (~40s) e arriscar
# o timeout de 60s do MCP. Medido: o handshake (initialize/list_tools) volta em ~1s
# MESMO com o warmup rodando — ele é thread daemon e não bloqueia o handshake.
# Para depurar/forçar load preguiçoso: exporte RPA_SKIP_OCR_WARMUP=1 antes de subir.

try:
    from mcp.server import Server, NotificationOptions
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent, ImageContent
except ImportError:
    print("mcp SDK não encontrado. Instale: pip install mcp", file=sys.stderr)
    sys.exit(1)

# Allow `python3 src/server.py` (no package context): make this file part of the
# `src` package by putting the repo root on sys.path and setting __package__,
# so the relative imports below resolve. Running as `-m src.server` is unaffected.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    __package__ = "src"

from .backends import detect_backends
from .engine import RpaEngine, ActionResult
from .complex import (
    select_combobox,
    click_tab,
    navigate_menu,
    fill_form,
    find_in_table,
)

# ── Global engine ─────────────────────────────────────────

import threading

_engine: RpaEngine = None
_engine_lock = threading.Lock()


def get_engine() -> RpaEngine:
    global _engine
    with _engine_lock:
        if _engine is None:
            desktop, vision, vlm = detect_backends()
            if desktop is None:
                raise RuntimeError("Desktop backend indisponível")
            if vision is None:
                raise RuntimeError("Vision backend indisponível")
            _engine = RpaEngine(desktop, vision, vlm)
    return _engine


def _warm_ocr():
    """Pré-carrega o EasyOCR em background no boot, para a PRIMEIRA chamada de
    tool não pagar a carga fria (~30-60s) e estourar o timeout de 60s do MCP.
    Respeita RPA_SKIP_OCR_WARMUP=1 para pular (útil quando o EasyOCR demora
    demais e o framework mata o server antes do handshake)."""
    if os.environ.get("RPA_SKIP_OCR_WARMUP") == "1":
        print("[rpa] EasyOCR warm-up pulado (RPA_SKIP_OCR_WARMUP=1)", file=sys.stderr)
        return
    try:
        get_engine().vision._ensure_ocr()
        print("[rpa] EasyOCR pré-aquecido", file=sys.stderr)
    except Exception as e:
        print(f"[rpa] warm OCR falhou (não-fatal): {e}", file=sys.stderr)


# ── Tool schemas ──────────────────────────────────────────

TOOLS = [
    Tool(
        name="rpa_screenshot",
        description=("Lê a tela: retorna o TEXTO VISÍVEL com pontos de clique "
                     "(ex.: '7 @(683,667)'). Use os rótulos para rpa_click_text/rpa_click_at. "
                     "Recortado no palco; full=true p/ tela inteira; image=true inclui o PNG "
                     "(só p/ modelos de visão)."),
        inputSchema={
            "type": "object",
            "properties": {
                "full": {"type": "boolean", "description": "true = tela inteira; default = só o palco."},
                "image": {"type": "boolean", "description": "true = inclui a imagem PNG além do texto."},
            },
        },
    ),
    Tool(
        name="rpa_click_at",
        description="Clica em coordenadas absolutas (x,y). Verifica mudança visual.",
        inputSchema={
            "type": "object",
            "properties": {
                "x": {"type": "integer", "description": "Coordenada X."},
                "y": {"type": "integer", "description": "Coordenada Y."},
                "button": {
                    "type": "string",
                    "enum": ["left", "right", "middle"],
                    "description": "Botão do mouse. Default: left.",
                },
                "double": {
                    "type": "boolean",
                    "description": "Double-click. Default: false.",
                },
            },
            "required": ["x", "y"],
        },
    ),
    Tool(
        name="rpa_click_text",
        description="Encontra texto via OCR e clica nele. Com verificação antes/depois.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto a encontrar e clicar."},
                "region": {
                    "type": "object",
                    "description": "Região opcional: {x, y, width, height}.",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                },
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="rpa_click_image",
        description="Encontra um template (imagem .png) na tela e clica. Template matching + verificação.",
        inputSchema={
            "type": "object",
            "properties": {
                "template_path": {
                    "type": "string",
                    "description": "Caminho do template .png a buscar.",
                },
                "threshold": {
                    "type": "number",
                    "description": "Similaridade mínima (0.0–1.0). Default: 0.8.",
                },
            },
            "required": ["template_path"],
        },
    ),
    Tool(
        name="rpa_type_text",
        description=("Digita texto no elemento com foco. Use clear=true p/ LIMPAR o campo "
                     "antes (essencial em campos numéricos/spinbox como volume e preço do "
                     "MetaTrader, que senão concatenam e viram lixo). ATENÇÃO: o teclado pode "
                     "NÃO ter efeito em alguns toolkits — se o texto não aparecer, clique no campo."),
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto a digitar."},
                "clear": {"type": "boolean", "description": "true = limpa o campo focado antes de digitar (Ctrl+A+Del e Backspaces). Default: false."},
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="rpa_press_key",
        description=("Pressiona tecla ou combo (ex.: 'ctrl+c', 'alt+F4', 'Return', 'Escape', 'F9'). "
                     "ATENÇÃO: atalhos/teclas podem NÃO funcionar em apps Wine (MetaTrader) e alguns "
                     "toolkits. Se a tela não mudar, NÃO insista no teclado — abra o recurso por "
                     "clique (botão de toolbar, menu, painel)."),
        inputSchema={
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "Tecla ou combo (ex.: 'ctrl+c')."},
            },
            "required": ["key"],
        },
    ),
    Tool(
        name="rpa_scroll",
        description="Scroll vertical. Positivo = cima, negativo = baixo.",
        inputSchema={
            "type": "object",
            "properties": {
                "lines": {
                    "type": "integer",
                    "description": "Nº de linhas. Positivo=cima, negativo=baixo.",
                },
            },
            "required": ["lines"],
        },
    ),
    Tool(
        name="rpa_drag",
        description="Arrasta o mouse de um ponto a outro.",
        inputSchema={
            "type": "object",
            "properties": {
                "from_x": {"type": "integer"},
                "from_y": {"type": "integer"},
                "to_x": {"type": "integer"},
                "to_y": {"type": "integer"},
            },
            "required": ["from_x", "from_y", "to_x", "to_y"],
        },
    ),
    Tool(
        name="rpa_wait_for_image",
        description="Espera template aparecer na tela (polling). Timeout default: 30s.",
        inputSchema={
            "type": "object",
            "properties": {
                "template_path": {"type": "string", "description": "Caminho do template."},
                "threshold": {
                    "type": "number",
                    "description": "Similaridade mínima. Default: 0.8.",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout em segundos. Default: 30.",
                },
                "interval": {
                    "type": "number",
                    "description": "Intervalo entre polls. Default: 0.5.",
                },
            },
            "required": ["template_path"],
        },
    ),
    Tool(
        name="rpa_wait_for_text",
        description="Espera texto aparecer via OCR (polling). Timeout default: 30s.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto a esperar."},
                "timeout": {
                    "type": "number",
                    "description": "Timeout em segundos. Default: 30.",
                },
                "interval": {
                    "type": "number",
                    "description": "Intervalo entre polls. Default: 0.5.",
                },
                "region": {
                    "type": "object",
                    "description": "Região opcional.",
                    "properties": {
                        "x": {"type": "integer"},
                        "y": {"type": "integer"},
                        "width": {"type": "integer"},
                        "height": {"type": "integer"},
                    },
                },
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="rpa_describe_screen",
        description="Lê a tela e retorna o TEXTO VISÍVEL com pontos de clique (igual rpa_screenshot).",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="rpa_ask_screen",
        description="Lê a tela (TEXTO VISÍVEL + pontos de clique) para você responder a pergunta.",
        inputSchema={
            "type": "object",
            "properties": {
                "question": {"type": "string", "description": "Pergunta sobre a tela."},
            },
            "required": ["question"],
        },
    ),
    Tool(
        name="rpa_select_combobox",
        description="Seleciona opção de combobox/dropdown pelo texto do label.",
        inputSchema={
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Texto do label do combobox.",
                },
                "option": {
                    "type": "string",
                    "description": "Texto da opção a selecionar.",
                },
            },
            "required": ["label", "option"],
        },
    ),
    Tool(
        name="rpa_click_tab",
        description="Clica em uma tab pelo nome visível.",
        inputSchema={
            "type": "object",
            "properties": {
                "tab_name": {"type": "string", "description": "Nome da tab."},
            },
            "required": ["tab_name"],
        },
    ),
    Tool(
        name="rpa_navigate_menu",
        description="Navega menu hierárquico clicando cada item em sequência.",
        inputSchema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Lista de itens do menu em ordem hierárquica.",
                },
            },
            "required": ["items"],
        },
    ),
    Tool(
        name="rpa_fill_form",
        description="Preenche formulário com múltiplos campos.",
        inputSchema={
            "type": "object",
            "properties": {
                "fields": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                            "type": {
                                "type": "string",
                                "enum": ["text", "combobox", "checkbox"],
                                "description": "Tipo do campo. Default: text.",
                            },
                        },
                        "required": ["label", "value"],
                    },
                    "description": "Lista de campos {label, value, type?}.",
                },
            },
            "required": ["fields"],
        },
    ),
    Tool(
        name="rpa_find_in_table",
        description="Encontra célula em tabela e opcionalmente clica.",
        inputSchema={
            "type": "object",
            "properties": {
                "header": {
                    "type": "string",
                    "description": "Texto do cabeçalho da tabela.",
                },
                "cell_text": {
                    "type": "string",
                    "description": "Texto a encontrar na célula.",
                },
                "click": {
                    "type": "boolean",
                    "description": "Clicar na célula? Default: true.",
                },
            },
            "required": ["header", "cell_text"],
        },
    ),
    Tool(
        name="rpa_launch",
        description=(
            "Abre um app gráfico SEM BLOQUEAR (destacado). Use ISTO para abrir programas "
            "(ex.: 'xcalc', 'mousepad arquivo.txt') — NÃO rode o app pelo bash, porque um "
            "processo gráfico de primeiro plano não retorna e TRAVA o loop. Retorna na hora "
            "com o pid e as janelas novas; depois chame rpa_target_window."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Comando do app (ex.: 'xcalc')."},
            },
            "required": ["command"],
        },
    ),
    Tool(
        name="rpa_target_window",
        description=(
            "Define a JANELA-ALVO (o 'palco'). Eleva/foca a janela cujo título "
            "contém `title` e passa a restringir TODAS as buscas (OCR, template, "
            "grounding) e cliques a ela. Chame ISTO antes de interagir com um app: "
            "evita que o OCR case texto de outras janelas (terminais, etc.). "
            "Retorna a região {x,y,width,height} do palco."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Trecho do título da janela (ex.: 'Calculator')."},
            },
            "required": ["title"],
        },
    ),
    # rpa_click_describe (VLM grounding) foi REMOVIDO do toolset: o VLM nesta
    # máquina estoura a memória / passa dos 60s do timeout MCP e DERRUBA o server
    # (reinício fail-soft). Use rpa_click_text / rpa_click_at + a perceção por texto.
]

TOOL_MAP = {t.name: t for t in TOOLS}


# ── Tool dispatcher ───────────────────────────────────────

def _perceive(engine, *, full: bool = False, with_image: bool = False, extra: dict = None):
    """Perceive the screen for the agent. Returns the visible TEXT via OCR (with
    click points) — works for TEXT-only models (deepseek etc.) — plus the image
    (for vision models). Scoped to the active stage window unless full=True."""
    import base64
    from io import BytesIO
    from PIL import Image
    path = engine.screenshot()
    img = Image.open(path)
    reg = engine.stage_region
    scoped = bool(reg and not full)
    region = reg if scoped else None
    # OCR text — THIS is what a text-only agent reads.
    try:
        tokens = engine.vision.ocr_tokens(path, region=region)
    except Exception as e:
        tokens = []
    if scoped:
        img = img.crop((reg["x"], reg["y"], reg["x"] + reg["width"], reg["y"] + reg["height"]))
    # PERCEÇÃO NÍVEL 1 (UIA, Windows): lê a ÁRVORE de acessibilidade da janela-palco
    # — controles com NOME, TIPO, ação (invoke/value/...) e ponto de clique EXATO,
    # independente de foco/z-order/clutter (o OCR lia a janela errada quando o alvo
    # não estava à frente). É isto que o agente deve preferir; o OCR vira fallback
    # p/ texto custom-desenhado fora da árvore.
    ui_elements = []
    try:
        from . import uia as _uia
        stage_title = engine.stage_window.get("name") if isinstance(engine.stage_window, dict) else None
        if _uia.available() and stage_title and not full:
            vl, vt = getattr(engine.desktop, "_vleft", 0), getattr(engine.desktop, "_vtop", 0)
            for e in _uia.dump(stage_title, maxd=10, limit=200, vleft=vl, vtop=vt):
                if not (e.get("name") and e.get("patterns") and e.get("rect")):
                    continue
                r = e["rect"]
                ui_elements.append(
                    f"{e['type'].replace('Control','')} '{e['name'][:40]}' "
                    f"@({r['cx']},{r['cy']}) [{','.join(e['patterns'])}]")
    except Exception as e:
        print(f"[rpa] perceive UIA falhou: {e}", file=sys.stderr)

    meta = {
        "scope": "stage" if scoped else "full",
        "size": list(img.size),
        "visible_text": [f"{t['text']} @({t['x']},{t['y']})" for t in tokens],
        "hint": "Texto visível na tela com pontos de clique. Use rpa_click_text(<texto>) "
                "ou rpa_click_at(x,y). Se vazio, chame rpa_target_window primeiro.",
    }
    if ui_elements:
        meta["ui_elements"] = ui_elements
        meta["hint"] = ("PREFIRA `ui_elements`: são os controles reais (acessibilidade UIA) "
                        "com nome+tipo+ação e ponto EXATO — leitura confiável mesmo sem foco. "
                        "Use rpa_click_text('<nome do elemento>') (aciona por UIA) ou rpa_click_at(x,y). "
                        "`visible_text` é OCR de fallback (texto custom fora da árvore).")
    # Popup/modal bloqueando o palco? (ex.: diálogo de confirmação surgiu após o
    # target). A janela-palco fica DESABILITADA — interagir nela não tem efeito.
    # Avisa o agente p/ mirar o popup, em vez de ele ficar batendo na janela morta.
    try:
        if engine.stage_window is not None:
            popup = getattr(engine.desktop, "blocking_popup", lambda w: None)(engine.stage_window)
            if popup is not None:
                meta["popup_bloqueando"] = popup["name"]
                meta["hint"] = (f"ATENÇÃO: a janela-palco está BLOQUEADA por um popup modal "
                                f"'{popup['name']}'. Cliques/teclas na tela atual NÃO terão efeito. "
                                f"Chame rpa_target_window('{popup['name'][:30]}') e interaja com o popup.")
    except Exception:
        pass
    if extra:
        meta.update(extra)
    content = [TextContent(type="text", text=json.dumps(meta, ensure_ascii=False))]
    if with_image:
        buf = BytesIO(); img.convert("RGB").save(buf, format="PNG")
        content.append(ImageContent(type="image",
                                    data=base64.b64encode(buf.getvalue()).decode(),
                                    mimeType="image/png"))
    try:
        os.unlink(path)
    except Exception:
        pass
    return content


async def handle_call(name: str, arguments: dict) -> list[TextContent]:
    engine = get_engine()

    try:
        if name == "rpa_launch":
            r = engine.launch_app(arguments["command"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_target_window":
            r = engine.target_window(arguments["title"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_click_describe":
            # Desativado: o VLM derruba o server (OOM / >60s). Redireciona, rápido.
            return [TextContent(type="text", text=json.dumps({
                "success": False,
                "error": "rpa_click_describe desativado (VLM instável nesta máquina). "
                         "Use rpa_screenshot para ler 'visible_text' com pontos de clique, "
                         "depois rpa_click_text(<rótulo>) ou rpa_click_at(x,y)."}))]

        elif name == "rpa_screenshot":
            # Perceive: visible OCR text (+ click points) for text-only models.
            # Pass image=true to ALSO include the PNG (for vision-capable models).
            return _perceive(engine, full=bool(arguments.get("full")),
                             with_image=bool(arguments.get("image")))

        elif name == "rpa_click_at":
            r = engine.click_at(
                arguments["x"], arguments["y"],
                button=arguments.get("button", "left"),
                double=arguments.get("double", False),
            )
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_click_text":
            r = engine.click_text(
                arguments["text"],
                region=arguments.get("region"),
            )
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_click_image":
            r = engine.click_image(
                arguments["template_path"],
                threshold=arguments.get("threshold", 0.8),
            )
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_type_text":
            r = engine.type_text(arguments["text"], clear=bool(arguments.get("clear")))
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_press_key":
            r = engine.press_key(arguments["key"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_scroll":
            r = engine.scroll(arguments["lines"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_drag":
            r = engine.drag(
                arguments["from_x"], arguments["from_y"],
                arguments["to_x"], arguments["to_y"],
            )
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_wait_for_image":
            r = engine.wait_for_image(
                arguments["template_path"],
                threshold=arguments.get("threshold", 0.8),
                timeout=arguments.get("timeout", 30.0),
                interval=arguments.get("interval", 0.5),
            )
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_wait_for_text":
            r = engine.wait_for_text(
                arguments["text"],
                timeout=arguments.get("timeout", 30.0),
                interval=arguments.get("interval", 0.5),
                region=arguments.get("region"),
            )
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_describe_screen":
            # Perceive as TEXT (OCR tokens + click points) — funciona para modelos
            # só-texto (deepseek) — mais a imagem para modelos de visão.
            return _perceive(engine)

        elif name == "rpa_ask_screen":
            return _perceive(engine, extra={"question": arguments["question"]})

        elif name == "rpa_select_combobox":
            r = select_combobox(engine, arguments["label"], arguments["option"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_click_tab":
            r = click_tab(engine, arguments["tab_name"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_navigate_menu":
            r = navigate_menu(engine, arguments["items"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_fill_form":
            r = fill_form(engine, arguments["fields"])
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        elif name == "rpa_find_in_table":
            r = find_in_table(
                engine,
                arguments["header"],
                arguments["cell_text"],
                click=arguments.get("click", True),
            )
            return [TextContent(type="text", text=json.dumps(r.to_dict(), indent=2))]

        else:
            return [TextContent(
                type="text",
                text=json.dumps({"error": f"Tool desconhecida: {name}"}),
            )]

    except Exception as e:
        return [TextContent(
            type="text",
            text=json.dumps({"success": False, "error": str(e)}),
        )]


# ── Server ────────────────────────────────────────────────

RPA_INSTRUCTIONS = """\
Motor de RPA de tela (mouse + teclado + OCR). SIGA este playbook para não quebrar:

1. MIRE A JANELA PRIMEIRO. Sempre chame `rpa_target_window` (title = trecho do título)
   antes de qualquer coisa. Sem isso o OCR casa texto de outras janelas e o clique erra.
   Abra apps com `rpa_launch` (NUNCA pelo bash: um app gráfico de 1º plano trava o loop).

2. PERCEBA POR TEXTO. `rpa_screenshot` / `rpa_describe_screen` retornam `visible_text`:
   uma lista do que está na tela COM pontos de clique (ex.: "Sell @(1349,334)"). Leia essa
   lista e aja por ela. NÃO peça imagem (image=true) nem use VLM/analyze — é lento e pode travar.

3. AÇÃO: PREFIRA CLIQUES. O mouse (`rpa_click_text`, `rpa_click_at`) funciona em QUALQUER app.
   O TECLADO (`rpa_type_text`, `rpa_press_key` — inclusive F9/Enter/atalhos) pode NÃO ter efeito
   em apps Wine (ex.: MetaTrader) e alguns toolkits. Regra: se uma tecla/atalho não mudar a tela,
   NÃO insista no teclado — abra o recurso por clique (botão de toolbar, menu, painel).

4. ESPERE O INESPERADO. Podem surgir diálogos de confirmação/termos (ex.: "One Click Trading",
   "Accept", "OK", "Save"). Após cada ação importante, tire `rpa_screenshot`, leia o `visible_text`,
   e clique no botão certo. Se um botão não for achado, use o rótulo EXATO que aparece na lista.
   ATENÇÃO — MODAIS SÃO OUTRA JANELA: um diálogo (ex.: "Ordem: EURUSD…", "Substituir", "Fonte")
   abre como JANELA SEPARADA. Para interagir com ele OU fechá-lo (Escape/botão), chame
   `rpa_target_window` com o título do MODAL primeiro — teclas/cliques mirados na janela-mãe NÃO
   atingem o modal. Use `rpa_screenshot` para ver o título/campos e mire a janela certa.

5. VERIFIQUE. Confirme o efeito lendo a tela de novo (`rpa_screenshot`) antes de declarar sucesso —
   a verificação visual interna é só uma dica (`stage_changed`), não prova que a ação surtiu efeito.
"""


def _check_resources():
    """Warn (não corrige) se a memória estiver apertada — EasyOCR/VLM podem
    estourar sem headroom. Swap é config de SISTEMA (não do server): ver o
    bloco 'Requisitos' no README (precisa /swapfile + /etc/fstab)."""
    try:
        info = {}
        for line in open("/proc/meminfo"):
            k, v = line.split(":", 1)
            info[k.strip()] = int(v.strip().split()[0])  # kB
        avail_mb = info.get("MemAvailable", 0) // 1024
        swap_mb = info.get("SwapTotal", 0) // 1024
        if swap_mb == 0 and avail_mb < 4096:
            print(f"[rpa] AVISO: {avail_mb}MB livres e SEM swap. EasyOCR/VLM podem "
                  f"causar OOM. Configure swap (ver README: /swapfile + fstab).",
                  file=sys.stderr)
        elif avail_mb + swap_mb < 3072:
            print(f"[rpa] AVISO: pouca memória ({avail_mb}MB livres + {swap_mb}MB swap). "
                  f"Operações pesadas podem ficar lentas/instáveis.", file=sys.stderr)
    except Exception:
        pass


async def main():
    _check_resources()
    # Aquece o EasyOCR em background (não bloqueia o handshake MCP).
    threading.Thread(target=_warm_ocr, daemon=True).start()
    server = Server("aluy-mcp-rpa", "0.1.0", instructions=RPA_INSTRUCTIONS)

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        return await handle_call(name, arguments)

    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    asyncio.run(main())
