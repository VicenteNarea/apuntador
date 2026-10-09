@echo off
cd /d %~dp0
where cloudflared >nul 2>nul
if errorlevel 1 if not exist "%ProgramFiles(x86)%\cloudflared\cloudflared.exe" if not exist "%ProgramFiles%\cloudflared\cloudflared.exe" if not exist "%LOCALAPPDATA%\Microsoft\WinGet\Links\cloudflared.exe" (
  echo Instalando cloudflared ^(solo la primera vez^)...
  winget install -e --id Cloudflare.cloudflared --accept-source-agreements --accept-package-agreements
)
call .venv\Scripts\activate
python asistente_llamadas.py --modo servicio --tunel
pause
