@echo off
cd /d %~dp0
.venv\Scripts\python.exe -c "import pystray, PIL" 2>nul || (
  echo Instalando icono de bandeja ^(solo la primera vez^)...
  .venv\Scripts\python.exe -m pip install --quiet pystray pillow
)
start "" .venv\Scripts\pythonw.exe asistente_llamadas.py --modo servicio --oculto
