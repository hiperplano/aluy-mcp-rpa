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

# Garante DISPLAY
if not os.environ.get("DISPLAY"):
    os.environ["DISPLAY"] = ":10"

try:
    from mcp.server import Server, NotificationOptions
    from mcp.server.stdio import stdio_server
    from mcp.types import Tool, TextContent
except ImportError:
    print("mcp SDK não encontrado. Instale: pip install mcp", file=sys.stderr)
    sys.exit(1)

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

_engine: RpaEngine = None


def get_engine() -> RpaEngine:
    global _engine
    if _engine is None:
        desktop, vision, vlm = detect_backends()
        if desktop is None:
            raise RuntimeError("Desktop backend indisponível")
        if vision is None:
            raise RuntimeError("Vision backend indisponível")
        _engine = RpaEngine(desktop, vision, vlm)
    return _engine


# ── Tool schemas ──────────────────────────────────────────

TOOLS = [
    Tool(
        name="rpa_screenshot",
        description="Tira um screenshot da tela primária e salva em /tmp.",
        inputSchema={
            "type": "object",
            "properties": {},
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
        description="Digita texto no elemento com foco.",
        inputSchema={
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Texto a digitar."},
            },
            "required": ["text"],
        },
    ),
    Tool(
        name="rpa_press_key",
        description="Pressiona tecla ou combo (ex.: 'ctrl+c', 'alt+F4', 'Return', 'Escape').",
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
        description="Descreve a tela atual usando VLM (Ollama ou BLIP).",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="rpa_ask_screen",
        description="Faz uma pergunta sobre a tela atual (VLM).",
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
]

TOOL_MAP = {t.name: t for t in TOOLS}


# ── Tool dispatcher ───────────────────────────────────────

async def handle_call(name: str, arguments: dict) -> list[TextContent]:
    engine = get_engine()

    try:
        if name == "rpa_screenshot":
            path = engine.screenshot()
            return [TextContent(type="text", text=json.dumps({"path": path}))]

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
            r = engine.type_text(arguments["text"])
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
            desc = engine.describe_screen()
            return [TextContent(type="text", text=desc)]

        elif name == "rpa_ask_screen":
            answer = engine.ask_screen(arguments["question"])
            return [TextContent(type="text", text=answer)]

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

async def main():
    server = Server(
        {"name": "aluy-mcp-rpa", "version": "0.1.0"},
        capabilities={"tools": {}},
    )

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
