$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot
$log = Join-Path $PSScriptRoot "instalacion.log"
Start-Transcript -Path $log -Force | Out-Null

function Paso($t) { Write-Host "`n===== $t =====" -ForegroundColor Cyan }
function RefrescarPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
$wingetArgs = @("--accept-source-agreements", "--accept-package-agreements", "--silent", "--disable-interactivity")

# 1. Python 3.11
Paso "1/7 Python 3.11"
$py = $null
try { $v = & py -3.11 -c "import sys;print(sys.executable)" 2>$null; if ($LASTEXITCODE -eq 0) { $py = $v.Trim() } } catch {}
if (-not $py) {
    $cand = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $cand) { $py = $cand }
}
if (-not $py) {
    Write-Host "Python 3.11 no encontrado. Instalando con winget..."
    winget install -e --id Python.Python.3.11 --scope user @wingetArgs
    RefrescarPath
    $cand = Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\python.exe"
    if (Test-Path $cand) { $py = $cand }
}
if (-not $py) { Write-Host "ERROR: no pude conseguir Python 3.11" -ForegroundColor Red; Stop-Transcript; exit 1 }
Write-Host "Python: $py"; & $py --version

# 2. Ollama
Paso "2/7 Ollama"
RefrescarPath
$ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source
if (-not $ollama) {
    $cand = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $cand) { $ollama = $cand }
}
if (-not $ollama) {
    Write-Host "Ollama no encontrado. Instalando con winget..."
    winget install -e --id Ollama.Ollama @wingetArgs
    RefrescarPath
    $cand = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $cand) { $ollama = $cand } else { $ollama = (Get-Command ollama -ErrorAction SilentlyContinue).Source }
}
if (-not $ollama) { Write-Host "ERROR: no pude conseguir Ollama" -ForegroundColor Red; Stop-Transcript; exit 1 }
Write-Host "Ollama: $ollama"
$vivo = $false
try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 3 | Out-Null; $vivo = $true } catch {}
if (-not $vivo) {
    Write-Host "Iniciando servidor de Ollama..."
    Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
    for ($i = 0; $i -lt 30 -and -not $vivo; $i++) {
        Start-Sleep 1
        try { Invoke-RestMethod http://localhost:11434/api/tags -TimeoutSec 2 | Out-Null; $vivo = $true } catch {}
    }
}
Write-Host "Servidor Ollama activo: $vivo"

# 3. Configuración de Claude Code del proyecto
Paso "3/7 Configuracion de Claude Code (.claude)"
if ((Test-Path "_claude") -and -not (Test-Path ".claude")) { Rename-Item "_claude" ".claude"; Write-Host "_claude renombrado a .claude" }
elseif (Test-Path ".claude") { Write-Host ".claude ya existe" }
$claude = (Get-Command claude -ErrorAction SilentlyContinue).Source
if ($claude) { Write-Host "Claude Code instalado: $claude" } else { Write-Host "AVISO: no encontre el comando 'claude' en el PATH" -ForegroundColor Yellow }

# 4. Entorno virtual y dependencias
Paso "4/7 Entorno virtual y dependencias (puede tardar varios minutos)"
if (-not (Test-Path ".venv\Scripts\python.exe")) { & $py -m venv .venv }
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
& $vpy -m pip install --upgrade pip --quiet
& $vpy -m pip install -r requirements-dev.txt --progress-bar off
if ($LASTEXITCODE -ne 0) { Write-Host "ERROR: fallo pip install" -ForegroundColor Red }
New-Item -ItemType Directory -Force -Path docs, pruebas_audio | Out-Null

# 5. Modelo LLM
Paso "5/7 Descargando qwen2.5:7b (~4,7 GB)"
& $ollama pull qwen2.5:7b

# 6. Tests
Paso "6/7 Tests"
& $vpy -m pytest -q

# 7. Diagnostico (descarga Whisper la primera vez)
Paso "7/7 Diagnostico"
& $vpy herramientas\diagnostico.py

# Git
$git = (Get-Command git -ErrorAction SilentlyContinue).Source
if ($git -and -not (Test-Path ".git")) {
    git init -q -b main; git add -A
    git -c user.name="Vicho" -c user.email="lamagiccomby@gmail.com" commit -qm "Apuntador de llamadas: version inicial"
    Write-Host "Repositorio git creado"
}

Paso "TERMINADO"
Stop-Transcript | Out-Null
Write-Host "Log en $log. Puedes cerrar esta ventana."
