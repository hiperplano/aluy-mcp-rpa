"""Smoke test dos módulos PUROS — roda headless, sem display nem libs nativas.

Não testa clique/OCR (precisam de tela real); testa a LÓGICA verificável: o grafo
de UI (aprendizado + persistência), a degradação segura do UIA fora do Windows e a
detecção de capacidades. É o que a CI honesta pode afirmar.
"""
import sys

import src.uia as uia
from src.ui_graph import UiGraph
from src.capabilities import detect_capabilities, summary


def test_ui_graph_aprende_e_acha_rota(tmp_path):
    """observe() vira nó, record_transition() vira aresta, path_to() acha o caminho."""
    g = UiGraph(path=str(tmp_path / "g.json"))
    a = g.observe("Janela A", [{"name": "Abrir", "type": "ButtonControl", "patterns": ["invoke"]}])
    b = g.observe("Janela B", [{"name": "Confirmar", "type": "ButtonControl", "patterns": ["invoke"]}])
    assert a and b and a != b

    g.record_transition(a, {"kind": "click", "target": "Abrir"}, b)
    path = g.path_to(a, "Confirmar")
    assert path, "deveria achar a rota A->B"
    assert path[0]["action"]["target"] == "Abrir"
    assert g.path_to(a, "Inexistente") is None


def test_ui_graph_persiste_entre_instancias(tmp_path):
    """save() grava em disco; uma nova instância carrega o mesmo grafo (cache de base)."""
    p = str(tmp_path / "g.json")
    g = UiGraph(path=p)
    a = g.observe("Tela X", [{"name": "Ok", "type": "ButtonControl", "patterns": ["invoke"]}])
    b = g.observe("Tela Y", [{"name": "Salvar", "type": "ButtonControl", "patterns": ["invoke"]}])
    g.record_transition(a, {"kind": "click", "target": "Ok"}, b)
    g.save()

    g2 = UiGraph(path=p)
    assert g2.stats()["states"] >= 2
    assert g2.path_to(a, "Salvar"), "rota deveria sobreviver ao reload"


def test_uia_degrada_seguro_fora_do_windows():
    """Sem `uiautomation` (Linux CI), tudo é best-effort: None/False/[], nunca levanta."""
    if sys.platform != "win32":
        assert uia.available() is False
    # Invariante cross-OS: quando indisponível, as funções não quebram.
    if not uia.available():
        assert uia.dump("qualquer") == []
        assert uia.find("qualquer", "x") is None
        assert uia.scroll("qualquer", 3) is False
        assert uia.set_focused_value("x") is False


def test_capabilities_tem_chaves_e_e_serializavel():
    """detect_capabilities() devolve o dict esperado e summary() formata sem erro."""
    caps = detect_capabilities()
    for k in ("os", "gpu", "uia", "a11y", "ocr_canvas", "keyboard"):
        assert k in caps
    if sys.platform != "win32":
        assert caps["uia"] is False
    assert isinstance(summary(caps), str)
