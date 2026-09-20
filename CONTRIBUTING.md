# Contribuindo com o aluy-mcp-rpa

Obrigado pelo interesse. Automação de tela quebra de formas que não aparecem
lendo código — então a regra deste repositório é **medir, não deduzir**.

## Ambiente

```bash
git clone https://github.com/hiperplano/aluy-mcp-rpa && cd aluy-mcp-rpa
pip install -r requirements.txt
export DISPLAY=:0        # ou :20, num Xvfb dedicado
```

Python ≥ 3.10. No Linux é preciso um servidor X; em Windows/macOS,
`pip install pyautogui pygetwindow`. Detalhes em [docs/instalacao.md](docs/instalacao.md).

> Trabalhe num **display dedicado** sempre que puder. O server move o seu mouse
> de verdade: rodar validação na sua sessão ativa significa perder o controle do
> cursor no meio do teste.

## Validar

Não há suíte unitária que substitua o display real. As três validações rodam
contra um X de verdade e são o gate honesto deste projeto:

```bash
python3 scripts/validate_engine.py        # target+type · click_text · misto
python3 scripts/validate_mcp_dispatch.py  # camada MCP (handle_call)
python3 scripts/validate_healing.py       # self-healing por template
```

Sanidade do backend:

```bash
python3 -c "
import os; os.environ['DISPLAY']=':0'
from src.backends import detect_backends
d, v, vlm = detect_backends()
print('Desktop:', d, '| Vision:', bool(v), '| Screen:', d.screen_width, 'x', d.screen_height)
"
```

## Fluxo de PR

1. Branch própria, PR contra `main`.
2. Diga **em que ambiente você validou** — sistema, servidor gráfico, app
   automatizado. Um PR de automação de tela sem isso não é revisável.
3. Se mexeu no engine ou nos backends, rode as três validações e cole a saída.

## Onde as coisas ficam

| | |
| --- | --- |
| `src/server.py` | as tools MCP: schema, descrição e dispatch |
| `src/engine.py` | o laço: screenshot → ação → screenshot → verificação → retry |
| `src/backends/` | ação por SO (Xlib/XTest no Linux, pyautogui no resto) e visão |
| `src/complex/` | controles compostos (combobox, tabs, menus, tabelas, formulários) |
| `scripts/` | as validações contra display real |

## Duas coisas que valem saber antes de propor mudança

**A descrição de uma tool é interface.** O texto em `Tool(description=…)` é o que
o modelo lê para decidir quando usá-la — mudá-lo muda o comportamento do agente
tanto quanto mudar o código. Os avisos ali ("o teclado pode não funcionar em apps
Wine", "use `clear=true` em campo numérico") estão lá porque custaram
dogfooding.

**Percepção volta como texto.** O `rpa_screenshot` devolve o texto visível com
pontos de clique, e a imagem é opt-in. É a decisão central do projeto: faz o
server funcionar com qualquer modelo, sem exigir visão. Proposta que inverta isso
precisa de um argumento forte.

## Segurança

Falha de segurança não vai em issue pública — ver [SECURITY.md](SECURITY.md).
