"""
Apuntador de llamadas — 100% local
Windows 11 · NVIDIA RTX (CUDA) · faster-whisper + Ollama

Escucha el audio del sistema (lo que suena por tus parlantes/audífonos: la voz de
los demás en Meet/Teams/Zoom), lo transcribe en la GPU, detecta preguntas y te
muestra una respuesta sugerida en una ventana flotante, usando tus documentos.
"""
import os
import re
import sys
import csv
import json
import time
import queue
import threading
import unicodedata
from collections import deque
from datetime import datetime
from math import gcd
from pathlib import Path

import numpy as np

from servidor_web import Difusor, ServidorWeb

try:
    import tkinter as tk
except ImportError:  # permite importar el módulo en entornos sin GUI
    tk = None

# ═══════════════════════════ CONFIGURACIÓN ═══════════════════════════
BASE = Path(__file__).resolve().parent

# Transcripción (Whisper en GPU)
WHISPER_MODEL = "large-v3-turbo"   # más rápido: "small" · intermedio: "medium"
WHISPER_COMPUTE = "int8_float16"   # ~1,5 GB VRAM con large-v3-turbo
IDIOMA = "es"
# Términos propios mejoran la transcripción (siglas, nombres, lugares)
# Se usan como "hotwords" (sesgo suave), no como texto previo: así Whisper no las repite en los silencios.
PALABRAS_CLAVE = "MLP, SVM, Random Forest, TF-IDF, ERPNext, FastAPI, OPEX, CAPEX, accuracy, EPP"
# Filtros contra alucinaciones (texto inventado cuando llega ruido en vez de voz)
WHISPER_VAD = True                 # filtro de voz Silero de Whisper: descarta ruido, teclado, música
WHISPER_MIN_LOGPROB = -1.0         # descarta segmentos en que Whisper está muy inseguro
WHISPER_MAX_COMPRESION = 2.4       # descarta segmentos repetitivos ("Q, Q, Q, Q")

# LLM local (Ollama)
OLLAMA_URL = "http://127.0.0.1:11434/api/chat"  # no usar "localhost": en Windows intenta IPv6 primero y pierde ~2 s
LLM_MODEL = "qwen2.5:7b"           # si falta VRAM: "qwen2.5:3b"
LLM_NUM_CTX = 4096
LLM_MAX_TOKENS = 220
LLM_TEMPERATURA = 0.2

# LLM en la nube (OpenAI) — responde en paralelo al local
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODELO = "gpt-6-luna"
OPENAI_ESFUERZO = "none"           # reasoning_effort; None para no enviarlo

# Conocimiento propio
DOCS_DIR = BASE / "docs"           # PDF, DOCX, XLSX, CSV, TXT, MD
CONTEXTO_FILE = BASE / "contexto.txt"
ENLACES_FILE = BASE / "enlaces.txt"   # enlace del panel web (se reescribe al iniciar)
FRAGMENTOS_POR_PREGUNTA = 4

# Varias preguntas a la vez
MAX_PREGUNTAS = 4                  # máximo de preguntas que se responden juntas
VENTANA_PREGUNTAS_S = 8            # una pregunta que llega a menos de esto de la anterior se suma a ella
TOKENS_POR_PREGUNTA_EXTRA = 120    # tokens de respuesta adicionales por cada pregunta extra

# Detección de frases (latencia ↔ precisión)
SILENCIO_MS = 600                  # silencio que cierra una frase (bajar = más rápido, más cortes)
MAX_FRASE_S = 20
MIN_FRASE_S = 0.5
VAD_AGRESIVIDAD = 2                # 0–3
PREROLL_MS = 300
CONTINUACION_S = 1.5               # una pausa menor que esto tras una pregunta = la pregunta sigue
MAX_CONTINUACIONES = 3

# Comportamiento
RESPONDER_AUTOMATICO = True        # responde solo al detectar una pregunta
LINEAS_HISTORIAL = 12
GUARDAR_TRANSCRIPCION = False      # guarda en ./transcripciones/
HOTKEY_RESPONDER = "ctrl+alt+r"    # fuerza respuesta a lo último dicho
HOTKEY_PAUSA = "ctrl+alt+p"
OPACIDAD = 0.94

# Modo de ejecución (también se elige con --modo; los .bat ya lo pasan)
#   "normal"   → solo la ventana en el PC
#   "servicio" → solo el panel web (tablet/celular), sin ventana en el PC
#   "doble"    → ventana en el PC + panel web
MODO = "normal"
WEB_PUERTO = 8765                  # http://IP-del-PC:WEB_PUERTO en la misma red wifi
# ═════════════════════════════════════════════════════════════════════

SISTEMA = """Eres el apuntador silencioso de una persona que está en una reunión en vivo.
Te llega lo que le acaban de preguntar y debes darle lo que necesita para responder en voz alta, ya.

Reglas:
- Español. Máximo 3 viñetas cortas (≤ 20 palabras cada una). La primera es la respuesta directa.
- Si te llegan VARIAS preguntas, responde TODAS, en el mismo orden y en bloques separados:
  una línea "1) <tema en ≤ 5 palabras>" y debajo 1–2 viñetas. No omitas ninguna.
- Usa primero los DOCUMENTOS y el CONTEXTO PERSONAL; da cifras, fechas y nombres exactos cuando existan.
- Si la pregunta trae una premisa equivocada (p. ej., confunde qué opción se eligió), corrígela en la primera viñeta.
- Si los documentos no responden la pregunta, empieza con "⚠" y propone una respuesta prudente
  (p. ej., comprometerse a enviarlo después). Nunca inventes cifras.
- Sin saludos, sin introducciones, sin explicar lo que haces."""


