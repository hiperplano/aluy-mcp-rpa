# Motor RPA do aluy — resultados da noite (2026-06-19)

**TL;DR:** o aluy agora **faz interações gráficas de verdade** pelas ferramentas MCP — mirar
janela, clicar elementos localizados por OCR e digitar (incl. operadores), com **self-healing**, e
**vê a tela** (screenshot retorna imagem). Server MCP **registrado no `.mcp.json`** e provado
dirigido por cliente stdio. Sem VLM (não precisa).

## ✅ Provado AO VIVO com o aluy CLI real (deepseek-v4-pro, headless --yolo)

| Run | Conta | Como abriu a calc | Resposta do aluy |
|---|---|---|---|
| 1 | 7×8 | pré-aberta | **56** ✓ |
| 2 | 9×8 | pré-aberta | **72** ✓ |
| 3 | 7×3 | **o agente abriu via `rpa_launch`** | **21** ✓ (não travou) |

Dois bugs que faltavam, achados dirigindo o aluy de verdade:
1. **Perceção em imagem p/ modelo de texto** — o deepseek não vê imagem; `rpa_screenshot`/`describe`/`ask`
   agora retornam **texto OCR com pontos de clique** (`"7 @(595,607)"`). Imagem só com `image=true`.
2. **Abrir app pelo bash trava o loop** — `xcalc` de primeiro plano nunca retorna. Nova tool
   **`rpa_launch`** abre o app destacado (não-bloqueante) e retorna na hora.

## ▶ Como usar pelo aluy (runbook da manhã)

1. **Reinicie o aluy CLI** para ele carregar o novo `.mcp.json` (server `rpa`).
   Verificado: `aluy mcp list` (rodado de `~/projects/aluy`) já lista os 3 servers
   incluindo **`rpa`**. Dentro da sessão, `/mcp` faz o handshake ao vivo (carrega as 19 tools).
   As tools de RPA ficam **atrás da catraca** (efeito ⇒ confirmação) — o aluy pede OK antes de agir.
2. Garanta apps na tela onde o aluy roda. **Decisão de display:**
   - Para automatizar **o seu desktop** (apps que você abre): o aluy roda em `DISPLAY=:10` e age ali.
     *Ressalva:* no `:10` (xrdp) a captura pode vir defasada se o cliente RDP estiver desconectado.
   - Para automação **isolada e estável**: aponte o server ao **Xvfb `:20`** (deixei de pé) — adicione
     `"env": {"DISPLAY": ":20"}` na entrada `rpa` do `.mcp.json` e lance os apps-alvo em `:20`.
3. Peça ao aluy, ex.: *"use rpa_target_window('Calculator') e rpa_type_text('7*8=') e me mostre o
   rpa_screenshot"*. Ele mira, digita e te devolve a imagem da tela.

Tools-chave: `rpa_target_window` · `rpa_click_text` · `rpa_type_text` · `rpa_click_at` ·
`rpa_press_key` · `rpa_screenshot` (retorna imagem). Ver lista completa em `src/server.py`.

---

## O que estava travando o "punch" (causas-raiz achadas)

1. **Sem palco.** A RPA agia em coordenadas absolutas no desktop inteiro, lotado dos próprios
   terminais do aluy → o OCR casava texto de outras janelas. **Faltava mirar a janela.**
2. **Clique nunca caía no alvo.** `desktop._fake_motion` passava `x,y` nas posições erradas de
   `fake_input` (caíam em `time`/`root`) → o ponteiro **não se movia** e o XTest clicava onde o
   cursor estivesse.
3. **Teclado não digitava.** Este Xorg/xrdp **descarta injeção de tecla via XTest** (mouse via
   XTest funciona; tecla não). Além disso o resolvedor de keysym usava `XK.string_to_keysym(ch)`,
   que espera o *nome* ("minus","asterisk"), não o glifo → `-`,`*`,`=` viravam 0.
4. **Verificação visual mentirosa.** Todos os `verify()` eram `return True`; e quando dei dente a
   ele, o diff before/after deste servidor vinha *stale* (~26px mesmo com mudança óbvia) →
   falso-negativo, que ainda disparava cliques repetidos.

## O que consertei

