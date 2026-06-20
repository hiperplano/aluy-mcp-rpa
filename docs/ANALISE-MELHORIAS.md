# Análise de Melhorias — Motor RPA do aluy
## Gerado por 4 sub-agentes em paralelo (2026-06-20)

---

# PARTE 1: VERIFY — Quais métodos têm verificação real?

## 📊 Tabela geral (engine.py)

| # | Método | Linha do `check()` | Código do verify | Status |
|---|--------|-------------------|------------------|--------|
| 1 | `click_at` | ~435 | `return True` | ❌ NO-OP |
| 2 | `click_image` | ~460 | `return True` | ❌ NO-OP |
| 3 | `click_text` | — (não usa `act()`) | `region_changed(...)` (linha ~572) | ✅ REAL (mas só informativo, não é gate) |
| 4 | `click_describe` | ~649 | `return state["loc"] is not None` | ✅ REAL |
| 5 | `type_text` | ~703 | `return True  # Can't verify typed text without OCR` | ❌ NO-OP |
| 6 | `press_key` | ~716 | `return True` | ❌ NO-OP |
| 7 | `scroll` | ~749 | `return True  # Soft check` (diff calculado e DESCARTADO!) | ❌ NO-OP |
| 8 | `drag` | ~761 | `return True` | ❌ NO-OP |
| 9 | `wait_for_image` | ~797 | `return loc is not None` | ✅ REAL |
| 10 | `wait_for_text` | ~835 | `return loc is not None` | ✅ REAL |

## 🔴 Detalhe de cada NO-OP

### `scroll` (P0 — diff já calculado e descartado)
Linhas 741-749 de engine.py. O código TIRA duas screenshots extras, calcula o diff, e **descarta o resultado**. Sempre retorna `True`. É 1 linha de correção: usar o diff já computado.

### `type_text` (P0 — engine tem OCR, não usa)
`check() = return True  # Can't verify typed text without OCR` — COMENTÁRIO FALSO. O engine TEM `vision.find_text()`. Basta tirar um after-screenshot e verificar se o texto apareceu no palco.

### `press_key` (P1)
`check() = return True`. Sugestão: `region_changed(before, after)` — se uma tecla foi pressionada, algo na tela **deveria** mudar.

### `drag` (P1)
`check() = return True`. Sugestão: `region_changed` no bounding box que engloba `from` e `to`.

### `click_at` (P2)
`check() = return True`. O `before_color` JÁ É CAPTURADO (linha 430-432) mas nunca usado. Verificar se a cor mudou ou `region_changed` ao redor do ponto.

### `click_image` (P2)
`check() = return True`. Confia cegamente no template match. Sugestão: `region_changed` na região do template.

---

# PARTE 2: DISPATCHER — Quais tools do server.py passam por act()?

## 🔴 Tools com verify DUMMY (check() = return True)

- `rpa_click_at`
- `rpa_click_image`
- `rpa_type_text` (keyboard path)
- `rpa_press_key`
- `rpa_scroll` (wheel path)
- `rpa_drag`
- `rpa_click_tab`

## 🔴 Tools que NEM USAM act() (backend direto, sem screenshot, sem verify)

- `rpa_click_text` (UIA path: InvokePattern.Invoke() direto; OCR path: region_changed é só informativo)
- `rpa_type_text` (UIA clear=True: set_focused_value direto)
- `rpa_scroll` (UIA path: ScrollPattern direto)
- `rpa_navigate_menu`
- `rpa_fill_form`
- `rpa_find_in_table`

## 🟢 Únicas tools com verify REAL que dispara retry

- `rpa_wait_for_image` → `vision.locate()` pós-ação
- `rpa_wait_for_text` → OCR pós-ação

## 🟡 Caso especial

- `rpa_select_combobox` → verify próprio (`_option_shown`) fora do act()

---

# PARTE 3: POPUP DETECTION — Como funciona e onde falta

## O que EXISTE (portable.py: `blocking_popup`)

3 estratégias em cascata:

