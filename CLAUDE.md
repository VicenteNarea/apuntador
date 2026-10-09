# Apuntador de llamadas — contexto para Claude Code

App de escritorio **100 % local** para Windows 11. Escucha el audio del sistema (la voz de los demás en Meet, Teams o Zoom), lo transcribe en la GPU, detecta preguntas y muestra una respuesta sugerida en una ventana flotante, usando documentos propios. Interfaz, mensajes y comentarios en **español**.

## Entorno del usuario
- Windows 11 · NVIDIA **RTX 3060 Ti (8 GB VRAM)** · Python **3.11** en `.venv\`
- Ollama corriendo en `localhost:11434` con `qwen2.5:7b`
- Shell: los `.bat` usan cmd. Para comandos, usa `.venv\Scripts\python.exe` (no el Python global)

## Restricciones (no negociables)
- **Nada sale del equipo.** La única conexión de red permitida en tiempo de ejecución es Ollama en localhost (y la descarga inicial de modelos). No agregues APIs en la nube.
- **Presupuesto de VRAM ~7 GB:** Whisper large-v3-turbo int8 (~1,5 GB) + Qwen 2.5 7B Q4 con 4k de contexto (~5,2 GB). Cualquier modelo nuevo (por ejemplo, embeddings) debe caber o correr en CPU.
- **Latencia objetivo:** < 1,6 s desde el fin de la frase hasta la primera palabra. Mídela antes y después de cada cambio que toque el pipeline (`herramientas/simular.py`).
- Solo se captura el audio del sistema, no el micrófono, salvo que el usuario lo pida.

## Arquitectura (`asistente_llamadas.py`, un solo archivo)
```
CapturaAudio (WASAPI loopback, callback) → audio_q
  → Segmentador (hilo, WebRTC VAD 30 ms, cierra frase tras SILENCIO_MS) → frase_q
  → Transcriptor (hilo, faster-whisper CUDA) → App.on_texto → es_pregunta()
  → Respondedor (hilo, Ollama /api/chat streaming; descarta solicitudes viejas por contador gen)
     usa BaseConocimiento (BM25Plus sobre docs/) + contexto.txt
  → ui_q → Interfaz (tkinter, hilo principal, polling cada 40 ms)
```
- Toda la configuración está en constantes al inicio del archivo.
- Los hilos solo se comunican por colas; la UI solo se toca desde el hilo principal.
- `App(capturar=False)` + `app.iniciar(hotkeys=False)` permite correr sin audio real ni ventana (lo usan el simulador y los tests).
- WASAPI loopback no entrega paquetes cuando no suena nada: el Segmentador trata el timeout de la cola como silencio.
- `_registrar_dlls_cuda()` agrega al PATH las DLL de `nvidia-cublas-cu12` y `nvidia-cudnn-cu12` instaladas por pip. Si falla, cae a CPU con el modelo `small` y lo avisa en la UI.

## Comandos
| Tarea | Comando |
|---|---|
| Instalar | `instalar.bat` |
| Ejecutar | `iniciar.bat` o `.venv\Scripts\python.exe asistente_llamadas.py` |
| Diagnóstico completo (CUDA, Ollama, audio, VRAM) | `.venv\Scripts\python.exe herramientas\diagnostico.py` (`--audio` captura 3 s del sistema) |
| Prueba de punta a punta con un WAV, sin llamada | `.venv\Scripts\python.exe herramientas\simular.py ruta\audio.wav` |
| Tests (no requieren GPU, audio ni Ollama) | `.venv\Scripts\python.exe -m pytest -q` |

Comandos de proyecto en Claude Code: `/diagnostico`, `/probar`, `/mejorar`.

## Flujo de trabajo esperado
1. Antes de modificar, ejecuta los tests y el diagnóstico para conocer el estado real.
2. Después de cada cambio: `pytest -q`. Si tocaste audio, STT o LLM, ejecuta también `simular.py` y compara la latencia.
3. Funciones nuevas que no dependan de hardware → agrega tests en `tests/`.
4. No cambies los modelos por defecto sin verificar la VRAM con `nvidia-smi` mientras la app corre.

## Estado
**Hecho y probado en Linux (lógica):** detección de preguntas, filtro de alucinaciones, troceo e indexación de PDF/DOCX/XLSX/CSV/TXT/MD, búsqueda BM25Plus y segmentación VAD con audio sintético.
**Pendiente de validar en el PC del usuario:** captura de loopback, carga de CUDA, latencia real, ventana tkinter y atajos de teclado.

## Ideas para siguientes iteraciones (por prioridad)
1. Validar el funcionamiento real en Windows y ajustar `SILENCIO_MS` y `VAD_AGRESIVIDAD` con audio de una llamada.
2. Ocultar la ventana al compartir pantalla (`SetWindowDisplayAffinity` con `WDA_EXCLUDEFROMCAPTURE` vía ctypes).
3. Transcripción parcial en streaming: enviar al LLM antes de que termine la frase si ya es claramente una pregunta.
4. Búsqueda híbrida: embeddings multilingües en CPU (por ejemplo, `intfloat/multilingual-e5-small` con onnxruntime) combinados con BM25.
5. Captura opcional del micrófono para registrar la conversación completa (sin responder a las propias frases).
6. Empaquetar como `.exe` con PyInstaller.
