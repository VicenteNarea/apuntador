@echo off
cd /d %~dp0
set PYTHONIOENCODING=utf-8
echo Revisando dependencias...
.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet --disable-pip-version-check
echo Midiendo (1-2 minutos)...
.venv\Scripts\python.exe herramientas\comparar_latencia.py %* > comparacion.txt 2>&1
type comparacion.txt
pause
