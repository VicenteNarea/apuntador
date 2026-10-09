Ejecuta el diagnóstico completo del entorno y explícame el resultado.

1. Si no existe `.venv`, avísame que primero debo ejecutar `instalar.bat` y detente.
2. Ejecuta `.venv\Scripts\python.exe herramientas\diagnostico.py $ARGUMENTS` (agrega `--audio` si lo pedí; en ese caso recuérdame reproducir algo con voz).
3. Por cada FALLA: identifica la causa, propone la solución concreta y, si es algo que puedes arreglar tú (instalar un paquete, descargar un modelo con `ollama pull`, corregir código), hazlo y vuelve a ejecutar el diagnóstico.
4. Termina con una tabla: componente, estado, latencia o VRAM medida, y acción pendiente para mí si la hay.
