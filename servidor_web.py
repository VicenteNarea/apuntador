"""
Panel web del Apuntador: la misma vista de la ventana (transcripción, sugerencia local,
sugerencia OpenAI y botones) servida por HTTP para verla y controlarla desde una tablet,
en la misma red wifi.

Solo usa la biblioteca estándar. Los eventos llegan al navegador por Server-Sent Events.
"""
import json
import os
import queue
import socket
import threading
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


# ─────────────────────── difusión de eventos de UI ───────────────────────
class Difusor:
    """Reemplaza a la cola de UI: la ventana la sigue leyendo con get_nowait(), y además
    cada cliente web recibe una copia de los eventos más una foto del estado actual."""

    _ESTADO = ("estado", "pausa", "auto", "lat_stt", "lat_llm", "web")

    def __init__(self):
        self._q = queue.Queue()
        self._subs = set()
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._seq = 0
        self._log = deque(maxlen=5000)  # (n, evento) para los clientes por sondeo
        self._ultimo = {}
        self._trans = deque(maxlen=200)
        self._resp = []

    # interfaz de cola (la usan la ventana tkinter y el simulador)
    def put(self, ev):
        self._q.put(ev)
        with self._lock:
            self._registrar(ev)
            for s in self._subs:
                s.put(ev)
            self._seq += 1
            self._log.append((self._seq, ev))
            self._cond.notify_all()

    def get_nowait(self):
        return self._q.get_nowait()

    def get(self, *a, **k):
        return self._q.get(*a, **k)

    def empty(self):
        return self._q.empty()

    def _registrar(self, ev):
        t = ev[0]
        if t in self._ESTADO:
            self._ultimo.pop(t, None)  # reinsertar: la foto respeta el orden cronológico
            self._ultimo[t] = ev
        elif t == "trans":
            self._trans.append(ev)
        elif t == "resp_inicio":
            self._resp = [ev]
        elif t in ("tok", "tok2", "resp_fin"):
            if len(self._resp) < 20000:
                self._resp.append(ev)
        elif t == "limpiar":
            self._trans.clear()
            self._resp = []

    def _foto(self):
        return [("reset",), *self._ultimo.values(), *self._trans, *self._resp]

    def suscribir(self) -> queue.Queue:
        q = queue.Queue()
        with self._lock:
            for ev in self._foto():
                q.put(ev)
            self._subs.add(q)
        return q

    def esperar(self, desde: int, timeout: float = 20):
        """Sondeo largo: devuelve (n, eventos posteriores a 'desde'). Espera hasta timeout si no hay
        novedades. Si 'desde' es 0 o quedó fuera del registro, entrega la foto completa del estado."""
        with self._cond:
            if desde == self._seq:
                self._cond.wait(timeout)
            primero = self._log[0][0] if self._log else self._seq + 1
            if desde <= 0 or desde > self._seq or desde < primero - 1:
                return self._seq, self._foto()
            return self._seq, [ev for n, ev in self._log if n > desde]

    def desuscribir(self, q):
        with self._lock:
            self._subs.discard(q)


# ─────────────────────────── utilidades ───────────────────────────
def ip_local() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.255.255.255", 1))  # no envía nada; solo elige la interfaz de salida
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ─────────────────────────── servidor ───────────────────────────
class ServidorWeb:
    def __init__(self, app, puerto: int):
        self.app, self.puerto = app, puerto
        self.httpd = None

    def iniciar(self):
        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.puerto), _crear_manejador(self.app))
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def url_local(self):
        return f"http://{ip_local()}:{self.puerto}/"

    def cerrar(self):
        if self.httpd:
            self.httpd.shutdown()


