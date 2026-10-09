@echo off
cd /d %~dp0
call .venv\Scripts\activate
python asistente_llamadas.py
pause
