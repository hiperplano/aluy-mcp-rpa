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
│  ├─ Desktop: Xlib + XTest (mouse) +       │
│  │           XSendEvent (teclado) + mss    │
│  ├─ Vision: OpenCV + EasyOCR (upscale)    │
│  └─ Palco: target_window (escopo+foco)    │
│                                            │
│  Complex UI:                               │
│  ├─ Combobox, Tabs, Menus                 │
│  ├─ Tables, Forms                         │
└────────────────────────────────────────────┘
```

> **Estado (validado 2026-06-19):** mouse (XTest), teclado (XSendEvent — XTest-tecla é
> descartado neste Xorg/xrdp), window-targeting, OCR com upscale, **self-healing** por
> template e perceção via `rpa_screenshot` (retorna o TEXTO visível com pontos de clique; imagem opt-in). VLM NÃO é usado
> (OOM na box). Detalhes e provas em [`RESULTADO-NOITE-2026-06-19.md`](RESULTADO-NOITE-2026-06-19.md).

## Design

- **Palco primeiro**: `rpa_target_window` mira a janela e escopa OCR/clique a ela (senão o OCR
  casa texto de outras janelas no desktop compartilhado).
- **Localização**: OCR (EasyOCR, recorte ampliado 4×) + Template Matching; `click_text` casa
  exato antes de substring.
- **Self-healing**: ao localizar por OCR, guarda um template visual; se o OCR falhar depois,
  re-localiza por template-match.
- **Perceber**: `rpa_screenshot` devolve o **TEXTO visível** com pontos de clique (ex.: `Sell @(1349,334)`) — porque o modelo do agente é texto, não visão. Imagem opt-in via `image=true`.
- **Verificação**: best-effort (diff de região é informativo, não bloqueia — o diff before/after
  é instável neste servidor; a asserção real é o agente olhando o screenshot).

## Ferramentas (MCP tools)

| Tool | Descrição |
|------|-----------|
| `rpa_target_window` | **Mira a janela-alvo (palco)**: eleva+foca e escopa buscas/cliques a ela |
| `rpa_screenshot` | Lê a tela — **retorna o TEXTO visível** + pontos de clique (recortado no palco); imagem opt-in (`image=true`) |
| `rpa_click_at` | Clique em (x,y) |
| `rpa_click_text` | Encontra texto via OCR (upscale) e clica — com **self-healing** por template |
| `rpa_click_image` | Template matching + clique |
| `rpa_click_describe` | Grounding por descrição via VLM (quando houver RAM) |
| `rpa_type_text` | Digita texto (XSendEvent) |
| `rpa_press_key` | Tecla/combo (ctrl+c, alt+F4, etc) |
| `rpa_scroll` | Scroll vertical |
| `rpa_drag` | Arrasta mouse |
| `rpa_wait_for_image` | Polling até template aparecer |
| `rpa_wait_for_text` | Polling OCR até texto aparecer |
| `rpa_describe_screen` | Retorna o TEXTO visível da tela p/ o agente descrever |
| `rpa_ask_screen` | Retorna o TEXTO visível p/ o agente responder a pergunta |
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
# Ação no X é via python3-Xlib + XTest + mss (sem xdotool/pyautogui).
# Só precisa de um servidor X (ex.: o :10 do xrdp, ou um Xvfb dedicado):
sudo apt install xvfb openbox    # opcional: display dedicado p/ automação isolada
# Xvfb :20 -screen 0 1280x1024x24 -nolisten tcp & ; DISPLAY=:20 openbox &

# EasyOCR (OCR primário, ~500MB no primeiro uso) — instalado via pip (requirements.txt)
```

### Memória / swap (IMPORTANTE — config de SISTEMA, não do server)

EasyOCR/torch (~1-2GB) e, se usados, VLMs pesados podem **estourar a RAM e matar a sessão**
(OOM) em máquinas apertadas. O server **não gerencia swap** (é root/sistema) — ele só **avisa**
no startup se a memória estiver baixa. Garanta swap (sobrevive a reboot):

```bash
sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # persiste no reboot
```

### Pré-requisitos de SO que o server DETECTA e AVISA no startup (EST-1145)

O motor de detecção (`capabilities`) sonda e loga no boot — **não conserta** (são de sistema):

| Pré-requisito | Sem ele | Como prover |
|---|---|---|
| **Servidor X** (`DISPLAY`) | ERRO — o motor não opera | xrdp/`:10`, ou `Xvfb :20 -screen 0 1280x1024x24` |
| **Swap** (memória) | OOM mata a sessão | ver bloco acima |
| **Wine** | apps **Windows** (ex.: MetaTrader) não abrem; nativos ok | `sudo apt install wine wine64` |

O log de boot mostra tudo: `[rpa] capacidades: ... x_ok=… wine=… …`.

## Registro como MCP server

```bash
aluy mcp add rpa -- python3 /home/aluy/projects/aluy/aluy-mcp-rpa/src/server.py
```

Já registrado no `.mcp.json` do projeto (`aluy mcp list` mostra `rpa`). Reinicie a sessão Aluy;
dentro dela, `/mcp` faz o handshake e as tools `mcp__rpa__*` aparecem (atrás da catraca).

## Desenvolvimento / validação

```bash
export DISPLAY=:10.0        # ou :20 (Xvfb dedicado)
python3 scripts/validate_engine.py        # target+type / click_text / misto (A/B/C)
python3 scripts/validate_mcp_dispatch.py  # camada MCP (handle_call)
python3 scripts/validate_healing.py       # self-healing por template
```

```bash
# Sanidade do backend
python3 -c "
import os; os.environ['DISPLAY']=':10'
from src.backends import detect_backends
d, v, vlm = detect_backends()
print('Desktop:', d, '| Vision:', bool(v), '| Screen:', d.screen_width, 'x', d.screen_height)
"
```