### Estratégia A: `IsWindowEnabled` + `GW_ENABLEDPOPUP` (Windows)
```
Loop até 8 níveis:
  IsWindowEnabled(hwnd)? → False: GetWindow(hwnd, GW_ENABLEDPOPUP=6) → sobe a cadeia
  True: BREAK (janela habilitada = sem popup bloqueante)
```

### Estratégia B: Fallback `GW_OWNER` (MT5 e diálogos que NÃO desabilitam a dona)
Varre todas as janelas: `GetWindow(candidate, GW_OWNER=4) == target_hwnd` + `IsWindowVisible` + overlap geométrico.

### Estratégia C: Montagem manual
Se o HWND do popup não está em `list_windows()`, monta dict via `GetWindowTextW` + `GetWindowRect`.

## Onde a detecção é CHAMADA

✅ `target_window()` → chama `blocking_popup(win)` e redireciona o palco automaticamente
✅ `server.py:_perceive()` → linha ~588: re-consulta `blocking_popup` e emite aviso ao agente

## ❌ Onde NÃO é chamada (deveria ser)

- `type_text()` → após `_type_unicode_win()` retornar
- `type_text()` → após `self._pg.typewrite(...)`
- `press_key()` → após `press()` ou `hotkey()`

## Pontos EXATOS de inserção (portable.py)

```python
# type_text — após caminho Unicode:
if self._type_unicode_win(text):
    time.sleep(0.2)
    return self.blocking_popup(self._focused_win)

# type_text — após caminho ASCII:
self._pg.typewrite(text, interval=0.02)
time.sleep(0.2)
return self.blocking_popup(self._focused_win)

# press_key — após ambos os caminhos:
if len(keys) == 1:
    self._pg.press(keys[0])
else:
    self._pg.hotkey(*keys)
time.sleep(0.2)
return self.blocking_popup(self._focused_win)
```

---

# PARTE 4: FLUXO IDEAL vs ATUAL

## Fluxo IDEAL (como deveria ser)

```
1. target_window("App")
2. Ação (ex.: type_text("valor"))
     ├─ before screenshot
     ├─ do_action
     ├─ after screenshot
     ├─ verify REAL (OCR, region_changed, UIA re-check)
     ├─ POPUP CHECK → se surgiu popup: redireciona, trata, retoma
     └─ retry com backoff se verify falhou
3. _perceive() confirma resultado
```

## Fluxo ATUAL (type_text/press_key)

```
1. type_text("1.2345")
     ├─ act() é chamado com verify = lambda: True
     ├─ before/after screenshots são TIRADOS mas NUNCA USADOS como gate
     ├─ retorna "sucesso" mesmo se NADA mudou
     └─ NENHUMA verificação de popup
```

## Fluxo ATUAL (click_text)

```
1. click_text("Buy")
     ├─ UIA → InvokePattern.Invoke() DIRETO (sem act, sem retry, sem verify)
     ├─ OCR → region_changed INFORMATIVO (não é gate de sucesso)
     └─ NENHUMA verificação de popup
```

---

# PRIORIDADES DE IMPLEMENTAÇÃO

| Prio | O quê | Onde | Esforço |
|------|-------|------|---------|
| 🔴 P0 | `scroll`: usar o diff já calculado | engine.py ~749 | 1 linha |
| 🔴 P0 | `type_text`: OCR pós-digitação (`vision.find_text`) | engine.py ~703 | ~15 linhas |
| 🔴 P0 | `type_text`/`press_key`: popup check pós-ação | portable.py type_text/press_key | ~6 linhas |
| 🟠 P1 | `press_key`: `region_changed(before, after)` | engine.py ~716 | ~5 linhas |
| 🟠 P1 | `drag`: `region_changed` no bounding box | engine.py ~761 | ~10 linhas |
| 🟡 P2 | `click_at`: usar `before_color` já capturado | engine.py ~435 | ~3 linhas |
| 🟡 P2 | `click_image`: `region_changed` na região do template | engine.py ~460 | ~10 linhas |
| 🟡 P2 | `click_text` UIA path: passar por `act()` com verify | engine.py ~490 | ~30 linhas |
