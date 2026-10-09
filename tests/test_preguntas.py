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


# ─────────────── alucinaciones de Whisper (casos reales de una llamada) ───────────────
@pytest.mark.parametrize("texto", [
    "x slot, X, SE, G across, x slot",
    "FIX PLF, видео, interference, etc.",
    "Q, Q, Q, Q, Q",
    "字幕由 Amara.org 社区提供",
])
def test_alucinaciones_reales(texto):
    assert A.es_alucinacion(texto)


@pytest.mark.parametrize("texto", [
    "su MLP sacó 90.24% y SVM 85.36% con 41 ejemplos",
    "Eso es 37 aciertos contra 35",
    "es mejor",
    "y no que fue suerte.",
    "Sí, ya lo vi, ok",
    "Lo vimos en la U el 3 de mayo, a las 10",
    "We use an API to do it in the PC",
])
def test_frases_normales_no_son_alucinacion(texto):
    assert not A.es_alucinacion(texto)


def test_segmento_valido():
    from types import SimpleNamespace as S
    assert A.segmento_valido(S(no_speech_prob=0.1, avg_logprob=-0.3, compression_ratio=1.4))
    assert not A.segmento_valido(S(no_speech_prob=0.1, avg_logprob=-1.4, compression_ratio=1.4))  # inseguro
    assert not A.segmento_valido(S(no_speech_prob=0.1, avg_logprob=-0.3, compression_ratio=3.0))  # repetitivo
    assert not A.segmento_valido(S(no_speech_prob=0.9, avg_logprob=-0.3, compression_ratio=1.4))  # sin voz


# ─────────────── pregunta cortada en varios pedazos por pausas ───────────────
def _frase(app, texto, t_fin, dur):
    app.on_texto(texto, t_fin, 100, dur)


def test_pregunta_cortada_se_completa(app):
    # caso de la llamada: la persona hace pausas cortas en medio de la pregunta
    _frase(app, "Eso es 37 aciertos contra 35", 10.0, 2.5)
    assert app.pedidos == []
    _frase(app, "con tan pocos datos. ¿Cómo puede afirmar que MLP", 14.0, 3.0)
    _frase(app, "es mejor", 15.6, 1.4)          # pausa ~1,1 s
    _frase(app, "y no que fue suerte.", 17.4, 1.6)  # pausa ~1,1 s
    assert app.pedidos[-1] == ["con tan pocos datos. ¿Cómo puede afirmar que MLP es mejor y no que fue suerte."]
    assert len(app.pedidos) == 3  # se volvió a pedir con cada pedazo


def test_pausa_larga_no_continua(app):
    _frase(app, "¿Cuál es el OPEX?", 10.0, 2.0)
    _frase(app, "Bueno, pasemos al siguiente punto", 20.0, 2.5)  # pausa de varios segundos
    assert app.pedidos == [["¿Cuál es el OPEX?"]]


def test_sin_auto_no_continua(app):
    app.auto = False
    _frase(app, "¿Cómo puede afirmar que MLP", 10.0, 2.0)
    _frase(app, "es mejor", 11.5, 1.2)
    assert app.pedidos == []


# ─────────────── respuestas sin Markdown ───────────────
@pytest.mark.parametrize("pedazos", [
    ["- ", "**", "MLP", "**", ": red neuronal"],
    ["- *", "*MLP*", "*: red neuronal"],          # el '**' llega partido
    ["- **MLP**: red neuronal"],
])
def test_limpia_markdown(pedazos):
    limpiar = A.LimpiaMarkdown()
    assert "".join(limpiar(p) for p in pedazos) == "- MLP: red neuronal"


def test_vineta_con_asterisco_se_conserva():
    limpiar = A.LimpiaMarkdown()
    assert "".join(limpiar(p) for p in ["*", " uno"]) == "* uno"


def test_instrucciones_no_son_plantilla():
    # el modelo local copiaba "1) <tema>" con viñetas vacías: la regla no debe traer marcadores para rellenar
    assert "<tema" not in A.SISTEMA and "esquema" in A.SISTEMA and "**" in A.SISTEMA
