"""Grafo de conhecimento da UI — cache PERSISTENTE, alimentado ON-THE-FLY.

Ideia: em vez de o agente re-descobrir cada tela e adivinhar caminhos, a PRÓPRIA
ferramenta mantém um grafo da aplicação que cresce conforme navega:
  - NÓ    = estado de tela (assinatura do conjunto de controles acionáveis) + os
            controles daquela tela (nome/tipo/ação).
  - ARESTA = transição rotulada por AÇÃO: (estado, ação) -> estado'.
Na 1ª vez o motor explora e ALIMENTA o grafo; na 2ª vez ele CONSULTA a rota
(path-find) e vai direto — rápido e sem chutar. Persiste em disco (cache de base).

Reusa os padrões provados do ContextGraph do Maestro (aluy-vau, TypeScript): upsert
idempotente, teto anti-runaway + eviction por frequência/recência, e travessia
cycle-safe (visited). DIFERE em: arestas rotuladas por AÇÃO (não containment) e
PERSISTÊNCIA (o ContextGraph é em memória). Processos/linguagens distintos (lá TS
no agente; aqui Python no MCP server) → reusa-se o MODELO, não o código.

Best-effort: nunca levanta — falha de I/O degrada para grafo vazio/sem cache.
"""
import os
import re
import sys
import json
import time
import hashlib
from collections import deque

DEFAULT_MAX_STATES = 600


def _norm_label(label: str) -> str:
    """Normaliza o título da janela para a IDENTIDADE ESTÁVEL da tela: tira o
    sufixo do gráfico ativo do MT5 (' - EURUSD,H1'), mascara números (conta/preço)
    e fica só com letras. Assim a janela principal é UM nó, não um por gráfico/
    cotação (o conjunto volátil de controles — abas, colunas — fragmentava demais)."""
    s = re.sub(r"\s*-\s*[A-Za-z0-9]+,[A-Za-z0-9]+\s*$", "", (label or "").strip())
    s = re.sub(r"\d+", "", s.lower())
    return re.sub(r"\s+", " ", re.sub(r"[^a-z]+", " ", s)).strip()[:80]


def _signature(label: str, controls) -> str:
    """Assinatura do estado. Primário: título normalizado (identidade estável da
    janela). Sem título: cai no hash do conjunto de controles acionáveis."""
    base = _norm_label(label)
    if not base:
        keys = sorted({f"{(c.get('name') or '').strip()}|{c.get('type','')}"
                       for c in controls if (c.get('name') or '').strip()})
        base = "ctrls:" + "\n".join(keys)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()[:16]


def _norm_controls(controls):
    out = []
    for c in controls:
        nm = (c.get("name") or "").strip()
        if not nm:
            continue
        out.append({"name": nm, "type": c.get("type", ""),
                    "patterns": list(c.get("patterns", []))})
    return out


