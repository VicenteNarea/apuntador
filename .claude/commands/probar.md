Verifica que la app funcione de punta a punta.

1. Ejecuta los tests: `.venv\Scripts\python.exe -m pytest -q`. Si alguno falla, corrígelo antes de seguir.
2. Si hay un archivo WAV en `pruebas_audio\` (o el que indique en $ARGUMENTS), ejecuta `.venv\Scripts\python.exe herramientas\simular.py <wav>`. Si no hay ninguno, pídeme que grabe uno de 30–60 s con preguntas habladas (Grabadora de Windows → exportar o convertir a WAV PCM 16 bits) y guárdalo en `pruebas_audio\`.
3. Reporta en una tabla: frases detectadas, preguntas detectadas (y falsos positivos o negativos), STT promedio en ms, tiempo promedio hasta la primera palabra, y si alguna respuesta inventó datos que no estaban en `docs\`.
4. Si la latencia supera 1,6 s o hay errores de detección, propone ajustes concretos de configuración (`SILENCIO_MS`, `VAD_AGRESIVIDAD`, modelos) y pruébalos.