# ─────────────────────────── utilidades de texto ───────────────────────────
def normalizar(t: str) -> str:
    t = unicodedata.normalize("NFD", t.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


_STOP = set(normalizar(
    "de la que el en y a los se del las un por con no una su para es al lo como mas o pero "
    "sus le ha me si sin sobre este ya entre cuando todo esta ser son dos tambien fue habia "
    "era muy hasta desde nos durante uno ni contra ese eso mi te tu yo qué cual cuál cómo "
    "hay esto estos estas esa esos esas porque entonces bueno"
).split())


def tokens(t: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", normalizar(t)) if len(w) > 1 and w not in _STOP]


_INTERROG = ["cual", "cuales", "cuanto", "cuanta", "cuantos", "cuantas", "cuando", "donde",
             "por que", "para que", "quien", "quienes", "como se", "como va", "como esta", "que pasa"]
_PEDIDOS = ["me puedes", "me podrias", "nos puedes", "nos podrias", "podrias explicar", "puedes explicar",
            "explicanos", "explicame", "cuentanos", "cuentame", "nos comentas", "me comentas",
            "sabes si", "sabes cuanto", "tienes el dato", "tienes idea", "alguna idea", "nos dices",
            "me dices", "que opinas", "que piensas"]


def es_pregunta(texto: str) -> bool:
    if "?" in texto or "¿" in texto:
        return True
    n = normalizar(texto).strip()
    if len(n.split()) < 3:
        return False
    return any(n.startswith(w + " ") for w in _INTERROG) or any(p in n for p in _PEDIDOS)


def dividir_preguntas(texto: str) -> list[str]:
    """Separa una frase con varias preguntas ("¿A? ¿Y B?") en preguntas sueltas.
    Si hay una sola (o ninguna clara), devuelve el texto completo para no perder contexto."""
    partes = [p.strip() for p in re.split(r"(?<=[?!])\s*|(?<=\.)\s+", texto) if p and p.strip()]
    preguntas = [p for p in partes if es_pregunta(p)]
    return preguntas if len(preguntas) > 1 else [texto.strip()]


_ALUCINACIONES = ("gracias por ver", "suscribete", "amara.org", "subtitulos realizados",
                  "subtitulos por", "gracias por su atencion")


_CLAVES = set(tokens(PALABRAS_CLAVE))


# Alfabetos que no deberían aparecer en una llamada en español/inglés (cirílico, griego, árabe, CJK…)
_OTRO_ALFABETO = re.compile(r"[\u0370-\u03ff\u0400-\u052f\u0590-\u06ff\u0900-\u0dff"
                            r"\u0e00-\u0e7f\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")
_CORTAS_OK = {"si", "ya", "ok", "eh", "ah", "oh", "va", "ve", "ha", "he", "vi", "fe", "id", "ia", "ti",
              "bi", "pc", "it", "is", "in", "on", "of", "to", "be", "we", "us", "an", "at", "or", "as"}


def es_alucinacion(texto: str) -> bool:
    n = normalizar(texto)
    if any(a in n for a in _ALUCINACIONES) or len(n.strip(" .,¡!")) < 2:
        return True
    if _OTRO_ALFABETO.search(texto):
        return True
    palabras = re.findall(r"[a-z0-9]+", n)
    # letras o palabras sueltas repetidas ("Q, Q, Q, Q") o sopa de siglas ("x slot, X, SE, G across")
    if len(palabras) >= 4:
        if len(set(palabras)) <= 2:
            return True
        raras = [w for w in palabras if len(w) <= 2 and not w.isdigit() and w not in _STOP and w not in _CORTAS_OK]
        if len(raras) / len(palabras) >= 0.4:
            return True
    t = tokens(texto)
    # Whisper repitiendo la lista de palabras clave en un silencio
    if t and _CLAVES and len(t) >= 3 and sum(w in _CLAVES for w in t) / len(t) >= 0.6:
        return True
    # frase con una misma palabra repetida en bucle ("gracias gracias gracias…")
    return len(t) >= 6 and len(set(t)) <= 2


def segmento_valido(s) -> bool:
    """Descarta segmentos de Whisper sin voz, de baja confianza o repetitivos."""
    return (s.no_speech_prob < 0.6 and s.avg_logprob > WHISPER_MIN_LOGPROB
            and s.compression_ratio < WHISPER_MAX_COMPRESION)


# ─────────────────────────── documentos (RAG BM25) ───────────────────────────
def _filas_a_texto(nombre: str, filas) -> list[str]:
    out, enc = [], None
    for fila in filas:
        vals = ["" if v is None else str(v).strip() for v in fila]
        if not any(vals):
            continue
        if enc is None:
            enc = vals
            continue
        pares = [f"{enc[i] if i < len(enc) and enc[i] else f'col{i + 1}'}={v}"
                 for i, v in enumerate(vals) if v]
        out.append(f"{nombre}: " + "; ".join(pares))
    return out


def leer_secciones(p: Path) -> list[tuple[str, str]]:
    """Devuelve [(título_de_sección, texto)] respetando la estructura del documento."""
    ext = p.suffix.lower()
    if ext in (".txt", ".md"):
        texto = p.read_text(encoding="utf-8", errors="ignore")
        if ext == ".txt":
            return [("", texto)]
        secciones, titulo, buf = [], "", []
        for linea in texto.splitlines():
            if re.match(r"^#{1,4}\s", linea):
                if any(l.strip() for l in buf):
                    secciones.append((titulo, "\n".join(buf)))
                titulo, buf = linea.lstrip("#").strip(), []
            else:
                buf.append(linea)
        if any(l.strip() for l in buf):
            secciones.append((titulo, "\n".join(buf)))
        return secciones
    if ext == ".pdf":
        from pypdf import PdfReader
        return [(f"Página {i}", pg.extract_text() or "") for i, pg in enumerate(PdfReader(str(p)).pages, 1)]
    if ext == ".docx":
        import docx
        d = docx.Document(str(p))
        secciones, titulo, buf = [], "", []
        for par in d.paragraphs:
            estilo = (par.style.name or "").lower() if par.style is not None else ""
            if par.text.strip() and (estilo.startswith("heading") or estilo.startswith("título") or estilo.startswith("titulo")):
                if any(l.strip() for l in buf):
                    secciones.append((titulo, "\n".join(buf)))
                titulo, buf = par.text.strip(), []
            else:
                buf.append(par.text)
        if any(l.strip() for l in buf):
            secciones.append((titulo, "\n".join(buf)))
        for i, tabla in enumerate(d.tables, 1):
            filas = _filas_a_texto("Tabla", ([c.text for c in fila.cells] for fila in tabla.rows))
            if filas:
                secciones.append((f"Tabla {i}", "\n".join(filas)))
        return secciones
    if ext == ".pptx":
        from pptx import Presentation
        secciones = []
        for i, sl in enumerate(Presentation(str(p)).slides, 1):
            textos = []
            for sh in sl.shapes:
                if sh.has_text_frame and sh.text_frame.text.strip():
                    textos.append(sh.text_frame.text.strip())
                if getattr(sh, "has_table", False) and sh.has_table:
                    textos += _filas_a_texto("Tabla", ([c.text for c in f.cells] for f in sh.table.rows))
            titulo = textos[0].splitlines()[0][:80] if textos else ""
            cuerpo = "\n".join(textos)
            if sl.has_notes_slide:
                nota = sl.notes_slide.notes_text_frame.text.strip()
                if nota:
                    cuerpo += "\nGuion: " + nota
            secciones.append((f"Diapositiva {i}: {titulo}", cuerpo))
        return secciones
    if ext in (".xlsx", ".xlsm"):
        import openpyxl
        wb = openpyxl.load_workbook(str(p), read_only=True, data_only=True)
        secciones = [(f"Hoja {ws.title}", "\n".join(_filas_a_texto(ws.title, ws.iter_rows(values_only=True))))
                     for ws in wb.worksheets]
        wb.close()
        return secciones
    if ext == ".csv":
        raw = p.read_text(encoding="utf-8-sig", errors="ignore")
        try:
            dialecto = csv.Sniffer().sniff(raw[:4096], delimiters=";,\t|")
        except csv.Error:
            dialecto = csv.excel
        return [("", "\n".join(_filas_a_texto(p.stem, csv.reader(raw.splitlines(), dialecto))))]
    return []


def leer_documento(p: Path) -> str:
    return "\n".join(f"{t}\n{c}" if t else c for t, c in leer_secciones(p))


def trocear(texto: str, tam: int = 900, solape: int = 150) -> list[str]:
    texto = re.sub(r"[ \t]+", " ", texto)
    piezas = []
    for linea in (l.strip() for l in texto.splitlines()):
        while len(linea) > tam:
            corte = linea.rfind(" ", 0, tam)
            corte = corte if corte > tam // 2 else tam
            piezas.append(linea[:corte])
            linea = linea[corte:].strip()
        if linea:
            piezas.append(linea)
    trozos, actual = [], ""
    for pz in piezas:
        if actual and len(actual) + len(pz) + 1 > tam:
            trozos.append(actual)
            actual = actual[-solape:] + "\n" + pz
        else:
            actual = f"{actual}\n{pz}" if actual else pz
    if actual:
        trozos.append(actual)
    return trozos


class BaseConocimiento:
    EXT = {".txt", ".md", ".pdf", ".docx", ".pptx", ".xlsx", ".xlsm", ".csv"}

    def __init__(self, carpeta: Path):
        self.carpeta = Path(carpeta)
        self.frag: list[tuple[str, str]] = []
        self.bm25 = None
        self.lock = threading.Lock()

    def cargar(self) -> int:
        self.carpeta.mkdir(exist_ok=True)
        frag = []
        for p in sorted(self.carpeta.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in self.EXT or p.name.startswith("~$"):
                continue
            try:
                secciones = leer_secciones(p)
            except Exception as e:
                print(f"[docs] No pude leer {p.name}: {e}")
                continue
            for titulo, texto in secciones:
                if not texto.strip():
                    continue
                pref = f"[{titulo}]\n" if titulo else ""
                frag += [(p.name, pref + c) for c in trocear(texto, tam=1100)]
        bm, conj = None, []
        if frag:
            from rank_bm25 import BM25Plus  # IDF siempre > 0, funciona bien con pocos documentos
            toks = [tokens(t) or ["_"] for _, t in frag]
            bm, conj = BM25Plus(toks), [set(t) for t in toks]
        with self.lock:
            self.frag, self.bm25, self.conj = frag, bm, conj
        return len(frag)

    def buscar(self, consulta: str, k: int = FRAGMENTOS_POR_PREGUNTA) -> list[tuple[str, str]]:
        with self.lock:
            frag, bm, conj = self.frag, self.bm25, getattr(self, "conj", [])
        q = tokens(consulta)
        if not bm or not q:
            return []
        sc = bm.get_scores(q)
        qs = set(q)
        return [frag[i] for i in np.argsort(sc)[::-1] if qs & conj[i]][:k]


# ─────────────────────────── audio ───────────────────────────
def _registrar_dlls_cuda():
    """Hace visibles cuBLAS/cuDNN instalados por pip (nvidia-*-cu12) en Windows."""
    if os.name != "nt":
        return
    try:
        import nvidia
        raiz = Path(list(nvidia.__path__)[0])
    except Exception:
        return
    for sub in ("cublas", "cudnn", "cuda_nvrtc", "cuda_runtime"):
        b = raiz / sub / "bin"
        if b.is_dir():
            os.add_dll_directory(str(b))
            os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


class CapturaAudio:
    """Loopback WASAPI: captura lo que suena en el dispositivo de salida predeterminado."""

    def __init__(self, salida_q: queue.Queue):
        import pyaudiowpatch as pyaudio
        self.pyaudio = pyaudio
        self.q = salida_q
        self.pa = pyaudio.PyAudio()
        self.dev = self.pa.get_default_wasapi_loopback()
        self.rate = int(self.dev["defaultSampleRate"])
        self.ch = int(self.dev["maxInputChannels"])
        g = gcd(16000, self.rate)
        self.up, self.down = 16000 // g, self.rate // g
        self.stream = None

    @property
    def nombre(self) -> str:
        return self.dev["name"].replace(" [Loopback]", "")

    def _cb(self, in_data, frame_count, time_info, status):
        x = np.frombuffer(in_data, dtype=np.int16).astype(np.float32) / 32768.0
        if self.ch > 1:
            x = x.reshape(-1, self.ch).mean(axis=1)
        if self.up != self.down:
            from scipy.signal import resample_poly
            x = resample_poly(x, self.up, self.down)
        self.q.put(np.clip(x, -1.0, 1.0).astype(np.float32))
        return (None, self.pyaudio.paContinue)

    def iniciar(self):
        self.stream = self.pa.open(
            format=self.pyaudio.paInt16, channels=self.ch, rate=self.rate, input=True,
            input_device_index=self.dev["index"], frames_per_buffer=int(self.rate * 0.03),
            stream_callback=self._cb)
        self.stream.start_stream()

    def detener(self):
        try:
            if self.stream:
                self.stream.stop_stream()
                self.stream.close()
            self.pa.terminate()
        except Exception:
            pass


class Segmentador(threading.Thread):
    """Corta el flujo de audio en frases usando WebRTC VAD (frames de 30 ms)."""
    FRAME = 480  # 30 ms @ 16 kHz

    def __init__(self, app):
        super().__init__(daemon=True)
        import webrtcvad
        self.app = app
        self.vad = webrtcvad.Vad(VAD_AGRESIVIDAD)
        self.n_sil = max(1, SILENCIO_MS // 30)
        self.max_frames = int(MAX_FRASE_S * 1000 / 30)
        self.min_voz = int(MIN_FRASE_S * 1000 / 30)
        self._reset()

    def _reset(self):
        self.preroll = deque(maxlen=max(1, PREROLL_MS // 30))
        self.voz, self.frames_voz, self.hablando, self.sil = [], 0, False, 0

    def _emitir(self):
        if self.frames_voz >= self.min_voz:
            self.app.frase_q.put((np.concatenate(self.voz), time.perf_counter()))
        self._reset()

    def _frame(self, f: np.ndarray):
        es_voz = self.vad.is_speech((f * 32767).astype(np.int16).tobytes(), 16000)
        if not self.hablando:
            self.preroll.append(f)
            if es_voz:
                self.hablando = True
                self.voz = list(self.preroll)
                self.preroll.clear()
                self.frames_voz, self.sil = 1, 0
            return
        self.voz.append(f)
        if es_voz:
            self.frames_voz += 1
            self.sil = 0
        else:
            self.sil += 1
        if self.sil >= self.n_sil or len(self.voz) >= self.max_frames:
            self._emitir()

    def run(self):
        resto = np.zeros(0, dtype=np.float32)
        while True:
            try:
                chunk = self.app.audio_q.get(timeout=0.1)
            except queue.Empty:
                # WASAPI loopback no entrega paquetes cuando no suena nada → cuenta como silencio
                if self.hablando:
                    self.sil += 3
                    if self.sil >= self.n_sil:
                        self._emitir()
                continue
            if self.app.pausado:
                self._reset()
                resto = resto[:0]
                continue
            resto = np.concatenate([resto, chunk])
            n = len(resto) // self.FRAME
            for i in range(n):
                self._frame(resto[i * self.FRAME:(i + 1) * self.FRAME])
            resto = resto[n * self.FRAME:]


# ─────────────────────────── transcripción ───────────────────────────
class Transcriptor(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.listo = threading.Event()
        self.model = None
        self.desc = ""

    def _cargar(self):
        _registrar_dlls_cuda()
        from faster_whisper import WhisperModel
        silencio = np.zeros(16000, dtype=np.float32)
        try:
            m = WhisperModel(WHISPER_MODEL, device="cuda", compute_type=WHISPER_COMPUTE)
            list(m.transcribe(silencio, language=IDIOMA, vad_filter=WHISPER_VAD)[0])  # carga cuBLAS/cuDNN y Silero
            return m, f"{WHISPER_MODEL} · GPU"
        except Exception as e:
            print(f"[whisper] CUDA no disponible ({e}). Usando CPU con modelo 'small'.")
            m = WhisperModel("small", device="cpu", compute_type="int8")
            list(m.transcribe(silencio, language=IDIOMA, vad_filter=WHISPER_VAD)[0])
            return m, "small · CPU (⚠ revisa CUDA)"

    def run(self):
        self.app.estado(f"Cargando Whisper {WHISPER_MODEL} (la 1ª vez descarga ~1,6 GB)…")
        try:
            self.model, self.desc = self._cargar()
        except Exception as e:
            self.app.estado(f"Error cargando Whisper: {e}")
            return
        self.listo.set()
        while True:
            audio, t_fin = self.app.frase_q.get()
            t0 = time.perf_counter()
            try:
                segs, _ = self.model.transcribe(
                    audio, language=IDIOMA, beam_size=1, vad_filter=WHISPER_VAD,
                    condition_on_previous_text=False, without_timestamps=True,
                    hotwords=PALABRAS_CLAVE or None)
                texto = " ".join(s.text.strip() for s in segs if segmento_valido(s)).strip()
            except Exception as e:
                print(f"[whisper] {e}")
                continue
            if texto and not es_alucinacion(texto):
                self.app.on_texto(texto, t_fin, (time.perf_counter() - t0) * 1000, len(audio) / 16000)


# ─────────────────────────── LLM (Ollama) ───────────────────────────
class Respondedor(threading.Thread):
    def __init__(self, app):
        super().__init__(daemon=True)
        self.app = app
        self.q = queue.Queue()
        self.gen = 0
        self.ocupado = False  # True mientras se genera una respuesta (o hay una pendiente)

    def solicitar(self, preguntas, historial, t_ref):
        if isinstance(preguntas, str):
            preguntas = [preguntas]
        self.gen += 1
        self.ocupado = True
        self.q.put((self.gen, list(preguntas), historial, t_ref))

    def calentar(self) -> bool:
        import requests
        try:
            r = requests.post(OLLAMA_URL, timeout=180, json={
                "model": LLM_MODEL, "stream": False, "keep_alive": "60m",
                "messages": [{"role": "user", "content": "ok"}],
                "options": {"num_predict": 1, "num_ctx": LLM_NUM_CTX}})
            return r.ok
        except Exception:
            return False

    def _mensajes(self, preguntas, hist):
        if len(preguntas) == 1:
            frag = self.app.kb.buscar(preguntas[0] + " " + " ".join(hist[-2:]))
            pedido = f"PREGUNTA QUE ME HICIERON:\n{preguntas[0]}"
        else:  # buscar por separado para cada pregunta y unir sin repetir
            frag, vistos = [], set()
            for p in preguntas:
                for f in self.app.kb.buscar(p, k=2):
                    if f not in vistos:
                        vistos.add(f)
                        frag.append(f)
            frag = frag[:6]  # que quepa en LLM_NUM_CTX
            lista = "\n".join(f"{i}) {p}" for i, p in enumerate(preguntas, 1))
            pedido = (f"ME HICIERON {len(preguntas)} PREGUNTAS (responde cada una, en este orden):\n{lista}")
        docs = "\n\n".join(f"[{i + 1}] ({f})\n{t}" for i, (f, t) in enumerate(frag)) or "(sin coincidencias)"
        conv = "\n".join(f"- {h}" for h in hist[-8:]) or "(vacía)"
        user = (f"CONTEXTO PERSONAL:\n{self.app.contexto or '(no definido)'}\n\n"
                f"DOCUMENTOS RELEVANTES:\n{docs}\n\n"
                f"CONVERSACIÓN RECIENTE:\n{conv}\n\n"
                f"{pedido}")
        fuentes = sorted({f for f, _ in frag})
        return [{"role": "system", "content": SISTEMA}, {"role": "user", "content": user}], fuentes

    @staticmethod
    def _clave_openai():
        clave = os.environ.get("OPENAI_API_KEY")
        if clave:
            return clave.strip()
        for env in (BASE / ".env", BASE / ".env" / ".env", BASE / ".env" / ".env.txt", BASE / ".env.txt"):
            if env.is_file():
                for linea in env.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                    linea = linea.strip()
                    if linea.startswith("OPENAI_API_KEY") and "=" in linea:
                        return linea.split("=", 1)[1].strip().strip('"').strip("'")
        return None

    def _generar_openai(self, gen, mensajes, max_tokens):
        import requests
        try:
            clave = self._clave_openai()
            if not clave:
                self.app.ui_q.put(("tok2", "[Falta OPENAI_API_KEY en el entorno o en .env]"))
                return
            payload = {"model": OPENAI_MODELO, "stream": True, "messages": mensajes,
                       "max_completion_tokens": max_tokens + (0 if OPENAI_ESFUERZO in (None, "none") else 2000)}
            if OPENAI_ESFUERZO:
                payload["reasoning_effort"] = OPENAI_ESFUERZO
            with requests.post(OPENAI_URL, json=payload, stream=True, timeout=(10, 120),
                               headers={"Authorization": f"Bearer {clave}"}) as r:
                if not r.ok:
                    raise RuntimeError(f"HTTP {r.status_code}: {r.text[:200]}")
                for linea in r.iter_lines():
                    if gen != self.gen:
                        return
                    if not linea or not linea.startswith(b"data: "):
                        continue
                    dato = linea[6:]
                    if dato == b"[DONE]":
                        break
                    for ch in json.loads(dato).get("choices", []):
                        tok = (ch.get("delta") or {}).get("content") or ""
                        if tok:
                            self.app.ui_q.put(("tok2", tok))
        except Exception as e:
            self.app.ui_q.put(("tok2", f"\n[Error OpenAI: {e}]"))

    def _generar(self, gen, preguntas, hist, t_ref):
        mensajes, fuentes = self._mensajes(preguntas, hist)
        titulo = preguntas[0] if len(preguntas) == 1 else "\n".join(f"{i}) {p}" for i, p in enumerate(preguntas, 1))
        self.app.ui_q.put(("resp_inicio", titulo, fuentes))
        max_tokens = LLM_MAX_TOKENS + TOKENS_POR_PREGUNTA_EXTRA * (len(preguntas) - 1)
        hilo = threading.Thread(target=self._generar_openai, args=(gen, mensajes, max_tokens), daemon=True)
        hilo.start()
        try:
            self._generar_local(gen, mensajes, t_ref, max_tokens)
        finally:
            hilo.join()

    def _generar_local(self, gen, mensajes, t_ref, max_tokens=LLM_MAX_TOKENS):
        import requests
        payload = {"model": LLM_MODEL, "stream": True, "keep_alive": "60m", "messages": mensajes,
                   "options": {"temperature": LLM_TEMPERATURA, "num_ctx": LLM_NUM_CTX,
                               "num_predict": max_tokens}}
        primero = True
        with requests.post(OLLAMA_URL, json=payload, stream=True, timeout=(3, 120)) as r:
            r.raise_for_status()
            for linea in r.iter_lines():
                if gen != self.gen:  # llegó una pregunta más nueva → abortar
                    return
                if not linea:
                    continue
                d = json.loads(linea)
                tok = d.get("message", {}).get("content", "")
                if tok:
                    if primero:
                        self.app.ui_q.put(("lat_llm", (time.perf_counter() - t_ref) * 1000))
                        primero = False
                    self.app.ui_q.put(("tok", tok))
                if d.get("done"):
                    break

    def run(self):
        while True:
            item = self.q.get()
            try:
                while True:
                    item = self.q.get_nowait()  # quedarse solo con la más reciente
            except queue.Empty:
                pass
            gen, preguntas, hist, t_ref = item
            if gen != self.gen:
                continue
            try:
                self._generar(gen, preguntas, hist, t_ref)
            except Exception as e:
                self.app.ui_q.put(("tok", f"\n[Error LLM: {e}. ¿Está Ollama abierto?]"))
            self.app.ui_q.put(("resp_fin",))
            if gen == self.gen and self.q.empty():
                self.ocupado = False


# ─────────────────────────── orquestador ───────────────────────────
class App:
    def __init__(self, capturar: bool = True):
        self.capturar = capturar  # False = sin loopback (simulador / tests)
        self.ui_q, self.audio_q, self.frase_q = Difusor(), queue.Queue(), queue.Queue()
        self.pausado = False
        self.auto = RESPONDER_AUTOMATICO
        self.historial = deque(maxlen=LINEAS_HISTORIAL)
        self.kb = BaseConocimiento(DOCS_DIR)
        self.contexto = ""
        self.captura = None
        self.transcriptor = Transcriptor(self)
        self.respondedor = Respondedor(self)
        self.segmentador = Segmentador(self)
        self.log = None
        self.listo = threading.Event()
        self.web = None
        self.enlaces = []
        self.preguntas = []          # preguntas que se están respondiendo juntas
        self.t_ult_preg = -1e9
        self.t_fin_preg = None       # fin (perf_counter) del último pedazo de pregunta
        self.n_cont = 0
        self.ui_q.put(("auto", self.auto))
        if GUARDAR_TRANSCRIPCION:
            (BASE / "transcripciones").mkdir(exist_ok=True)
            self.log = BASE / "transcripciones" / f"{datetime.now():%Y-%m-%d_%H%M}.txt"

    # API usada por los hilos
    def estado(self, s):
        self.ui_q.put(("estado", s))

    def on_texto(self, texto, t_fin, stt_ms, dur=None):
        self.historial.append(texto)
        preg = es_pregunta(texto)
        self.ui_q.put(("trans", texto, preg))
        self.ui_q.put(("lat_stt", stt_ms))
        if self.log:
            with open(self.log, "a", encoding="utf-8") as f:
                f.write(f"[{datetime.now():%H:%M:%S}] {texto}\n")
        if self.auto and not self.pausado:
            if preg:
                self.encolar_pregunta(texto, t_fin)
                self.t_fin_preg, self.n_cont = t_fin, 0
            elif self._es_continuacion(t_fin, dur):
                self.continuar_pregunta(texto, t_fin)

    def _es_continuacion(self, t_fin, dur) -> bool:
        """¿Este pedazo sigue a una pregunta tras una pausa corta? ("¿cómo afirma que MLP" … "es mejor")"""
        if self.t_fin_preg is None or not self.preguntas or self.n_cont >= MAX_CONTINUACIONES:
            return False
        # t_fin = fin de la voz + SILENCIO_MS; el audio incluye PREROLL_MS antes de la voz
        inicio_voz = t_fin - (dur or 0) + PREROLL_MS / 1000
        fin_voz_anterior = self.t_fin_preg - SILENCIO_MS / 1000
        return inicio_voz - fin_voz_anterior < CONTINUACION_S

    def continuar_pregunta(self, texto, t_ref=None):
        self.preguntas[-1] = f"{self.preguntas[-1]} {texto}"
        self.n_cont += 1
        self.t_fin_preg = t_ref
        self.t_ult_preg = time.monotonic()
        self.respondedor.solicitar(self.preguntas, list(self.historial), t_ref or time.perf_counter())

    def encolar_pregunta(self, texto, t_ref=None):
        """Si la pregunta llega mientras se responde la anterior (o muy seguida), se suma a ella
        y se responden todas juntas, en vez de descartar la anterior."""
        ahora = time.monotonic()
        if not (self.respondedor.ocupado or ahora - self.t_ult_preg < VENTANA_PREGUNTAS_S):
            self.preguntas = []
        self.t_ult_preg = ahora
        for p in dividir_preguntas(texto):
            if p not in self.preguntas:
                self.preguntas.append(p)
        self.preguntas = self.preguntas[-MAX_PREGUNTAS:]
        self.respondedor.solicitar(self.preguntas, list(self.historial), t_ref or time.perf_counter())

    def pedir_respuesta(self, pregunta=None, t_ref=None):
        if pregunta is None:
            if not self.historial:
                return
            pregunta = " ".join(list(self.historial)[-2:])
        self.preguntas = dividir_preguntas(pregunta)[-MAX_PREGUNTAS:]
        self.t_ult_preg = time.monotonic()
        self.respondedor.solicitar(self.preguntas, list(self.historial), t_ref or time.perf_counter())

    def alternar_pausa(self):
        self.pausado = not self.pausado
        self.ui_q.put(("pausa", self.pausado))

    def fijar_auto(self, valor: bool):
        self.auto = bool(valor)
        self.ui_q.put(("auto", self.auto))

    def limpiar(self):
        self.ui_q.put(("limpiar",))

    def _enlace(self, texto):
        try:
            print(texto)
        except Exception:
            pass
        self.enlaces.append(texto)
        self.ui_q.put(("web", "\n".join(self.enlaces)))
        try:
            ENLACES_FILE.write_text("\n".join(self.enlaces) + "\n", encoding="utf-8")
        except OSError:
            pass

    def _iniciar_web(self):
        try:
            self.web = ServidorWeb(self, WEB_PUERTO)
            self.web.iniciar()
        except Exception as e:
            self._enlace(f"Panel web desactivado: {e}")
            return
        self._enlace(f"Tablet (misma red wifi): {self.web.url_local()}")

    def _leer_contexto(self):
        try:
            self.contexto = CONTEXTO_FILE.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            self.contexto = ""

    def recargar_docs(self):
        def _r():
            self.estado("Reindexando documentos…")
            self._leer_contexto()
            n = self.kb.cargar()
            self.estado(f"Escuchando · {n} fragmentos indexados")
        threading.Thread(target=_r, daemon=True).start()

    def _arranque(self):
        try:
            self.estado("Indexando documentos…")
            self._leer_contexto()
            n = self.kb.cargar()
            self.transcriptor.start()
            self.transcriptor.listo.wait()
            self.estado(f"Cargando {LLM_MODEL} en la GPU…")
            ok = self.respondedor.calentar()
            fuente = "simulación"
            if self.capturar:
                self.captura = CapturaAudio(self.audio_q)
                self.captura.iniciar()
                fuente = self.captura.nombre
            aviso = "" if ok else " · ⚠ Ollama no responde"
            self.estado(f"Escuchando: {fuente} · Whisper {self.transcriptor.desc} · "
                        f"{n} fragmentos{aviso}")
            self.listo.set()
        except Exception as e:
            self.estado(f"Error al iniciar: {e}")

    def iniciar(self, hotkeys: bool = True, web: bool = False):
        if web:
            self._iniciar_web()
        self.segmentador.start()
        self.respondedor.start()
        threading.Thread(target=self._arranque, daemon=True).start()
        if not hotkeys:
            return
        try:
            import keyboard
            keyboard.add_hotkey(HOTKEY_RESPONDER, lambda: self.pedir_respuesta())
            keyboard.add_hotkey(HOTKEY_PAUSA, self.alternar_pausa)
        except Exception as e:
            print(f"[hotkeys] desactivados: {e}")

    def cerrar(self):
        if self.captura:
            self.captura.detener()
        if self.web:
            self.web.cerrar()


# ─────────────────────────── interfaz ───────────────────────────
class Interfaz:
    BG, PANEL, TXT, TENUE = "#111418", "#1a1f26", "#e6edf3", "#8b98a5"
    PREG, OK = "#f0c674", "#7ee2a8"

    def __init__(self, app: App):
        self.app = app
        self.fuentes = []
        self.lat = {"stt": None, "llm": None}
        r = self.root = tk.Tk()
        r.title("Apuntador")
        r.geometry("480x800+30+30")
        r.configure(bg=self.BG)
        r.attributes("-topmost", True)
        r.attributes("-alpha", OPACIDAD)

        cab = tk.Frame(r, bg=self.BG)
        cab.pack(fill="x", padx=10, pady=(8, 2))
        self.lbl_estado = tk.Label(cab, text="Iniciando…", fg=self.TENUE, bg=self.BG, anchor="w",
                                   justify="left", wraplength=460, font=("Segoe UI", 9))
        self.lbl_estado.pack(fill="x")
        self.lbl_lat = tk.Label(cab, text="", fg=self.OK, bg=self.BG, anchor="w", font=("Consolas", 9))
        self.lbl_lat.pack(fill="x")
        self.lbl_web = tk.Label(cab, text="", fg=self.TENUE, bg=self.BG, anchor="w", justify="left",
                                wraplength=460, font=("Consolas", 8))
        self.lbl_web.pack(fill="x")

        self._titulo("TRANSCRIPCIÓN")
        self.t_trans = self._texto(8, ("Segoe UI", 10), self.TENUE)
        self.t_trans.tag_configure("preg", foreground=self.PREG)
        self._titulo(f"SUGERENCIA · LOCAL ({LLM_MODEL})")
        self.t_resp = self._texto(9, ("Segoe UI", 13), self.TXT, expand=True)
        self._titulo(f"SUGERENCIA · OPENAI ({OPENAI_MODELO})")
        self.t_resp2 = self._texto(9, ("Segoe UI", 13), self.TXT, expand=True)
        for t in (self.t_resp, self.t_resp2):
            t.tag_configure("preg", foreground=self.PREG, font=("Segoe UI", 10, "italic"))
            t.tag_configure("fuente", foreground=self.TENUE, font=("Segoe UI", 8))
            t.tag_configure("aviso", foreground="#ff9e64", font=("Segoe UI", 10, "bold"))

        bot = tk.Frame(r, bg=self.BG)
        bot.pack(fill="x", padx=10, pady=8)
        self.btn_pausa = self._boton(bot, "Pausar", self.app.alternar_pausa)
        self._boton(bot, "Responder", lambda: self.app.pedir_respuesta())
        self._boton(bot, "Limpiar", self.app.limpiar)
        self._boton(bot, "Recargar docs", self.app.recargar_docs)
        self.var_auto = tk.BooleanVar(value=app.auto)
        tk.Checkbutton(bot, text="Auto", variable=self.var_auto, bg=self.BG, fg=self.TXT,
                       selectcolor=self.PANEL, activebackground=self.BG, activeforeground=self.TXT,
                       command=lambda: app.fijar_auto(self.var_auto.get())).pack(side="right")

        r.protocol("WM_DELETE_WINDOW", self.cerrar)
        r.after(40, self._poll)

    # widgets
    def _titulo(self, t):
        tk.Label(self.root, text=t, fg=self.TENUE, bg=self.BG, anchor="w",
                 font=("Segoe UI", 8, "bold")).pack(fill="x", padx=10, pady=(6, 0))

    def _texto(self, alto, fuente, color, expand=False):
        t = tk.Text(self.root, height=alto, wrap="word", bg=self.PANEL, fg=color, font=fuente,
                    bd=0, padx=8, pady=6, highlightthickness=0, state="disabled")
        t.pack(fill="both", expand=expand, padx=10)
        return t

    def _boton(self, padre, txt, cmd):
        b = tk.Button(padre, text=txt, command=cmd, bg=self.PANEL, fg=self.TXT, bd=0, padx=10, pady=4,
                      activebackground="#2a313b", activeforeground=self.TXT, font=("Segoe UI", 9))
        b.pack(side="left", padx=(0, 6))
        return b

    @staticmethod
    def _agregar(t, s, tag=None):
        t.configure(state="normal")
        t.insert("end", s, tag)
        if int(t.index("end-1c").split(".")[0]) > 300:
            t.delete("1.0", "50.0")
        t.see("end")
        t.configure(state="disabled")

    def limpiar(self):
        for t in (self.t_trans, self.t_resp, self.t_resp2):
            t.configure(state="normal")
            t.delete("1.0", "end")
            t.configure(state="disabled")

    def _pintar_lat(self):
        p = []
        if self.lat["stt"] is not None:
            p.append(f"STT {self.lat['stt']:.0f} ms")
        if self.lat["llm"] is not None:
            p.append(f"1ª palabra {self.lat['llm'] / 1000:.2f} s")
        self.lbl_lat.configure(text="  ·  ".join(p))

    def _poll(self):
        try:
            while True:
                ev = self.app.ui_q.get_nowait()
                tipo = ev[0]
                if tipo == "estado":
                    self.lbl_estado.configure(text=ev[1])
                elif tipo == "trans":
                    self._agregar(self.t_trans, ev[1] + "\n", "preg" if ev[2] else None)
                elif tipo == "resp_inicio":
                    self.fuentes = ev[2]
                    for t in (self.t_resp, self.t_resp2):
                        t.configure(state="normal")
                        t.delete("1.0", "end")
                        t.configure(state="disabled")
                        self._agregar(t, f"↳ {ev[1]}\n\n", "preg")
                        if not self.fuentes:
                            self._agregar(t, "⚠ Sin respaldo en tus documentos — respuesta improvisada\n\n", "aviso")
                elif tipo == "tok":
                    self._agregar(self.t_resp, ev[1])
                elif tipo == "tok2":
                    self._agregar(self.t_resp2, ev[1])
                elif tipo == "resp_fin" and self.fuentes:
                    for t in (self.t_resp, self.t_resp2):
                        self._agregar(t, "\n\nFuentes: " + ", ".join(self.fuentes), "fuente")
                elif tipo == "lat_stt":
                    self.lat["stt"] = ev[1]
                    self._pintar_lat()
                elif tipo == "lat_llm":
                    self.lat["llm"] = ev[1]
                    self._pintar_lat()
                elif tipo == "pausa":
                    self.btn_pausa.configure(text="Reanudar" if ev[1] else "Pausar")
                    self.lbl_estado.configure(text="⏸ En pausa" if ev[1] else "Escuchando")
                elif tipo == "auto":
                    self.var_auto.set(ev[1])
                elif tipo == "limpiar":
                    self.limpiar()
                elif tipo == "web":
                    self.lbl_web.configure(text=ev[1])
        except queue.Empty:
            pass
        self.root.after(40, self._poll)

    def cerrar(self):
        self.app.cerrar()
        self.root.destroy()
        os._exit(0)


def _servicio(app: App):
    """Modo servicio: sin ventana; el panel web es la interfaz. La consola muestra estado y enlaces."""
    print("Apuntador en modo servicio. Cierra esta ventana o pulsa Ctrl+C para detenerlo.")
    try:
        while True:
            try:
                ev = app.ui_q.get(timeout=1)
            except queue.Empty:
                continue
            if ev[0] in ("estado", "trans"):
                try:
                    print(("» " if ev[0] == "trans" else "· ") + ev[1])
                except Exception:
                    pass
    except KeyboardInterrupt:
        pass
    finally:
        app.cerrar()


def _bandeja(app: App):
    """Modo servicio oculto: sin consola ni ventana, solo un ícono en la bandeja del sistema."""
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        print("pystray/Pillow no instalados: el servicio corre oculto y sin ícono. Detenlo desde el "
              "Administrador de tareas (pythonw.exe).")
        while True:
            app.ui_q.get()

    import webbrowser
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    dib = ImageDraw.Draw(img)
    dib.ellipse((4, 4, 60, 60), fill="#7ee2a8")
    dib.ellipse((22, 22, 42, 42), fill="#111418")

    def abrir_panel(icono, item):
        if app.web:
            webbrowser.open(f"http://127.0.0.1:{WEB_PUERTO}/")

    def ver_enlaces(icono, item):
        if ENLACES_FILE.exists():
            os.startfile(ENLACES_FILE)

    icono = pystray.Icon("apuntador", img, "Apuntador · iniciando…", pystray.Menu(
        pystray.MenuItem("Abrir panel", abrir_panel, default=True),
        pystray.MenuItem("Ver enlace para la tablet", ver_enlaces),
        pystray.MenuItem("Pausar / reanudar", lambda i, it: app.alternar_pausa()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Salir", lambda i, it: i.stop())))

    def drenar():
        while True:
            ev = app.ui_q.get()
            if ev[0] == "estado":
                icono.title = ("Apuntador · " + ev[1])[:120]
                print(ev[1])
    threading.Thread(target=drenar, daemon=True).start()
    icono.run()  # bloquea hasta "Salir"


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Apuntador de llamadas")
    ap.add_argument("--modo", choices=("normal", "servicio", "doble"), default=MODO)
    ap.add_argument("--oculto", action="store_true",
                    help="modo servicio sin consola: ícono en la bandeja y registro en servicio.log")
    args = ap.parse_args()
    if args.oculto:  # con pythonw no hay consola: todo lo impreso va a servicio.log
        sys.stdout = sys.stderr = open(BASE / "servicio.log", "w", encoding="utf-8", buffering=1)
    web = args.modo in ("servicio", "doble")

    app = App()
    if args.modo == "servicio":
        app.iniciar(web=True)
        if args.oculto:
            try:
                _bandeja(app)
            finally:
                app.cerrar()
                os._exit(0)
        _servicio(app)
        return
    ui = Interfaz(app)
    app.iniciar(web=web)
    ui.root.mainloop()


if __name__ == "__main__":
    main()