| Camada | Conserto |
|---|---|
| **Palco** (`desktop.py`) | `list_windows`/`find_window`/`activate_window`/`window_geometry`: enumera, **eleva+foca** (EWMH) e devolve a geometria. Todo OCR/clique passa a ser **escopado à janela**. |
| **Clique** (`desktop._fake_motion`) | x,y como keyword + `warp_pointer`. Agora o ponteiro vai ao alvo e o clique registra. |
| **Teclado** (`desktop.type_text`/`press_key`) | Reescrito p/ **XSendEvent** na janela focada. keysym = `ord(ch)` p/ ASCII. Shift de verdade (bracket `Shift_L`), pois o `state` mask não basta. |
| **OCR** (`vision.find_text`) | **Upscale 4× + allowlist + 1 passada** → lê dígitos/glifos pequenos (antes era cego a botão de 1 caractere). Casa exato antes de substring (`7` não casa `127`). |
| **Verificação** (`engine.click_text`) | Clica **1×**; sucesso = localizou+clicou; reporta `stage_changed` só como **informação** (o diff visual é não-confiável aqui, não serve de gate). `vision.region_changed` existe como ferramenta opcional. |
| **Captura** (`desktop.screenshot`) | Grab **fresco** por chamada (mss reutilizado vinha stale em grabs rápidos). |
| **Tools MCP novas** (`server.py`) | `rpa_target_window(title)` e `rpa_click_describe(description)` (grounding VLM, p/ quando houver RAM). |

## Validação end-to-end (a API que o aluy chama)

`scripts/validate_engine.py` — carrega o modelo 1× e exercita o `RpaEngine`, com o OCR conferindo o
**display real** da calculadora:

```
A) target_window + type_text('7*8=')              -> display '56'  ✅
B) target_window + click_text('5')                -> display '5'   ✅
C) click_text('9') + type_text('*8=')             -> display '72'  ✅   (mouse + teclado)
TOTAL: TODOS VERDES
```

`scripts/validate_mcp_dispatch.py` — chama as **tools MCP** como o cliente (aluy) faz
(`server.handle_call`):

```
rpa_target_window + rpa_type_text('7*8=')         -> display '56'  ✅
```

`scripts/validate_healing.py` — **self-healing**: `click_text` aprende um template visual do
elemento ao localizá-lo; se depois o OCR falhar, re-localiza por template-match e clica.

```
1) click_text('7')                 -> acha por OCR, aprende template
2) (OCR quebrado) click_text('7')  -> healed=True, re-localiza por template e clica  ✅
3) display tem '7'                 -> clique curado caiu certo  ✅
```

Evidências (PNG) em `scripts/evidencia/`: `A_teclado_7x8_56.png`, `teclado_9-3_6.png`,
`teclado_7x8_56.png`, `click_mouse_7.png`, `click_registra_5555.png`, `MCP_dispatch_56.png`.

## Elo final: o aluy carrega e dirige o server (corrigido + registrado)

Dois bloqueios reais que impediam o aluy de usar tudo isso — ambos corrigidos:

1. **O server stdio crashava no boot** — `main()` chamava `Server({...}, capabilities=...)`, mas o
   SDK quer `Server(name, version)`. (Meus testes anteriores passavam porque chamavam `handle_call`
   direto, sem o `main()`.) Corrigido + shim para rodar como `python3 src/server.py` (imports
   relativos).
2. **Não estava registrado** — `.mcp.json` só tinha o `desktop`. **Adicionei o server `rpa`.**

**Prova suprema** (`/tmp/mcp_drive.py`): um cliente MCP — igual ao aluy — subiu o server por **stdio**,
fez `initialize` (19 tools), e chamou `rpa_target_window` + `rpa_type_text('7*8=')`. A calculadora
mostrou **56** (evidência `MCP_stdio_drive_56.png`). É o aluy fazendo a interação pelo protocolo real.

## Ciclo agir→perceber→verificar fechado (sem VLM)

`rpa_screenshot` antes só devolvia um *path* (texto) — o agente ficava **cego** ao resultado. Agora
ele **retorna a imagem** (MCP `ImageContent`, recortada no palco), então o **aluy enxerga a tela** e
verifica suas próprias ações sem precisar do VLM (que dá OOM). Provado por MCP stdio: dirigiu a calc,
pediu `rpa_screenshot`, e a imagem decodificada mostra **56** (evidência `MCP_screenshot_o_agente_ve_56.png`).
Isso fecha o laço: **agir** (target/click/type) + **perceber** (screenshot que o agente vê).
Os três tools de perceção — `rpa_screenshot`, `rpa_describe_screen`, `rpa_ask_screen` — agora
**retornam a imagem** (recortada no palco) e **não dependem mais do VLM** (que pendurava por OOM):
o próprio agente descreve/responde olhando a imagem. Validado por MCP stdio: os 3 devolvem 1 imagem.

