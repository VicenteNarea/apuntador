# Apuntador de llamadas (100 % local)

Escucha lo que suena en tu PC (la voz de los demás en Meet, Teams o Zoom), lo transcribe en la GPU, detecta preguntas y muestra una respuesta sugerida en una ventana flotante, usando tus documentos. Nada sale del equipo.

## Requisitos
- Windows 11 · NVIDIA RTX 3060 Ti (8 GB) con driver actualizado
- **Python 3.11** (python.org, marca "Add to PATH")
- **Ollama** (ollama.com/download)

## Instalación
1. Instala Python 3.11 y Ollama.
2. Doble clic en `instalar.bat` (crea el entorno, instala dependencias y descarga `qwen2.5:7b`, ~4,7 GB).
3. Pon tus archivos en `docs/` (PDF, DOCX, XLSX, CSV, TXT, MD).
4. Edita `contexto.txt` con lo que el modelo siempre debe saber.

## Uso
1. Abre Ollama (queda en la bandeja del sistema).
2. Doble clic en `iniciar.bat`. La primera vez descarga Whisper large-v3-turbo (~1,6 GB).
3. Entra a la llamada. **El audio de la llamada debe salir por el dispositivo de salida predeterminado de Windows.**

| Acción | Cómo |
|---|---|
| Responder a lo último dicho (aunque no parezca pregunta) | botón **Responder** o `Ctrl+Alt+R` |
| Pausar o reanudar | botón **Pausar** o `Ctrl+Alt+P` |
| Desactivar respuestas automáticas | casilla **Auto** |
| Agregar documentos en caliente | cópialos a `docs/` y pulsa **Recargar docs** |

**Preguntas con pausas:** si la persona hace una pausa corta en medio de la pregunta (menos de `CONTINUACION_S`), los pedazos que siguen se suman a la pregunta y la respuesta se vuelve a generar con la pregunta completa.

**Texto basura de Whisper:** con ruido (teclado, música, notificaciones) Whisper puede inventar palabras, siglas o hasta otro alfabeto. Se filtra con el detector de voz Silero (`WHISPER_VAD`), la confianza de cada segmento (`WHISPER_MIN_LOGPROB`, `WHISPER_MAX_COMPRESION`) y reglas sobre el texto (otros alfabetos, letras sueltas repetidas). Si se pierden frases reales, baja la exigencia: `WHISPER_MIN_LOGPROB = -1.3`.

**Varias preguntas a la vez:** si en una misma frase te hacen varias preguntas, o llegan seguidas (mientras se responde la anterior o con menos de `VENTANA_PREGUNTAS_S` segundos entre una y otra), se responden juntas, numeradas y en el mismo orden, hasta `MAX_PREGUNTAS`.

Las preguntas detectadas aparecen en amarillo. Arriba se muestra la latencia real (STT y tiempo hasta la primera palabra).

## Modos: normal, servicio y doble
| Modo | Qué hace | Cómo se abre |
|---|---|---|
| **Normal** | Solo la ventana en el PC | `iniciar.bat` |
| **Servicio** | Sin ventana ni consola en el PC: solo un ícono en la bandeja del sistema (junto a la hora). Se ve y controla desde la tablet o el celular | `iniciar_servicio.bat` |
| **Doble** | Ventana en el PC + panel web en la tablet, sincronizados | `iniciar_doble.bat` |

El panel web muestra lo mismo que la ventana: transcripción, sugerencia local, sugerencia de OpenAI y los botones.

- **Cómo entrar:** en la tablet (conectada al mismo wifi que el PC) abre `http://192.168.x.x:8765`. El enlace exacto aparece en la consola, arriba en la ventana (modo doble) y en `enlaces.txt`.
- La primera vez Windows pregunta si permite a Python en la red: acepta **Redes privadas**.
- No pide clave: cualquiera conectado a tu misma red wifi podría abrirlo.
- En modo servicio, el ícono de la bandeja tiene **Abrir panel**, **Ver enlace para la tablet** (abre `enlaces.txt`), **Pausar / reanudar** y **Salir**. Si algo falla, revisa `servicio.log`.
- Desde consola: `python asistente_llamadas.py --modo servicio|doble|normal`. Puerto: `WEB_PUERTO`.

