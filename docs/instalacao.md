# Instalação

## Como usar

O jeito normal é **não instalar**: o `uvx` busca e roda o pacote sob demanda.

```bash
uvx --from git+https://github.com/hiperplano/aluy-mcp-rpa aluy-mcp-rpa
```

> O pacote **ainda não está publicado no PyPI**. Quando estiver, o `--from` sai e o
> comando vira só `uvx aluy-mcp-rpa`.

Registrando num cliente MCP:

```bash
# aluy
aluy mcp add rpa -- uvx --from git+https://github.com/hiperplano/aluy-mcp-rpa aluy-mcp-rpa

# a partir de um clone local
aluy mcp add rpa -- uvx --from . aluy-mcp-rpa
```

O `aluy onboard` também oferece o RPA na lista de servers MCP, já com esse comando.
Depois de registrar, reinicie a sessão; `/mcp` faz o handshake e as tools
`mcp__rpa__*` aparecem — atrás da catraca de permissão.

Para desenvolver, a partir de um clone:

```bash
pip install -r requirements.txt
```

## Servidor X (Linux)

A ação no X é via `python3-Xlib` + XTest + `mss` — sem `xdotool` e sem `pyautogui`.
Basta um servidor X: o `:0` da sua sessão, um do xrdp, ou um dedicado.

```bash
# display dedicado, para automação isolada da sua sessão real
sudo apt install xvfb openbox
Xvfb :20 -screen 0 1280x1024x24 -nolisten tcp &
DISPLAY=:20 openbox &
```

Rodar num display dedicado é o modo recomendado quando a automação não precisa da
sua tela: o agente não disputa o mouse com você, e um clique errado não acerta a sua
sessão.

## Memória e swap

EasyOCR/torch ocupam 1–2 GB. Em máquina apertada isso **estoura a RAM e mata a
sessão** (OOM). O server detecta memória baixa e **avisa** no boot, mas não gerencia
swap — isso é configuração de sistema, e ele não roda como root.

```bash
sudo fallocate -l 8G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab   # persiste no reboot
```

O OCR baixa os modelos do EasyOCR (~500 MB) no primeiro uso.

## O que o server detecta no boot

O motor de capacidades sonda e registra no log. Ele **não conserta** nada — os três
são de sistema:

| Pré-requisito | Sem ele | Como prover |
| --- | --- | --- |
| **Servidor X** (`DISPLAY`) | erro: o motor não opera | `:0`, ou `Xvfb :20 -screen 0 1280x1024x24` |
| **Swap** | o OOM mata a sessão | bloco acima |
| **Wine** | apps **Windows** não abrem; os nativos seguem normais | `sudo apt install wine wine64` |

```
[rpa] capacidades: os=… x_ok=… wine=… …
```

## Windows e macOS

A detecção escolhe o **backend de ação** pelo sistema; visão, OCR, engine e a camada
MCP são os mesmos em todos.

```bash
pip install pyautogui pygetwindow
```

O motor detecta o SO e usa o backend portável (`pyautogui` + `pygetwindow` + `mss`).
O input já foi validado; o run completo com janelas ainda não foi exercitado numa
máquina Windows ou macOS — relatos são bem-vindos.

## Sanidade

```bash
export DISPLAY=:0
python3 -c "
import os; os.environ['DISPLAY']=':0'
from src.backends import detect_backends
d, v, vlm = detect_backends()
print('Desktop:', d, '| Vision:', bool(v), '| Screen:', d.screen_width, 'x', d.screen_height)
"
```

E as validações de ponta a ponta:

```bash
python3 scripts/validate_engine.py        # target+type · click_text · misto
python3 scripts/validate_mcp_dispatch.py  # camada MCP (handle_call)
python3 scripts/validate_healing.py       # self-healing por template
```
