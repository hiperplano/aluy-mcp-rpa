# aluy-mcp-rpa

**MCP server de automação visual RPA.** Orquestra ferramentas de desktop e visão em rotinas determinísticas com verificação visual.

## Arquitetura

```
┌────────────────────────────────────────────┐
│  Aluy Agent (LLM)                         │
│  └─ mcp__rpa__rpa_click_text("OK")        │
└──────────────┬─────────────────────────────┘
               │ MCP stdio
┌──────────────▼─────────────────────────────┐
│  aluy-mcp-rpa (Python MCP server)         │
│                                            │
│  ┌──────────────────────────────────────┐  │
│  │  RpaEngine (core loop)               │  │
│  │                                       │  │
│  │  1. before screenshot                 │  │
│  │  2. do_action (desktop backend)       │  │
│  │  3. after screenshot                  │  │
│  │  4. verify (vision backend)           │  │
│  │  5. retry on failure (max 3x)        │  │
│  │  6. escalate to VLM on hard failure   │  │
│  └──────────────────────────────────────┘  │
│                                            │
│  Backends:                                 │
│  ├─ Desktop: xdotool | pyautogui          │
│  ├─ Vision: OpenCV + EasyOCR              │
│  └─ VLM: Ollama | BLIP (fallback)        │
│                                            │
│  Complex UI:                               │
│  ├─ Combobox, Tabs, Menus                 │
│  ├─ Tables, Forms                         │
└────────────────────────────────────────────┘
```

## Design

- **Loop interno determinístico**: ações executadas localmente, sem round-trip ao LLM
- **Verificação visual obrigatória**: screenshot antes/depois + diff OpenCV em cada ação
- **Retry automático**: até 3 tentativas com backoff
- **Escalação para LLM**: em falhas repetidas, o agente decide o próximo passo
- **OCR + Template Matching**: localização rápida de elementos visuais

## Ferramentas (MCP tools)

| Tool | Descrição |
|------|-----------|
| `rpa_screenshot` | Screenshot da tela |
| `rpa_click_at` | Clique em (x,y) com verificação visual |
| `rpa_click_text` | Encontra texto via OCR e clica |
| `rpa_click_image` | Template matching + clique |
| `rpa_type_text` | Digita texto |
| `rpa_press_key` | Tecla/combo (ctrl+c, alt+F4, etc) |
| `rpa_scroll` | Scroll vertical |
| `rpa_drag` | Arrasta mouse |
| `rpa_wait_for_image` | Polling até template aparecer |
| `rpa_wait_for_text` | Polling OCR até texto aparecer |
| `rpa_describe_screen` | Descrição VLM da tela |
| `rpa_ask_screen` | Pergunta sobre a tela (VLM) |
| `rpa_select_combobox` | Seleciona opção de dropdown |
| `rpa_click_tab` | Clica em tab |
| `rpa_navigate_menu` | Navega menu hierárquico |
| `rpa_fill_form` | Preenche formulário completo |
| `rpa_find_in_table` | Encontra célula em tabela |

## Instalação

```bash
pip install -r requirements.txt
```

### Dependências do sistema

```bash
# xdotool (backend desktop primário)
sudo apt install xdotool imagemagick

# EasyOCR (OCR primário, ~500MB no primeiro uso)
# Instalado automaticamente via pip
```

## Registro como MCP server

```bash
aluy mcp add rpa -- python3 /caminho/para/aluy-mcp-rpa/src/server.py
```

Depois reinicie a sessão Aluy para as tools `mcp__rpa__*` aparecerem.

## Desenvolvimento

```bash
# Teste manual do backend
python3 -c "
import os; os.environ['DISPLAY']=':10'
from src.backends import detect_backends
d, v, vlm = detect_backends()
print('Desktop:', d)
print('Vision:', v)
print('VLM:', vlm)
print('Screen:', d.screen_width, 'x', d.screen_height)
"
```
