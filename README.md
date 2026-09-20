<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/hiperplano/aluy-mcp-rpa/main/docs/aluy-wordmark-white.png">
    <img src="https://raw.githubusercontent.com/hiperplano/aluy-mcp-rpa/main/docs/aluy-wordmark.png" alt="Aluy" height="48">
  </picture>
</p>

<h1 align="center">aluy-mcp-rpa</h1>

<p align="center">
  <b>Olhos e mãos para o seu agente no desktop.</b><br>
  Um server MCP que lê a tela, clica e digita — e devolve o que vê como <b>texto</b>.
</p>

<p align="center">
  <a href="LICENSE"><img alt="Licença MIT" src="https://img.shields.io/badge/licen%C3%A7a-MIT-blue"></a>
  <img alt="Python 3.10+" src="https://img.shields.io/badge/python-3.10%2B-blue">
  <img alt="MCP" src="https://img.shields.io/badge/MCP-server-8A2BE2">
  <img alt="Linux · Windows · macOS" src="https://img.shields.io/badge/-Linux%20%C2%B7%20Windows%20%C2%B7%20macOS-informational">
</p>

---

Automação de desktop para quando **não existe API**: o sistema legado, o terminal
gráfico, o app que só tem interface. O agente enxerga a janela, encontra o botão
pelo texto, clica e confere o resultado.

```bash
# registra no aluy (ou em qualquer cliente MCP)
aluy mcp add rpa -- uvx --from git+https://github.com/hiperplano/aluy-mcp-rpa aluy-mcp-rpa
```

Depois, dentro da sessão, é só pedir — *"abra a calculadora, digite 12 × 7 e me diga
o resultado"* — e o agente conduz:

```
rpa_launch("xcalc")            → pid 48213, janela nova
rpa_target_window("xcalc")     → palco: xcalc @ 1280x1024
rpa_screenshot()               → "7 @(412,377) · × @(455,377) · = @(498,420) …"
rpa_click_text("7") …          → ok (verificado)
rpa_screenshot()               → "84 @(430,210)"
```

## A ideia central

**Percepção volta como TEXTO, não como imagem.** O `rpa_screenshot` devolve o que
está escrito na tela junto com as coordenadas de clique (`Salvar @(1349,334)`),
porque o modelo do agente é de texto — mandar um PNG gasta contexto, exige um
modelo com visão, e ainda deixa a decisão de "onde clicar" no lugar errado. A
imagem existe, mas é opt-in (`image=true`).

É isso que faz o server funcionar com **qualquer modelo**, inclusive os baratos e
os locais, sem VLM.

## O que ele tem de diferente

**Palco antes de tudo.** `rpa_target_window` mira a janela e **escopa** OCR e cliques
a ela. Sem isso, num desktop compartilhado, o OCR casa o texto da janela errada e o
agente clica em outro app — o tipo de bug que só aparece em produção.

**Localizar sobrevive a mudança.** O texto é achado por OCR (EasyOCR, com recorte
ampliado 4×) e, ao localizar, o server **guarda um template visual**. Se o OCR falhar
depois — fonte diferente, tema novo, antialias —, ele re-localiza por template
matching. É *self-healing*, não uma coordenada fixa que quebra na próxima versão do
app.

**O agente aprende a interface e para de re-explorar.** Há um repositório de objetos
por trás: `rpa_learn_screen` guarda a análise que o agente fez de uma tela nova;
`rpa_screen_map` devolve depois o que já se sabe dela — controles, menus já abertos,
e que ação leva a que tela; e `rpa_goto("Buy a mercado")` **percorre sozinho a rota
já aprendida** até o controle-alvo, sem redescobrir o caminho. Na segunda visita a
automação fica rápida e determinística.

**Abrir app sem travar o loop.** `rpa_launch` sobe o programa destacado e retorna na
hora com o pid e as janelas novas. Um app gráfico chamado pelo bash não retorna e
prende o agente — é o erro que todo mundo comete uma vez.

## ⚠️ Antes de instalar

Este server **controla o seu mouse e o seu teclado**. Ele digita em janelas reais,
clica em botões reais, e não distingue o seu editor de texto do seu internet
banking. Um agente com acesso a ele pode fazer no seu desktop o que você faria.

- Prefira um **display dedicado** (`Xvfb`) ou uma VM quando a tarefa não exigir a
  sua sessão real.
- No cliente MCP, mantenha as tools `mcp__rpa__*` **atrás da catraca de permissão**.
  Um server MCP roda com os **seus** privilégios, sem sandbox.
- Não deixe credencial visível durante uma automação: o OCR lê o que estiver na
  tela, e esse texto vai para o contexto do modelo.

O que conta como vulnerabilidade aqui — e o que não conta — está em
[SECURITY.md](SECURITY.md). Falha de segurança **não** vai em issue pública:
use o [canal privado](https://github.com/hiperplano/aluy-mcp-rpa/security/advisories/new).

## Requisitos

- **Python ≥ 3.10**
- **Linux:** um servidor X (`DISPLAY`) — o `:0`, um do xrdp, ou um `Xvfb` dedicado
- **Memória:** EasyOCR/torch pesam 1–2 GB. Garanta **swap** — sem ele o OOM mata a
  sessão. O server **detecta e avisa** no boot, mas não conserta: é config de sistema
- **Wine**, só para automatizar app Windows sobre Linux

O log de boot imprime o que foi detectado: `[rpa] capacidades: os=… x_ok=… wine=…`.
Passo a passo em [docs/instalacao.md](docs/instalacao.md).

## Suporte por sistema

O que muda entre os sistemas é só o **backend de ação**; visão, OCR, engine e a
camada MCP são os mesmos.

| Sistema | Backend de ação | Estado |
| --- | --- | --- |
| **Linux / X11** | Xlib + XTest/XSendEvent + EWMH | ✅ provado — é o caminho default |
| **Windows / macOS** | pyautogui + pygetwindow + mss | ⚠️ implementado; **input validado**, mas o run completo com janelas ainda não foi exercitado numa máquina Win/Mac |

Screenshot é via `mss` nos dois casos. Relato de uso em Windows e macOS é
especialmente bem-vindo — é a lacuna conhecida do projeto.

## Documentação

| | |
| --- | --- |
| [Ferramentas](docs/ferramentas.md) | as 22 tools MCP, uma a uma |
| [Instalação](docs/instalacao.md) | dependências de sistema, swap, Xvfb, Wine |

## Contribuir

PRs são bem-vindos — o [CONTRIBUTING.md](CONTRIBUTING.md) tem o essencial, e o
[código de conduta](CODE_OF_CONDUCT.md) vale para todo mundo.

```bash
git clone https://github.com/hiperplano/aluy-mcp-rpa && cd aluy-mcp-rpa
pip install -r requirements.txt
export DISPLAY=:0                         # ou :20, num Xvfb dedicado
python3 scripts/validate_engine.py        # target+type · click_text · misto
python3 scripts/validate_mcp_dispatch.py  # camada MCP (handle_call)
python3 scripts/validate_healing.py       # self-healing por template
```

Automação de tela falha de formas que só aparecem no display real — então a regra
aqui é **medir, não deduzir**. Um relato de bug com o app, o tema e o que apareceu
na tela vale mais do que qualquer leitura de código.

## Licença

[MIT](LICENSE) © Hiperplano
