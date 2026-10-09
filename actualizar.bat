@echo off
cd /d %~dp0
echo Actualizando dependencias...
.venv\Scripts\python.exe -m pip install -r requirements.txt --quiet > actualizacion.txt 2>&1
.venv\Scripts\python.exe -c "import pptx; print('python-pptx OK')" >> actualizacion.txt 2>&1
.venv\Scripts\python.exe -m pytest -q >> actualizacion.txt 2>&1
type actualizacion.txt
echo.
echo Listo. Cierra el Apuntador y vuelve a abrir iniciar.bat
pause
