"""Prueba de punta a punta sin llamada: reproduce un WAV en tiempo real dentro del pipeline
(segmentación → Whisper → detección de preguntas → Ollama) e imprime transcripción, respuestas y latencias.

Uso:  .venv\\Scripts\\python.exe herramientas\\simular.py ruta\\audio.wav [--rapido]
      --rapido  entrega el audio sin esperar el tiempo real (mide solo procesamiento)

Para generar un WAV de prueba: graba con la Grabadora de Windows o exporta el audio de un video
(cualquier WAV PCM de 16 bits, mono o estéreo, de cualquier frecuencia de muestreo).
"""
import queue
import sys
import time
import wave
from math import gcd
from pathlib import Path

import numpy as np
from scipy.signal import resample_poly

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asistente_llamadas as A  # noqa: E402


def leer_wav(ruta: str) -> np.ndarray:
    with wave.open(ruta, "rb") as w:
        if w.getsampwidth() != 2:
            raise SystemExit("El WAV debe ser PCM de 16 bits.")
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != 16000:
        g = gcd(16000, sr)
        x = resample_poly(x, 16000 // g, sr // g).astype(np.float32)
    return x


def main():
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    rapido = "--rapido" in sys.argv
    audio = leer_wav(sys.argv[1])
    print(f"Audio: {len(audio) / 16000:.1f} s")

    app = A.App(capturar=False)
    app.iniciar(hotkeys=False)
    while not app.listo.wait(0.5):
        _volcar(app)
    _volcar(app)

    bloque = 480
    t0 = time.perf_counter()
    for i in range(0, len(audio), bloque):
        app.audio_q.put(audio[i:i + bloque])
        if not rapido:
            espera = t0 + (i + bloque) / 16000 - time.perf_counter()
            if espera > 0:
                time.sleep(espera)
        _volcar(app)

    fin = time.perf_counter() + 15  # deja terminar la última frase y respuesta
    while time.perf_counter() < fin:
        _volcar(app)
        time.sleep(0.05)
    print("\n— fin de la simulación —")


_lat = {}


def _volcar(app):
    try:
        while True:
            ev = app.ui_q.get_nowait()
            t = ev[0]
            if t == "estado":
                print(f"[estado] {ev[1]}")
            elif t == "trans":
                print(f"\n{'❓' if ev[2] else '  '} {ev[1]}")
            elif t == "lat_stt":
                print(f"   (STT {ev[1]:.0f} ms)")
            elif t == "resp_inicio":
                print(f"   ↳ respondiendo… fuentes: {', '.join(ev[2]) or '—'}\n   ", end="")
            elif t == "tok":
                print(ev[1], end="", flush=True)
            elif t == "lat_llm":
                _lat["llm"] = ev[1]
            elif t == "resp_fin":
                print(f"\n   (1ª palabra a {_lat.get('llm', 0) / 1000:.2f} s del fin de la frase)")
    except queue.Empty:
        pass


if __name__ == "__main__":
    main()