## Ferramentas confiáveis pro aluy hoje

`rpa_target_window` → depois `rpa_type_text` / `rpa_press_key` (teclado, robusto) e
`rpa_click_text` / `rpa_click_image` / `rpa_click_at` (mouse, universal). Fluxo típico:
**mirar a janela** e então digitar/clicar — cobre a maioria dos apps reais (campos, botões de texto,
diálogos, navegação por Tab).

## Display dedicado Xvfb `:20` (instalado e validado nesta noite)

Instalei `Xvfb` + `openbox` e subi um display **`:20` (1280×1024)** isolado do `:10` (xrdp). Rodei a
mesma validação lá:

```
:20  A) type_text('7*8=') -> 56   ✅   B) click_text('5') -> 5   ✅   C) misto 9*8 -> 72   ✅
:20  TODOS VERDES
```

Ou seja, **o engine roda igual num display dedicado** — e ali a captura é sempre viva (sem o
problema de stale do xrdp). É a topologia recomendada p/ produção: cada job de RPA no seu Xvfb,
sem poluição e sem disputar a sessão do usuário. (Xvfb `:20` ficou de pé p/ você inspecionar.)

## Generalidade além da calculadora (achado importante)

Testei num editor GTK (mousepad), lendo o resultado pela **árvore de acessibilidade AT-SPI**
(estado vivo do widget, imune à captura stale):

- **Mouse (XTest): universal** — clica qualquer elemento localizado, em qualquer app. Confiável,
  em `:10` E `:20`.
- **Teclado: funciona em Athena E GTK.** `7*8=`/`9-3=` digitam perfeito no **xcalc** (Athena) e
  `calc` foi digitado no campo de busca do **xfce4-appfinder** (GtkEntry), que filtrou a lista —
  prova que o XSendEvent chega no GTK (evidência `teclado_GTK_appfinder_calc.png`).
- **Exceção conhecida: `mousepad` (GtkSourceView).** Esse editor específico não recebeu texto por
  nenhum método (XSendEvent/XTest/xdotool, em `:10` e `:20`) — é um caso isolado do widget de
  source-view em headless (foco/render), NÃO uma falha geral de GTK. Apps com `entry`/campos comuns
  funcionam.

**Sobre o AT-SPI (a11y):** instalado e o registry roda; enumera os apps. MAS no teste o mousepad
(GtkSourceView) **não expôs o widget de texto** (árvore de acessibilidade incompleta nessa config),
então não serviu de oráculo nem de fallback de escrita p/ esse app. Conclusão honesta: a11y aqui é
**app-dependente** e precisa de setup; não é um atalho garantido hoje.

**Implicação prática:** o caminho **robusto e universal é o mouse** (clicar elementos localizados por
OCR/template). Teclado é confiável onde a injeção sintética é aceita (Athena, e a confirmar Qt). Para
GTK, a entrada de texto via teclado fica pendente de solução (ver próximos passos).

## Limitações conhecidas / próximos passos

- **VLM grounding (`minicpm-v`) morre por OOM** na box (~1Gi livre). Glifos/ícones que o OCR não lê
  dependeriam dele → hoje use teclado/template. Precisa de RAM.
- **Teclado:** funciona em Athena (xcalc) e GTK (`entry`/appfinder) via XSendEvent. Exceção: o
  `GtkSourceView` do mousepad em headless não recebeu (caso isolado). Apps "paranoicos" (xterm,
  `allowSendEvents:false`) ignoram XSendEvent por design — aí o mouse cobre. XTest-tecla é descartado
  por este Xorg/xrdp (por isso XSendEvent).
- **Captura before/after stale** no Xorg/xrdp (pior com cliente RDP desconectado). Leitura por
  captura única funciona. **Próximo passo robusto: display Xvfb dedicado** (captura sempre viva,
  sem poluição, sem te incomodar) — Xvfb não está instalado (precisa `apt`).
- **Self-healing** já construído (template visual + re-localização). Evolução futura: descriptor
  multi-sinal (âncoras + texto + estrutura), não só template.

## Como rodar

```bash
export DISPLAY=:10.0
cd ~/projects/aluy/aluy-mcp-rpa
python3 scripts/validate_engine.py        # A/B/C
python3 scripts/validate_mcp_dispatch.py  # camada MCP
```
