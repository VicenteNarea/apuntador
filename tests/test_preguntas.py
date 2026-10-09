"""Varias preguntas a la vez: separación, unión de preguntas seguidas y armado del prompt."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asistente_llamadas as A  # noqa: E402


@pytest.mark.parametrize("texto, esperado", [
    ("¿Cuál es el OPEX? ¿Y por qué eligieron el MLP?", ["¿Cuál es el OPEX?", "¿Y por qué eligieron el MLP?"]),
    ("Cuál fue el accuracy? Y cuánto tardó el entrenamiento?",
     ["Cuál fue el accuracy?", "Y cuánto tardó el entrenamiento?"]),
    ("¿Cuál es el OPEX?", ["¿Cuál es el OPEX?"]),
    # una sola pregunta con contexto previo: se conserva el texto completo
    ("Sobre el presupuesto. ¿Cuánto cuesta el servidor?", ["Sobre el presupuesto. ¿Cuánto cuesta el servidor?"]),
    # el punto decimal no corta la frase
    ("El valor es 1.5 millones, ¿y el CAPEX? ¿Qué pasa si se cae ERPNext?",
     ["El valor es 1.5 millones, ¿y el CAPEX?", "¿Qué pasa si se cae ERPNext?"]),
])
def test_dividir_preguntas(texto, esperado):
    assert A.dividir_preguntas(texto) == esperado


@pytest.fixture
def app(monkeypatch):
    a = A.App(capturar=False)
    a.pedidos = []

    def solicitar(preguntas, historial, t_ref):
        a.pedidos.append(list(preguntas))
        a.respondedor.ocupado = True
    monkeypatch.setattr(a.respondedor, "solicitar", solicitar)
    return a


def test_preguntas_seguidas_se_suman(app, monkeypatch):
    reloj = [100.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: reloj[0])
    app.encolar_pregunta("¿Cuál es el OPEX?")
    reloj[0] += 2  # llega mientras se responde la anterior
    app.encolar_pregunta("¿Y el CAPEX? ¿Cuándo se paga?")
    assert app.pedidos[-1] == ["¿Cuál es el OPEX?", "¿Y el CAPEX?", "¿Cuándo se paga?"]

    # mucho después y con la respuesta terminada: empieza de cero
    app.respondedor.ocupado = False
    reloj[0] += 60
    app.encolar_pregunta("¿Quién lidera el proyecto?")
    assert app.pedidos[-1] == ["¿Quién lidera el proyecto?"]


def test_respuesta_en_curso_suma_aunque_pase_la_ventana(app, monkeypatch):
    reloj = [0.0]
    monkeypatch.setattr(A.time, "monotonic", lambda: reloj[0])
    app.encolar_pregunta("¿Cuál es el OPEX?")
    reloj[0] += A.VENTANA_PREGUNTAS_S + 5
    app.encolar_pregunta("¿Y el CAPEX?")  # la respuesta anterior sigue generándose
    assert app.pedidos[-1] == ["¿Cuál es el OPEX?", "¿Y el CAPEX?"]


def test_maximo_y_sin_repetidas(app):
    for i in range(6):
        app.encolar_pregunta(f"¿Pregunta número {i}?")
    app.encolar_pregunta("¿Pregunta número 5?")
    assert len(app.pedidos[-1]) == A.MAX_PREGUNTAS
    assert app.pedidos[-1][-1] == "¿Pregunta número 5?"


def test_prompt_con_varias_preguntas(monkeypatch):
    a = A.App(capturar=False)
    monkeypatch.setattr(a.kb, "buscar", lambda q, k=4: [(f"doc_{q[:6]}.pdf", "texto")])
    mensajes, fuentes = a.respondedor._mensajes(["¿Cuál es el OPEX?", "¿Y el CAPEX?"], [])
    user = mensajes[1]["content"]
    assert "ME HICIERON 2 PREGUNTAS" in user and "1) ¿Cuál es el OPEX?" in user and "2) ¿Y el CAPEX?" in user
    assert len(fuentes) == 2  # buscó documentos para cada pregunta
    assert "VARIAS preguntas" in mensajes[0]["content"]

    mensajes, _ = a.respondedor._mensajes(["¿Cuál es el OPEX?"], [])
    assert "PREGUNTA QUE ME HICIERON:\n¿Cuál es el OPEX?" in mensajes[1]["content"]
