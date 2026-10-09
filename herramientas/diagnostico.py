"""Diagnóstico del entorno: Python, dependencias, CUDA/Whisper, Ollama, VRAM y audio loopback.

Uso:  .venv\\Scripts\\python.exe herramientas\\diagnostico.py [--audio]
"""
import importlib
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import asistente_llamadas as A  # noqa: E402

RES = []


class Aviso(Exception):
    """No es una falla: el equipo funciona, pero con una limitación (p. ej., sin NVIDIA)."""


def check(nombre, fn):
    t0 = time.perf_counter()
    try:
        detalle = fn() or ""
        RES.append(("OK", nombre, detalle, time.perf_counter() - t0))
    except Aviso as e:
        RES.append(("AVISO", nombre, str(e), time.perf_counter() - t0))
    except Exception as e:
        RES.append(("FALLA", nombre, f"{type(e).__name__}: {e}", time.perf_counter() - t0))


def python_ver():
    v = sys.version_info
    if (v.major, v.minor) != (3, 11):
        raise RuntimeError(f"Python {v.major}.{v.minor} (se recomienda 3.11)")
    return sys.version.split()[0]


def dependencias():
    mods = ["faster_whisper", "pyaudiowpatch", "webrtcvad", "numpy", "scipy", "requests",
            "rank_bm25", "pypdf", "docx", "openpyxl", "keyboard"]
    faltan = []
    for m in mods:
        try:
            importlib.import_module(m)
        except Exception:
            faltan.append(m)
    if faltan:
        raise RuntimeError("faltan: " + ", ".join(faltan))
    return f"{len(mods)} módulos"


def vram():
    if not A.hay_gpu_nvidia():
        raise Aviso("sin tarjeta NVIDIA (AMD/Intel): Whisper usa el procesador; Ollama usa su propio soporte")
    out = subprocess.check_output(
        ["nvidia-smi", "--query-gpu=name,memory.used,memory.total,driver_version",
         "--format=csv,noheader"], text=True).strip()
    return out


def whisper_cpu():
    import numpy as np
    from faster_whisper import WhisperModel
    t0 = time.perf_counter()
    m = WhisperModel(A.WHISPER_MODEL_CPU, device="cpu", compute_type="int8", cpu_threads=A.hilos_cpu())
    carga = time.perf_counter() - t0
    audio = np.zeros(16000 * 3, dtype=np.float32)
    list(m.transcribe(audio, language=A.IDIOMA)[0])
    t1 = time.perf_counter()
    list(m.transcribe(audio, language=A.IDIOMA, beam_size=1)[0])
    ms = (time.perf_counter() - t1) * 1000
    return (f"{A.WHISPER_MODEL_CPU} en CPU ({A.hilos_cpu()} hilos) · carga {carga:.1f} s · "
            f"3 s de audio en {ms:.0f} ms")


def whisper_gpu():
    import numpy as np
    if not A.whisper_en_gpu():
        return whisper_cpu()
    A._registrar_dlls_cuda()
    from faster_whisper import WhisperModel
    t0 = time.perf_counter()
    m = WhisperModel(A.WHISPER_MODEL, device="cuda", compute_type=A.WHISPER_COMPUTE)
    carga = time.perf_counter() - t0
    audio = np.zeros(16000 * 3, dtype=np.float32)
    list(m.transcribe(audio, language=A.IDIOMA)[0])
    t1 = time.perf_counter()
    list(m.transcribe(audio, language=A.IDIOMA, beam_size=1)[0])
    return f"{A.WHISPER_MODEL} en GPU · carga {carga:.1f} s · 3 s de audio en {(time.perf_counter() - t1) * 1000:.0f} ms"


def ollama():
    import requests
    base = A.OLLAMA_URL.rsplit("/api/", 1)[0]
    tags = requests.get(f"{base}/api/tags", timeout=3).json()
    nombres = [m["name"] for m in tags.get("models", [])]
    if not any(n.startswith(A.LLM_MODEL) for n in nombres):
        raise RuntimeError(f"modelo {A.LLM_MODEL} no descargado (hay: {', '.join(nombres) or 'ninguno'}). "
                           f"Ejecuta: ollama pull {A.LLM_MODEL}")
    payload = {"model": A.LLM_MODEL, "stream": True, "keep_alive": "60m",
               "messages": [{"role": "user", "content": "¿Cuál es la capital de Chile? Responde en 3 palabras."}],
               "options": {"num_ctx": A.LLM_NUM_CTX, "num_predict": 20}}
    requests.post(A.OLLAMA_URL, json={**payload, "stream": False}, timeout=180)  # calienta
    t0 = time.perf_counter()
    primera, texto = None, ""
    with requests.post(A.OLLAMA_URL, json=payload, stream=True, timeout=60) as r:
        for linea in r.iter_lines():
            if not linea:
                continue
            d = json.loads(linea)
            tok = d.get("message", {}).get("content", "")
            if tok and primera is None:
                primera = time.perf_counter() - t0
            texto += tok
            if d.get("done"):
                break
    return f"{A.LLM_MODEL} · 1ª palabra {primera * 1000:.0f} ms · «{texto.strip()[:40]}»"


def documentos():
    kb = A.BaseConocimiento(A.DOCS_DIR)
    n = kb.cargar()
    archivos = [p.name for p in A.DOCS_DIR.rglob("*") if p.suffix.lower() in kb.EXT]
    return f"{len(archivos)} archivos · {n} fragmentos"


def audio_loopback():
    import numpy as np
    import queue
    q = queue.Queue()
    cap = A.CapturaAudio(q)
    print(f"   Capturando 3 s de «{cap.nombre}». Reproduce algo con voz ahora…")
    cap.iniciar()
    time.sleep(3)
    cap.detener()
    trozos = []
    while not q.empty():
        trozos.append(q.get())
    if not trozos:
        raise RuntimeError("no llegó audio: ¿está sonando algo por la salida predeterminada?")
    x = np.concatenate(trozos)
    rms = float(np.sqrt(np.mean(x ** 2)))
    if rms < 0.002:
        raise RuntimeError(f"audio casi en silencio (RMS {rms:.4f})")
    return f"{cap.nombre} · {cap.rate} Hz · {len(x) / 16000:.1f} s · RMS {rms:.3f}"


if __name__ == "__main__":
    check("Python", python_ver)
    check("Dependencias", dependencias)
    check("GPU / VRAM (nvidia-smi)", vram)
    check("Whisper (GPU o CPU)", whisper_gpu)
    check("Ollama", ollama)
    check("Documentos (docs/)", documentos)
    if "--audio" in sys.argv:
        check("Audio loopback", audio_loopback)
    check("VRAM con modelos cargados", vram)

    ancho = max(len(r[1]) for r in RES)
    print()
    for estado, nombre, detalle, t in RES:
        print(f"[{estado:5}] {nombre:<{ancho}}  {detalle}  ({t:.1f} s)")
    fallas = sum(r[0] == "FALLA" for r in RES)
    avisos = sum(r[0] == "AVISO" for r in RES)
    print(f"\n{len(RES) - fallas - avisos}/{len(RES)} OK" + (f" · {avisos} aviso(s)" if avisos else ""))
    sys.exit(1 if fallas else 0)
