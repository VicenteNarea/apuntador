"""Compara la latencia del LLM local (Ollama) contra la API de OpenAI usando el MISMO prompt
que arma la app (instrucciones + contexto.txt + fragmentos de docs/ + pregunta).

Mide por cada pregunta y modelo:
  - TTFT: tiempo hasta la primera palabra (lo que importa en la app)
  - Total: tiempo hasta terminar la respuesta
  - Tokens/s de salida (aprox.)

Uso:
  .venv\\Scripts\\python.exe herramientas\\comparar_latencia.py [--reps 3] [--modelos qwen2.5:7b,gpt-6-luna]

La clave de OpenAI se lee de la variable de entorno OPENAI_API_KEY o de un archivo .env en la raíz
del proyecto con la línea:  OPENAI_API_KEY=sk-...
(.env está en .gitignore; nunca se imprime ni se guarda en los resultados.)

Preguntas: una por línea en herramientas\\preguntas_benchmark.txt (si no existe, usa un set por defecto).
Resultados: resultados_latencia\\AAAA-MM-DD_HHMM.csv y .md
"""
import argparse
import csv
import json
import os
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))
import asistente_llamadas as A  # noqa: E402

import requests  # noqa: E402

OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OPENAI_MODELO = "gpt-6-luna"
PREGUNTAS_DEFECTO = [
    "¿Cuál es el OPEX del proyecto?",
    "¿Por qué eligieron el MLP?",
    "¿Qué protocolo usa el sistema para leer los correos?",
    "¿Qué pasa si se cae ERPNext?",
    "¿Cuál fue el accuracy en datos reales?",
]


def leer_clave() -> str | None:
    clave = os.environ.get("OPENAI_API_KEY")
    if clave:
        return clave.strip()
    # acepta .env como archivo, o una carpeta .env con el archivo adentro (.env\\.env o .env\\.env.txt)
    for env in (RAIZ / ".env", RAIZ / ".env" / ".env", RAIZ / ".env" / ".env.txt", RAIZ / ".env.txt"):
        if not env.is_file():
            continue
        for linea in env.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
            linea = linea.strip()
            if linea.startswith("OPENAI_API_KEY"):
                return linea.split("=", 1)[1].strip().strip('"').strip("'") if "=" in linea else None
    return None


def armar_mensajes(kb, contexto, pregunta):
    frag = kb.buscar(pregunta)
    docs = "\n\n".join(f"[{i + 1}] ({f})\n{t}" for i, (f, t) in enumerate(frag)) or "(sin coincidencias)"
    user = (f"CONTEXTO PERSONAL:\n{contexto or '(no definido)'}\n\n"
            f"DOCUMENTOS RELEVANTES:\n{docs}\n\n"
            f"CONVERSACIÓN RECIENTE:\n(vacía)\n\n"
            f"PREGUNTA QUE ME HICIERON:\n{pregunta}")
    return [{"role": "system", "content": A.SISTEMA}, {"role": "user", "content": user}]


def medir_ollama(modelo, mensajes):
    payload = {"model": modelo, "stream": True, "keep_alive": "60m", "messages": mensajes,
               "options": {"temperature": A.LLM_TEMPERATURA, "num_ctx": A.LLM_NUM_CTX,
                           "num_predict": A.LLM_MAX_TOKENS}}
    t0 = time.perf_counter()
    ttft, texto, n_out = None, "", None
    with requests.post(A.OLLAMA_URL, json=payload, stream=True, timeout=(5, 180)) as r:
        r.raise_for_status()
        for linea in r.iter_lines():
            if not linea:
                continue
            d = json.loads(linea)
            tok = d.get("message", {}).get("content", "")
            if tok and ttft is None:
                ttft = time.perf_counter() - t0
            texto += tok
            if d.get("done"):
                n_out = d.get("eval_count")
                break
    return ttft, time.perf_counter() - t0, texto, n_out


class EsfuerzoNoSoportado(Exception):
    pass


