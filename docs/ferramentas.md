# Ferramentas MCP

As 22 tools que o server expõe. No cliente elas aparecem com o prefixo do server —
no aluy, `mcp__rpa__rpa_click_text` e assim por diante — e **todas passam pela
catraca de permissão** antes de virar efeito.

A descrição de cada uma vem do próprio `src/server.py`: é o mesmo texto que o modelo
lê para decidir quando usá-la.

## Palco

Chame isto **antes** de interagir com um app.

| Tool | O que faz |
| --- | --- |
| `rpa_target_window` | Define a janela-alvo (o **palco**): eleva/foca a janela cujo título contém `title` e passa a restringir **todas** as buscas (OCR, template) e cliques a ela. Devolve a região `{x,y,width,height}`. Sem palco, o OCR casa texto de outras janelas |
| `rpa_launch` | Abre um app gráfico **sem bloquear** (destacado). Use isto em vez do bash: um processo gráfico em primeiro plano não retorna e trava o loop do agente. Volta na hora com o pid e as janelas novas |

## Percepção

Todas devolvem o **texto visível** com pontos de clique (`Salvar @(1349,334)`) — é a
forma que o agente consegue usar diretamente.

| Tool | O que faz |
| --- | --- |
| `rpa_screenshot` | Lê a tela e devolve o texto visível com os pontos de clique. Recortado no palco; `full=true` para a tela inteira; `image=true` inclui o PNG (só para modelos de visão) |
| `rpa_describe_screen` | Igual ao `rpa_screenshot` — existe para o agente descrever a tela |
| `rpa_ask_screen` | Igual, para o agente responder uma pergunta sobre o que está na tela |

## Ação

| Tool | O que faz |
| --- | --- |
| `rpa_click_at` | Clica em coordenadas absolutas `(x,y)`. Verifica mudança visual |
| `rpa_click_text` | Encontra o texto por OCR e clica, com verificação antes/depois e **self-healing** por template |
| `rpa_click_image` | Template matching de um `.png` na tela, e clica |
| `rpa_type_text` | Digita no elemento com foco. Use `clear=true` para **limpar o campo antes** — essencial em campos numéricos (spinbox), que senão concatenam e viram lixo |
| `rpa_press_key` | Tecla ou combo (`ctrl+c`, `alt+F4`, `Return`, `Escape`, `F9`) |
| `rpa_scroll` | Scroll vertical. Positivo = cima, negativo = baixo |
| `rpa_drag` | Arrasta o mouse de um ponto a outro |

> **Teclado nem sempre funciona.** Em apps Wine e em alguns toolkits, teclas e
> atalhos podem não ter efeito. Se a tela não mudar, **não insista no teclado** —
> abra o recurso por clique (botão de toolbar, menu, painel). Se o texto não
> aparecer ao digitar, clique no campo antes. As próprias descrições das tools
> avisam o modelo sobre isso.

## Espera

| Tool | O que faz |
| --- | --- |
| `rpa_wait_for_text` | Polling por OCR até o texto aparecer. Timeout default: 30s |
| `rpa_wait_for_image` | Polling até o template aparecer. Timeout default: 30s |

## Controles compostos

Atalhos para padrões de UI que, feitos na mão, viram uma sequência longa de cliques.

| Tool | O que faz |
| --- | --- |
| `rpa_select_combobox` | Seleciona a opção de um dropdown pelo texto do label |
| `rpa_click_tab` | Clica numa aba pelo nome visível |
| `rpa_navigate_menu` | Navega um menu hierárquico, clicando cada item em sequência |
| `rpa_fill_form` | Preenche um formulário com vários campos |
| `rpa_find_in_table` | Encontra uma célula em tabela (por cabeçalho + conteúdo) e, opcionalmente, clica |

## Memória da interface

O diferencial do projeto: em vez de redescobrir a tela toda vez, o agente **aprende**
e consulta o que já sabe.

| Tool | O que faz |
| --- | --- |
| `rpa_learn_screen` | **Aprendizado ativo:** depois de analisar uma tela nova (propósito, controles, fluxos possíveis), o agente salva a análise aqui. Fica no cache (*object repository*) e volta para ele na próxima visita. Use quando a percepção marcar `tela_nova` |
| `rpa_screen_map` | Devolve o **mapa cacheado** da tela: controles por tipo, menus já descobertos e transições conhecidas (que ação leva a que tela). É "o que eu já sei daqui" sem re-explorar — bom para planejar antes de agir |
| `rpa_goto` | Navega até a tela que contém um controle-alvo pela **rota já aprendida**. `rpa_goto('Buy a mercado')` executa sozinho os passos intermediários. `click=true` também aciona o alvo no fim. Se a rota ainda não foi aprendida, retorna erro — navegue manualmente uma vez, e o grafo aprende |

## Desativada

| Tool | Situação |
| --- | --- |
| `rpa_click_describe` | **Fora do toolset.** Era o grounding por descrição via VLM. Foi removida da lista de tools porque o VLM se mostrou instável (OOM em máquinas com pouca RAM); o dispatcher ainda responde, mas devolve erro explicando. Não conte com ela |