def _crear_manejador(app):
    class Manejador(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _enviar(self, code, cuerpo=b"", tipo="text/plain; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if cuerpo:
                self.wfile.write(cuerpo)

        def do_GET(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            if u.path == "/":
                return self._enviar(200, PAGINA.encode("utf-8"), "text/html; charset=utf-8")
            if u.path == "/eventos":
                return self._sse()
            if u.path == "/sondeo":
                try:
                    desde = int((qs.get("desde") or ["0"])[0])
                except ValueError:
                    desde = 0
                n, evs = app.ui_q.esperar(desde)
                cuerpo = json.dumps({"n": n, "ev": evs}, ensure_ascii=False).encode("utf-8")
                return self._enviar(200, cuerpo, "application/json; charset=utf-8")
            self._enviar(404, b"no encontrado")

        def do_POST(self):
            u = urlparse(self.path)
            qs = parse_qs(u.query)
            acc = u.path.rstrip("/").rsplit("/", 1)[-1]
            if acc == "responder":
                app.pedir_respuesta()
            elif acc == "pausa":
                app.alternar_pausa()
            elif acc == "limpiar":
                app.limpiar()
            elif acc == "recargar":
                app.recargar_docs()
            elif acc == "auto":
                app.fijar_auto((qs.get("v") or ["1"])[0] == "1")
            else:
                return self._enviar(404, b"accion desconocida")
            self._enviar(204)

        def _sse(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            q = app.ui_q.suscribir()
            try:
                self.wfile.write(b"retry: 2000\n\n")
                self.wfile.flush()
                while True:
                    try:
                        lote = [q.get(timeout=15)]
                    except queue.Empty:
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        continue
                    try:
                        while len(lote) < 300:
                            lote.append(q.get_nowait())
                    except queue.Empty:
                        pass
                    datos = "".join(f"data: {json.dumps(e, ensure_ascii=False)}\n\n" for e in lote)
                    self.wfile.write(datos.encode("utf-8"))
                    self.wfile.flush()
            except OSError:  # el cliente se desconectó
                pass
            finally:
                app.ui_q.desuscribir(q)

    return Manejador


# ─────────────────────────── páginas ───────────────────────────
_ESTILO_BASE = """
:root{--bg:#111418;--panel:#1a1f26;--txt:#e6edf3;--tenue:#8b98a5;--preg:#f0c674;--ok:#7ee2a8;--aviso:#ff9e64;--boton:#2a313b}
*{box-sizing:border-box}
html,body{margin:0;height:100%;background:var(--bg);color:var(--txt);font-family:"Segoe UI",system-ui,-apple-system,Roboto,sans-serif}
"""

PAGINA = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="theme-color" content="#111418"><title>Apuntador</title>
<style>""" + _ESTILO_BASE + """
body{display:flex;flex-direction:column;height:100dvh}
header{padding:10px 16px 4px}
#estado{color:var(--tenue);font-size:.85rem}
#lat{color:var(--ok);font-family:Consolas,ui-monospace,monospace;font-size:.8rem;min-height:1em}
#con{display:inline-block;width:8px;height:8px;border-radius:50%;background:var(--aviso);margin-right:6px;vertical-align:middle}
#con.ok{background:var(--ok)}
main{flex:1;min-height:0;display:grid;gap:10px;padding:6px 16px;grid-template-rows:minmax(90px,.7fr) 1fr 1fr}
@media (min-width:900px){main{grid-template-columns:.8fr 1fr 1fr;grid-template-rows:1fr}}
section{display:flex;flex-direction:column;min-height:0}
h2{font-size:.7rem;letter-spacing:.06em;color:var(--tenue);margin:0 0 4px;font-weight:700}
.caja{flex:1;min-height:0;overflow-y:auto;background:var(--panel);border-radius:8px;padding:10px 12px;white-space:pre-wrap;word-wrap:break-word}
#trans{font-size:.95rem;color:var(--tenue)}
#local,#openai{font-size:1.15rem;line-height:1.4}
.preg{color:var(--preg)}
.caja .preg{font-style:italic;font-size:.9rem}
#trans .preg{font-style:normal;font-size:inherit}
.fuente{color:var(--tenue);font-size:.75rem}
.aviso{color:var(--aviso);font-weight:700;font-size:.9rem}
footer{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 16px calc(10px + env(safe-area-inset-bottom))}
button{background:var(--boton);color:var(--txt);border:0;border-radius:8px;padding:12px 18px;font:inherit;font-size:.95rem;cursor:pointer}
button:active{filter:brightness(1.4)}
label{margin-left:auto;display:flex;align-items:center;gap:8px;font-size:.95rem}
input[type=checkbox]{width:22px;height:22px;accent-color:var(--ok)}
</style></head><body>
<header><div><span id="con"></span><span id="estado">Conectando…</span></div><div id="lat"></div></header>
<main>
<section><h2>TRANSCRIPCIÓN</h2><div class="caja" id="trans"></div></section>
<section><h2>SUGERENCIA · LOCAL</h2><div class="caja" id="local"></div></section>
<section><h2>SUGERENCIA · OPENAI</h2><div class="caja" id="openai"></div></section>
</main>
<footer>
<button id="bPausa" data-a="pausa">Pausar</button>
<button data-a="responder">Responder</button>
<button data-a="limpiar">Limpiar</button>
<button data-a="recargar">Recargar docs</button>
<label><input type="checkbox" id="auto"> Auto</label>
</footer>
<script>
const $ = s => document.querySelector(s);
const tr = $('#trans'), rl = $('#local'), ro = $('#openai');
let fuentes = [], lat = {stt: null, llm: null};

function abajo(el){ return el.scrollHeight - el.scrollTop - el.clientHeight < 40; }
function add(el, txt, cls){
  const seguir = abajo(el);
  if (cls){ const n = document.createElement('span'); n.className = cls; n.textContent = txt; el.appendChild(n); }
  else if (el.lastChild && el.lastChild.nodeType === 3){ el.lastChild.data += txt; }
  else { el.appendChild(document.createTextNode(txt)); }
  if (seguir) el.scrollTop = el.scrollHeight;
}
function limpiar(){ tr.textContent = ''; rl.textContent = ''; ro.textContent = ''; }
function pintarLat(){
  const p = [];
  if (lat.stt != null) p.push('STT ' + Math.round(lat.stt) + ' ms');
  if (lat.llm != null) p.push('1ª palabra ' + (lat.llm / 1000).toFixed(2) + ' s');
  $('#lat').textContent = p.join('  ·  ');
}
function evento(e){
  const [t, ...a] = e;
  switch (t){
    case 'reset': limpiar(); break;
    case 'estado': $('#estado').textContent = a[0]; break;
    case 'trans': {
      const seguir = abajo(tr), d = document.createElement('div');
      d.textContent = a[0]; if (a[1]) d.className = 'preg'; tr.appendChild(d);
      while (tr.children.length > 300) tr.firstElementChild.remove();
      if (seguir) tr.scrollTop = tr.scrollHeight; break; }
    case 'lat_stt': lat.stt = a[0]; pintarLat(); break;
    case 'lat_llm': lat.llm = a[0]; pintarLat(); break;
    case 'resp_inicio':
      fuentes = a[1] || [];
      for (const el of [rl, ro]){
        el.textContent = ''; add(el, '↳ ' + a[0] + '\\n\\n', 'preg');
        if (!fuentes.length) add(el, '⚠ Sin respaldo en tus documentos — respuesta improvisada\\n\\n', 'aviso');
      } break;
    case 'tok': add(rl, a[0]); break;
    case 'tok2': add(ro, a[0]); break;
    case 'resp_fin': if (fuentes.length) for (const el of [rl, ro]) add(el, '\\n\\nFuentes: ' + fuentes.join(', '), 'fuente'); break;
    case 'pausa': $('#bPausa').textContent = a[0] ? 'Reanudar' : 'Pausar';
      $('#estado').textContent = a[0] ? '⏸ En pausa' : 'Escuchando'; break;
    case 'auto': $('#auto').checked = !!a[0]; break;
    case 'limpiar': limpiar(); break;
  }
}
// Primero intenta eventos en vivo (SSE). Si en 3 s no llega nada (algún proxy o antivirus los
// retiene), cambia a sondeo largo.
let modoSondeo = false;
function conectar(){
  const es = new EventSource('/eventos');
  let recibido = false;
  const plazo = setTimeout(() => { if (!recibido){ es.close(); modoSondeo = true; sondear(0); } }, 3000);
  es.onmessage = m => { recibido = true; $('#con').className = 'ok'; evento(JSON.parse(m.data)); };
  es.onerror = () => {
    $('#con').className = '';
    if (es.readyState === 2 && !modoSondeo){ clearTimeout(plazo); setTimeout(conectar, 3000); }
  };
}
async function sondear(desde){
  while (true){
    try {
      const r = await fetch('/sondeo?desde=' + desde, {cache: 'no-store'});
      const d = await r.json();
      $('#con').className = 'ok';
      d.ev.forEach(evento);
      desde = d.n;
    } catch (e){
      $('#con').className = '';
      await new Promise(f => setTimeout(f, 2000));
    }
  }
}
async function accion(a, q = ''){
  await fetch('/accion/' + a + q, {method: 'POST'});
}
document.querySelectorAll('button[data-a]').forEach(b => b.onclick = () => accion(b.dataset.a));
$('#auto').onchange = e => accion('auto', '?v=' + (e.target.checked ? 1 : 0));
conectar();
</script></body></html>"""