## Latencia esperada (RTX 3060 Ti)
| Etapa | Tiempo |
|---|---|
| Detectar fin de frase (`SILENCIO_MS`) | 0,6 s |
| Whisper large-v3-turbo | 0,2–0,5 s |
| Búsqueda en documentos (BM25) | < 50 ms |
| Qwen 2.5 7B, primera palabra | 0,2–0,5 s |
| **Total hasta ver la respuesta** | **~1–1,6 s** |

## PC sin tarjeta NVIDIA (AMD, Intel)
Funciona igual, pero Whisper corre en el procesador: faster-whisper solo acelera con NVIDIA (CUDA). El programa lo detecta solo y usa `WHISPER_MODEL_CPU` (por defecto `small`), sin perder tiempo intentando CUDA. El diagnóstico lo muestra como **AVISO**, no como falla.

- Transcripción más lenta que en GPU: con un procesador moderno, ~0,5–1,5 s por frase con `small`. Si se siente lenta, usa `WHISPER_MODEL_CPU = "base"`; si el procesador es potente y quieres más precisión, `"medium"`.
- Ollama tiene su propio soporte para tarjetas AMD (Radeon RX 6000/7000 en Windows). Si la tarjeta no es compatible, usa el procesador y responde más lento.

## VRAM (8 GB)
Whisper turbo int8 (~1,5 GB) + Qwen 2.5 7B Q4 con contexto de 4k (~5,2 GB) ≈ **6,7 GB**. Si aparece un error de memoria o Ollama se vuelve lento, en `asistente_llamadas.py` cambia:
- `LLM_MODEL = "qwen2.5:3b"` (y ejecuta `ollama pull qwen2.5:3b`), o
- `WHISPER_MODEL = "small"`

## Ajustes (arriba en `asistente_llamadas.py`)
| Variable | Efecto |
|---|---|
| `SILENCIO_MS` | 400 es más rápido pero puede cortar frases; 800 es más estable |
| `PALABRAS_CLAVE` | Siglas y nombres propios de tu tema (MLP, ERPNext…): mejora la transcripción |
| `VAD_AGRESIVIDAD` | Súbelo a 3 si capta ruido o música como voz |
| `GUARDAR_TRANSCRIPCION` | `True` guarda el texto en `transcripciones/` |

## Problemas comunes
| Síntoma | Solución |
|---|---|
| "Whisper small · CPU (⚠ revisa CUDA)" | Faltan DLL de CUDA. Ejecuta `pip install --force-reinstall nvidia-cublas-cu12 "nvidia-cudnn-cu12==9.*"` dentro de `.venv` y actualiza el driver NVIDIA |
| No aparece transcripción | El audio de la llamada no sale por la salida predeterminada. En Teams/Zoom elige "Igual que el sistema" o cambia la predeterminada de Windows |
| "Ollama no responde" | Abre Ollama y verifica en una terminal: `ollama run qwen2.5:7b` |
| Atajos de teclado no funcionan | Ejecuta `iniciar.bat` como administrador |
| Frases como "Gracias por ver el video" | Son alucinaciones de Whisper con silencio; ya se filtran. Agrega otras en `_ALUCINACIONES` |

## Alcance
- Solo capta el audio del sistema (los demás). Tu micrófono no se transcribe, así que no responde a tus propias frases.
- Avisa a los participantes si usas transcripción automática en reuniones.

## Seguir desarrollando con Claude Code
El proyecto incluye `CLAUDE.md` (arquitectura, restricciones y hoja de ruta), tests, un diagnóstico y un simulador.

1. Abre una terminal en la carpeta del proyecto y ejecuta `claude`.
2. Comandos del proyecto:

| Comando | Qué hace |
|---|---|
| `/diagnostico` (o `/diagnostico --audio`) | Revisa Python, dependencias, CUDA, Ollama, VRAM y audio, y corrige lo que pueda |
| `/probar` | Corre los tests y simula una llamada con un WAV de `pruebas_audio/`, midiendo latencias |
| `/mejorar` | Implementa la siguiente mejora de la hoja de ruta (o la que indiques), con tests y medición |

Herramientas sueltas:
- `herramientas\diagnostico.py [--audio]`
- `herramientas\simular.py archivo.wav [--rapido]`
- `herramientas\comparar_latencia.py` (o `comparar.bat`): latencia del modelo local vs. OpenAI (`gpt-6-luna`). Requiere `OPENAI_API_KEY` en `.env`.
- `python -m pytest -q`