class UiGraph:
    def __init__(self, path: str = None, *, max_states: int = DEFAULT_MAX_STATES):
        self.path = path or os.path.join(os.path.expanduser("~/.aluy"), "rpa_ui_graph.json")
        self.max_states = max_states
        self.states = {}   # sid -> {id,label,controls:[...],created,seen,count}
        self.edges = {}    # sid -> { akey -> {action:{kind,target}, to, count, seen} }
        self._dirty = False
        self._load()

    # ── alimentação (aprende dirigindo) ──────────────────────
    def observe(self, label: str, controls) -> str:
        """Registra/atualiza o estado atual (idempotente) e devolve seu id."""
        ctrls = _norm_controls(controls)
        if not ctrls:
            return None
        sid = _signature(label, ctrls)
        now = time.time()
        st = self.states.get(sid)
        if st:
            st["seen"] = now
            st["count"] += 1
            if label:
                st["label"] = label
            st["controls"] = ctrls  # UI pode ter mudado rótulos; mantém o atual
        else:
            if len(self.states) >= self.max_states:
                self._evict_one()
            self.states[sid] = {"id": sid, "label": label or "", "created": now,
                                "seen": now, "count": 1, "controls": ctrls,
                                "menus": {}, "analysis": ""}
        self.states[sid].setdefault("menus", {})
        self.states[sid].setdefault("analysis", "")
        self._dirty = True
        return sid

    def set_analysis(self, sid: str, text: str):
        """Aprendizado ATIVO: guarda a ANÁLISE PROFUNDA da tela feita pelo agente
        (o que é a tela, affordances, comportamentos possíveis) — o object-
        repository fica rico, não só a lista de controles. Persiste no cache."""
        st = self.states.get(sid)
        if st and text:
            st["analysis"] = text.strip()[:1500]
            self._dirty = True

    def is_analyzed(self, sid: str) -> bool:
        st = self.states.get(sid)
        return bool(st and st.get("analysis"))

    def add_menu(self, sid: str, menu_name: str, items):
        """Object-repository: guarda a 'visão geral' de um MENU descoberto (itens
        capturados por OCR quando o menu abre — menus são custom-desenhados, fora
        da árvore UIA). Acumula/dedupa entre observações. Assim a tela 'conhece'
        seu menu sem precisar reabrir."""
        st = self.states.get(sid)
        if not st or not menu_name:
            return
        cur = st.setdefault("menus", {}).get(menu_name, [])
        seen = {i.lower() for i in cur}
        for it in items:
            it = (it or "").strip()
            if it and it.lower() not in seen and len(it) <= 50:
                cur.append(it)
                seen.add(it.lower())
        st["menus"][menu_name] = cur[:40]   # teto por menu
        self._dirty = True

    def screen_map(self, label_or_sid: str) -> dict:
        """Mapa cacheado da tela (object-repository): controles + menus conhecidos
        + transições conhecidas. É o 'algo para ajudar' o agente — sem re-explorar."""
        sid = label_or_sid
        if sid not in self.states:
            sid = _signature(label_or_sid, [])  # tenta por título normalizado
        st = self.states.get(sid)
        if not st:
            return {}
        by_type = {}
        for c in st["controls"]:
            by_type.setdefault(c["type"].replace("Control", ""), []).append(c["name"])
        return {
            "label": st["label"],
            "analysis": st.get("analysis", ""),
            "controls_by_type": {k: v[:30] for k, v in by_type.items()},
            "menus": st.get("menus", {}),
            "transitions": [{"action": e["action"], "to_label": self.states.get(e["to"], {}).get("label", "")}
                            for e in self.edges.get(sid, {}).values()],
        }

    def record_transition(self, from_sid: str, action: dict, to_sid: str):
        """Grava aresta (from, ação) -> to. action = {kind, target}."""
        if not from_sid or not to_sid or from_sid == to_sid:
            return
        akey = f"{action.get('kind')}:{action.get('target')}"
        d = self.edges.setdefault(from_sid, {})
        now = time.time()
        e = d.get(akey)
        if e:
            e["count"] += 1
            e["seen"] = now
            e["to"] = to_sid
        else:
            d[akey] = {"action": action, "to": to_sid, "count": 1, "seen": now}
        self._dirty = True

    # ── consulta / path-find ─────────────────────────────────
    def states_with_control(self, name: str):
        nl = name.strip().lower()
        out = []
        for sid, st in self.states.items():
            for c in st["controls"]:
                cn = (c.get("name") or "").lower()
                if cn == nl or nl in cn:
                    out.append(sid)
                    break
        return out

    def path_to(self, from_sid: str, target_control: str, *, max_depth: int = 10):
        """BFS de `from_sid` até um estado que CONTÉM `target_control`. Devolve a
        lista de PASSOS [{action:{kind,target}, to_label}] do caminho (to_label =
        título da janela resultante, p/ re-mirar), ou None se desconhecido. Lista
        vazia = já estamos lá. Cycle-safe via `seen` (UI pode ter ciclos A->B->A)."""
        targets = set(self.states_with_control(target_control))
        if not targets:
            return None
        if from_sid in targets:
            return []  # já estamos num estado que tem o controle
        q = deque([(from_sid, [])])
        seen = {from_sid}
        while q:
            sid, path = q.popleft()
            if len(path) >= max_depth:
                continue
            for e in self.edges.get(sid, {}).values():
                to = e["to"]
                step = {"action": e["action"],
                        "to_label": (self.states.get(to, {}).get("label") or "")}
                npath = path + [step]
                if to in targets:
                    return npath
                if to not in seen:
                    seen.add(to)
                    q.append((to, npath))
        return None

    def transitions_from(self, sid: str):
        """Ações conhecidas a partir de um estado (p/ o agente saber 'o que dá pra
        fazer aqui' sem re-explorar)."""
        return [{"action": e["action"], "to": e["to"], "count": e["count"]}
                for e in self.edges.get(sid, {}).values()]

    # ── eviction (teto anti-runaway; frequência+recência) ────
    def _evict_one(self):
        if not self.states:
            return
        victim = min(self.states.values(), key=lambda s: (s["count"], s["seen"]))
        sid = victim["id"]
        self.states.pop(sid, None)
        self.edges.pop(sid, None)
        for d in self.edges.values():
            for k in [k for k, e in d.items() if e["to"] == sid]:
                d.pop(k, None)

    # ── persistência (o ponto: cache de base entre execuções) ─
    def save(self):
        if not self._dirty:
            return
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"v": 1, "states": self.states, "edges": self.edges},
                          f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception as e:
            print(f"[rpa] ui_graph save falhou: {e}", file=sys.stderr)

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path, encoding="utf-8") as f:
                    d = json.load(f)
                self.states = d.get("states", {})
                self.edges = d.get("edges", {})
        except Exception as e:
            print(f"[rpa] ui_graph load falhou: {e}", file=sys.stderr)

    def stats(self) -> dict:
        return {"states": len(self.states),
                "edges": sum(len(d) for d in self.edges.values()),
                "path": self.path}
