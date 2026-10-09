"""Panel web: difusión de eventos, acciones, SSE y sondeo (sin GPU, audio ni Ollama)."""
import json
import socket
import sys
import time
from pathlib import Path

import pytest
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from servidor_web import Difusor, ServidorWeb  # noqa: E402


def test_difusor_foto_y_cola():
    d = Difusor()
    d.put(("estado", "Escuchando"))
    d.put(("trans", "hola", False))
    d.put(("resp_inicio", "¿qué?", []))
    d.put(("tok", "a"))
    d.put(("tok2", "b"))
    d.put(("pausa", True))
    d.put(("estado", "Otra cosa"))  # el último estado debe quedar después de la pausa
    assert d.get_nowait() == ("estado", "Escuchando")  # la ventana sigue recibiendo todo

    q = d.suscribir()
    foto = []
    while not q.empty():
        foto.append(q.get_nowait())
    assert foto[0] == ("reset",)
    tipos = [e[0] for e in foto]
    assert tipos.index("pausa") < tipos.index("estado")
    assert ("trans", "hola", False) in foto and ("tok", "a") in foto and ("tok2", "b") in foto

    d.put(("limpiar",))
    q2 = d.suscribir()
    tipos2 = [q2.get_nowait()[0] for _ in range(q2.qsize())]
    assert "trans" not in tipos2 and "tok" not in tipos2
    assert q.get_nowait() == ("limpiar",)  # los suscritos reciben los eventos en vivo


class AppFalsa:
    def __init__(self):
        self.ui_q = Difusor()
        self.llamadas = []

    def pedir_respuesta(self):
        self.llamadas.append("responder")

    def alternar_pausa(self):
        self.llamadas.append("pausa")

    def limpiar(self):
        self.llamadas.append("limpiar")

    def recargar_docs(self):
        self.llamadas.append("recargar")

    def fijar_auto(self, v):
        self.llamadas.append(("auto", v))


@pytest.fixture
def servidor(tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    puerto = s.getsockname()[1]
    s.close()
    app = AppFalsa()
    web = ServidorWeb(app, puerto)
    web.iniciar()
    yield app, f"http://127.0.0.1:{puerto}"
    web.cerrar()


def test_pagina_y_acciones(servidor):
    app, url = servidor
    r = requests.get(url + "/")
    assert r.status_code == 200 and "SUGERENCIA · OPENAI" in r.text
    for a in ("responder", "pausa", "limpiar", "recargar"):
        assert requests.post(url + "/accion/" + a).status_code == 204
    assert requests.post(url + "/accion/auto?v=0").status_code == 204
    assert app.llamadas == ["responder", "pausa", "limpiar", "recargar", ("auto", False)]
    assert requests.post(url + "/accion/borrar_todo").status_code == 404


def test_sse_envia_foto_y_eventos(servidor):
    app, url = servidor
    app.ui_q.put(("trans", "¿Cuál es el OPEX?", True))
    with requests.get(url + "/eventos", stream=True, timeout=5) as r:
        assert r.headers["Content-Type"].startswith("text/event-stream")
        recibidos = []
        enviado = False
        t0 = time.time()
        for linea in r.iter_lines(chunk_size=1, decode_unicode=True):
            if linea and linea.startswith("data: "):
                recibidos.append(json.loads(linea[6:]))
            if not enviado and len(recibidos) >= 2:
                app.ui_q.put(("tok2", "respuesta nube"))
                enviado = True
            if ["tok2", "respuesta nube"] in recibidos or time.time() - t0 > 4:
                break
    assert recibidos[0] == ["reset"]
    assert ["trans", "¿Cuál es el OPEX?", True] in recibidos
    assert ["tok2", "respuesta nube"] in recibidos


def test_sondeo_largo(servidor):
    app, url = servidor
    app.ui_q.put(("trans", "hola", False))
    ses = requests.Session()
    d = ses.get(url + "/sondeo?desde=0", timeout=5).json()
    assert d["ev"][0] == ["reset"] and ["trans", "hola", False] in d["ev"]
    n = d["n"]
    import threading
    threading.Timer(0.3, lambda: app.ui_q.put(("tok", "x"))).start()
    t0 = time.time()
    d2 = ses.get(url + f"/sondeo?desde={n}", timeout=5).json()  # espera hasta que llega el token
    assert d2["ev"] == [["tok", "x"]] and d2["n"] == n + 1 and time.time() - t0 < 3
