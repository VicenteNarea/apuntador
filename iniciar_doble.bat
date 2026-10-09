@echo off
cd /d %~dp0
call .venv\Scripts\activate
python asistente_llamadas.py --modo doble
pause
