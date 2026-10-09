"""Tests de la lógica que no depende de GPU, audio real ni Ollama."""
import queue
import sys
import time
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asistente_llamadas as A  # noqa: E402


@pytest.mark.parametrize("texto,esperado", [
    ("¿Cuál es el presupuesto del FIUT?", True),
    ("Cuánto llevamos ejecutado del proyecto", True),
    ("Me podrías contar cómo van los indicadores", True),
    ("Explícanos el avance de la entrega", True),
    ("Bueno, seguimos con el siguiente punto.", False),
    ("Ok", False),
    ("Perfecto, gracias a todos.", False),
])
def test_es_pregunta(texto, esperado):
    assert A.es_pregunta(texto) is esperado


@pytest.mark.parametrize("texto", ["Gracias por ver el video", "Subtítulos realizados por la comunidad de Amara.org", "."])
def test_alucinaciones(texto):
    assert A.es_alucinacion(texto)


def test_no_filtra_texto_normal():
    assert not A.es_alucinacion("El informe se entrega el viernes")


def test_trocear_respeta_tamano():
    texto = "\n".join(f"Línea número {i} con algo de contenido de relleno." for i in range(200))
    trozos = A.trocear(texto, tam=300, solape=50)
    assert len(trozos) > 5
    assert all(len(t) <= 300 + 60 for t in trozos)


def test_trocear_linea_larga():
    trozos = A.trocear("palabra " * 500, tam=200, solape=20)
    assert len(trozos) > 10


@pytest.fixture
def kb(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Presupuesto"
    ws.append(["Ítem", "Monto", "Ejecutado"])
    ws.append(["Personal", "120000000", "85%"])
    ws.append(["Equipamiento", "45000000", "40%"])
    wb.save(tmp_path / "fiut.xlsx")
    (tmp_path / "notas.md").write_text(
        "La entrega al ministerio vence el 30 de octubre. Responsable: unidad de planificación.",
        encoding="utf-8")
    (tmp_path / "datos.csv").write_text("indicador;valor\nmatriculados;1520\n", encoding="utf-8")
    b = A.BaseConocimiento(tmp_path)
    assert b.cargar() >= 3
    return b


def test_busqueda_excel(kb):
    r = kb.buscar("¿cuánto se ha ejecutado en equipamiento?")
    assert r and r[0][0] == "fiut.xlsx" and "Equipamiento" in r[0][1]


def test_busqueda_markdown(kb):
    r = kb.buscar("cuándo vence la entrega al ministerio")
    assert r and r[0][0] == "notas.md"


def test_busqueda_csv(kb):
    r = kb.buscar("cuántos matriculados hay")
    assert r and r[0][0] == "datos.csv" and "1520" in r[0][1]


def test_busqueda_sin_coincidencias(kb):
    assert kb.buscar("receta de pan amasado") == []


class _AppFalsa:
    def __init__(self):
        self.audio_q, self.frase_q, self.pausado = queue.Queue(), queue.Queue(), False


def _voz(seg, rng, sr=16000):
    t = np.arange(int(sr * seg)) / sr
    return (0.2 * np.sin(2 * np.pi * 180 * t) * (1 + np.sin(2 * np.pi * 4 * t))
            + 0.1 * rng.standard_normal(len(t))).astype(np.float32)


def test_segmentador_separa_frases():
    pytest.importorskip("webrtcvad")
    app = _AppFalsa()
    A.Segmentador(app).start()
    rng = np.random.default_rng(0)
    sil = lambda s: np.zeros(int(16000 * s), np.float32)  # noqa: E731
    senal = np.concatenate([sil(0.5), _voz(1.5, rng), sil(1.0), _voz(2.0, rng)])
    for i in range(0, len(senal), 480):
        app.audio_q.put(senal[i:i + 480])
    time.sleep(1.5)  # la última frase se cierra porque deja de llegar audio
    frases = []
    while not app.frase_q.empty():
        frases.append(len(app.frase_q.get()[0]) / 16000)
    assert len(frases) == 2
    assert 1.5 <= frases[0] <= 3.0 and 2.0 <= frases[1] <= 3.5


def test_segmentador_ignora_ruidos_cortos():
    pytest.importorskip("webrtcvad")
    app = _AppFalsa()
    A.Segmentador(app).start()
    rng = np.random.default_rng(1)
    senal = np.concatenate([np.zeros(8000, np.float32), _voz(0.2, rng), np.zeros(16000, np.float32)])
    for i in range(0, len(senal), 480):
        app.audio_q.put(senal[i:i + 480])
    time.sleep(1.0)
    assert app.frase_q.empty()


@pytest.mark.parametrize("texto", [
    "MLP, SVM, Random Forest, TF-IDF, ERPNext.",
    "OPEX CAPEX accuracy EPP",
    "gracias gracias gracias gracias gracias gracias",
])
def test_filtra_repeticion_de_palabras_clave(texto):
    assert A.es_alucinacion(texto)


def test_no_filtra_pregunta_con_siglas():
    assert not A.es_alucinacion("¿Por qué eligieron el MLP en vez del SVM para el sistema?")
