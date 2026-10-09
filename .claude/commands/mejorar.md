Implementa la siguiente mejora del proyecto: $ARGUMENTS

Si no indiqué cuál, toma la de mayor prioridad pendiente en la sección "Ideas para siguientes iteraciones" de CLAUDE.md y dime cuál elegiste antes de empezar.

Reglas:
- Respeta las restricciones de CLAUDE.md (100 % local, presupuesto de VRAM, latencia < 1,6 s, español).
- Mide antes y después con `herramientas\simular.py` si el cambio toca audio, STT o LLM.
- Agrega tests en `tests\` para la lógica nueva que no dependa de hardware y deja `pytest -q` en verde.
- Al terminar, actualiza en CLAUDE.md las secciones "Estado" e "Ideas" y el README si cambia el uso.
- Resume en una tabla qué cambió, la latencia antes y después, y qué debo probar yo manualmente.