def medir_openai(modelo, mensajes, clave, esfuerzo):
    payload = {"model": modelo, "stream": True, "messages": mensajes,
               "max_completion_tokens": A.LLM_MAX_TOKENS + (0 if esfuerzo in (None, "none") else 2000),
               "stream_options": {"include_usage": True}}
    if esfuerzo:
        payload["reasoning_effort"] = esfuerzo
    t0 = time.perf_counter()
    ttft, texto, n_out = None, "", None
    with requests.post(OPENAI_URL, json=payload, stream=True, timeout=(10, 180),
                       headers={"Authorization": f"Bearer {clave}"}) as r:
        if r.status_code == 400 and esfuerzo and "reasoning" in r.text:
            raise EsfuerzoNoSoportado(r.text[:200])
        if not r.ok:
            raise RuntimeError(f"HTTP {r.status_code}: {r.text[:300]}")
        for linea in r.iter_lines():
            if not linea or not linea.startswith(b"data: "):
                continue
            dato = linea[6:]
            if dato == b"[DONE]":
                break
            d = json.loads(dato)
            if d.get("usage"):
                n_out = d["usage"].get("completion_tokens")
            for ch in d.get("choices", []):
                tok = (ch.get("delta") or {}).get("content") or ""
                if tok and ttft is None:
                    ttft = time.perf_counter() - t0
                texto += tok
    return ttft, time.perf_counter() - t0, texto, n_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reps", type=int, default=3, help="repeticiones por pregunta (default 3)")
    ap.add_argument("--modelos", default=f"{A.LLM_MODEL},{OPENAI_MODELO}",
                    help="lista separada por comas; los que empiezan con 'gpt-' van a OpenAI")
    ap.add_argument("--esfuerzo", default="none",
                    help="reasoning_effort para OpenAI (none/low/medium…; 'omitir' para no enviarlo)")
    args = ap.parse_args()
    esfuerzo = None if args.esfuerzo == "omitir" else args.esfuerzo

    archivo_preg = Path(__file__).with_name("preguntas_benchmark.txt")
    preguntas = ([l.strip() for l in archivo_preg.read_text(encoding="utf-8").splitlines() if l.strip()]
                 if archivo_preg.exists() else PREGUNTAS_DEFECTO)
    modelos = [m.strip() for m in args.modelos.split(",") if m.strip()]

    kb = A.BaseConocimiento(A.DOCS_DIR)
    print(f"Documentos: {kb.cargar()} fragmentos · {len(preguntas)} preguntas · {args.reps} repeticiones")
    contexto = A.CONTEXTO_FILE.read_text(encoding="utf-8").strip() if A.CONTEXTO_FILE.exists() else ""

    clave = leer_clave()
    if any(m.startswith("gpt-") for m in modelos) and not clave:
        print("⚠ No encontré OPENAI_API_KEY (variable de entorno o archivo .env). Solo mido los modelos locales.")
        modelos = [m for m in modelos if not m.startswith("gpt-")]

    filas = []
    for modelo in modelos:
        es_openai = modelo.startswith("gpt-")
        print(f"\n=== {modelo} ({'OpenAI API' if es_openai else 'local · Ollama'}) ===")
        # calentamiento (carga el modelo en GPU / abre la conexión TLS); no se cuenta
        try:
            m0 = armar_mensajes(kb, contexto, preguntas[0])
            if es_openai:
                try:
                    medir_openai(modelo, m0, clave, esfuerzo)
                except EsfuerzoNoSoportado:
                    print(f"  (el modelo no acepta reasoning_effort={esfuerzo}; se omite)")
                    esfuerzo = None
                    medir_openai(modelo, m0, clave, esfuerzo)
            else:
                medir_ollama(modelo, m0)
        except Exception as e:
            print(f"  ✗ no se pudo usar {modelo}: {e}")
            continue
        for q in preguntas:
            mensajes = armar_mensajes(kb, contexto, q)
            for rep in range(1, args.reps + 1):
                try:
                    if es_openai:
                        ttft, total, texto, n_out = medir_openai(modelo, mensajes, clave, esfuerzo)
                    else:
                        ttft, total, texto, n_out = medir_ollama(modelo, mensajes)
                except Exception as e:
                    print(f"  ✗ {q[:40]}… rep {rep}: {e}")
                    continue
                tps = (n_out / (total - ttft)) if n_out and ttft and total > ttft else None
                filas.append({"modelo": modelo, "pregunta": q, "rep": rep,
                              "ttft_ms": round((ttft or 0) * 1000), "total_ms": round(total * 1000),
                              "tokens_salida": n_out or "", "tokens_s": round(tps, 1) if tps else "",
                              "respuesta": " ".join(texto.split())[:300]})
                print(f"  {q[:45]:<45} rep {rep}: TTFT {filas[-1]['ttft_ms']:>5} ms · total {filas[-1]['total_ms']:>5} ms")

    if not filas:
        print("\nNo hubo mediciones.")
        return

    salida = RAIZ / "resultados_latencia"
    salida.mkdir(exist_ok=True)
    base = salida / datetime.now().strftime("%Y-%m-%d_%H%M")
    with open(base.with_suffix(".csv"), "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(filas[0].keys()))
        w.writeheader()
        w.writerows(filas)

    def p(vals, q):
        vals = sorted(vals)
        return vals[min(len(vals) - 1, int(round(q * (len(vals) - 1))))]

    lineas = ["| Modelo | Dónde | TTFT mediana | TTFT p90 | Total mediana | Tokens/s | n |",
              "|---|---|---|---|---|---|---|"]
    for modelo in dict.fromkeys(f["modelo"] for f in filas):
        fs = [f for f in filas if f["modelo"] == modelo]
        t = [f["ttft_ms"] for f in fs]
        tot = [f["total_ms"] for f in fs]
        tps = [f["tokens_s"] for f in fs if f["tokens_s"] != ""]
        donde = "OpenAI API" if modelo.startswith("gpt-") else "Local (GPU)"
        tps_txt = f"{statistics.median(tps):.0f}" if tps else "—"
        lineas.append(f"| {modelo} | {donde} | {statistics.median(t):.0f} ms | {p(t, 0.9):.0f} ms | "
                      f"{statistics.median(tot):.0f} ms | {tps_txt} | {len(fs)} |")
    tabla = "\n".join(lineas)
    detalle = "\n".join(f"- **{f['modelo']}** · {f['pregunta']} → {f['respuesta']}"
                        for f in filas if f["rep"] == 1)
    base.with_suffix(".md").write_text(
        f"# Comparación de latencia · {datetime.now():%Y-%m-%d %H:%M}\n\n"
        f"reasoning_effort OpenAI: `{esfuerzo}` · repeticiones: {args.reps}\n\n{tabla}\n\n"
        f"## Respuestas (1ª repetición)\n{detalle}\n", encoding="utf-8")
    print("\n" + tabla)
    print(f"\nGuardado en {base.with_suffix('.md')} y .csv")


if __name__ == "__main__":
    main()
