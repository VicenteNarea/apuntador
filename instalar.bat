@echo off
cd /d %~dp0
echo === Creando entorno virtual (Python 3.11 recomendado) ===
py -3.11 -m venv .venv 2>nul || python -m venv .venv
call .venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements-dev.txt
echo.
echo === Descargando modelo LLM en Ollama ===
ollama pull qwen2.5:7b
if not exist docs mkdir docs
if not exist pruebas_audio mkdir pruebas_audio
echo.
echo Listo. Ejecuta iniciar.bat
pause
