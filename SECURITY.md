# Política de segurança

## Entenda o que este server é

O `aluy-mcp-rpa` **controla o mouse e o teclado da máquina onde roda**. Ele lê a
tela por OCR, move o cursor, clica e digita — em janelas reais, com os
privilégios do usuário. Não distingue um editor de texto de um internet banking.

Isso muda o que conta como falha aqui: o poder do server é o produto, não o bug.
O que nos interessa é ele fazer algo **além** do que o usuário pediu, ou vazar o
que viu.

## Como relatar uma vulnerabilidade

**Não abra uma issue pública.**

👉 **[Relatar vulnerabilidade](https://github.com/hiperplano/aluy-mcp-rpa/security/advisories/new)**

Só os mantenedores enxergam o relato, e a correção é discutida ali mesmo até a
publicação. Ajuda muito saber o sistema, o servidor gráfico (X11/Wayland/Windows/
macOS), o app automatizado e o menor caminho que reproduz.

Damos retorno em até **5 dias úteis**. Não há programa de recompensa.

## O que É vulnerabilidade aqui

- **Ação fora do palco.** O `rpa_target_window` escopa buscas e cliques à janela
  alvo. Clicar ou digitar fora dela, sem o usuário ter pedido, é falha — é o
  caminho para o agente acertar a janela errada.
- **Vazamento do que a tela mostrou.** O OCR lê tudo o que está visível. Se esse
  texto sair para log, arquivo, cache ou rede além do canal MCP que o cliente
  abriu, é falha.
- **Escrita fora dos diretórios do server.** Template, cache e repositório de
  objetos devem ficar onde o server declara.
- **Execução de comando não solicitada.** O `rpa_launch` abre o que o cliente
  mandou abrir. Qualquer caminho que execute outra coisa — por injeção no
  argumento, ou por texto lido da tela virando comando — é falha grave.
- **Conteúdo da tela virando instrução.** O texto que o OCR devolve é **dado**.
  Se ele conseguir ser interpretado como ordem pelo próprio server, é falha.

## O que NÃO é vulnerabilidade

- **O server clicar e digitar.** É o que ele faz.
- **Um agente com acesso a ele fazer besteira.** O controle de quem pode chamar
  as tools é do cliente MCP — no aluy, a catraca de permissão. Este server não
  tem como saber se o clique é uma boa ideia.
- **Rodar sem sandbox, com os privilégios do usuário.** É a natureza de um server
  MCP, está dito no README, e a mitigação é operacional: display dedicado ou VM.
- **OCR errar.** Precisão de reconhecimento é qualidade, não segurança.

## Recomendações de operação

- Rode num **display dedicado** (`Xvfb`) ou numa VM quando a tarefa não exigir a
  sua sessão real.
- Mantenha as tools atrás da **catraca de permissão** do cliente MCP.
- **Não deixe credencial visível** durante uma automação: o OCR lê o que estiver
  na tela, e esse texto vai para o contexto do modelo — que pode ser um serviço
  de terceiro.
