Set-Location -Path $PSScriptRoot
$out = Join-Path $PSScriptRoot "verificacion.txt"
$vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
$env:PYTHONIOENCODING = "utf-8"
function Log($t) { $t | Out-File -FilePath $out -Append -Encoding utf8; Write-Host $t }
"" | Out-File -FilePath $out -Encoding utf8
Log "===== ollama list ====="
& $ollama list 2>&1 | ForEach-Object { Log "$_" }
$tiene = (& $ollama list 2>&1 | Out-String) -match "qwen2.5:7b"
if (-not $tiene) {
    Log "===== ollama pull qwen2.5:7b ====="
    & $ollama pull qwen2.5:7b 2>&1 | Select-Object -Last 3 | ForEach-Object { Log "$_" }
    & $ollama list 2>&1 | ForEach-Object { Log "$_" }
}
Log "===== nvidia-smi ====="
& nvidia-smi --query-gpu=name,memory.used,memory.total,driver_version --format=csv 2>&1 | ForEach-Object { Log "$_" }
Log "===== pytest ====="
& $vpy -m pytest -q 2>&1 | ForEach-Object { Log "$_" }
Log "===== diagnostico ====="
& $vpy herramientas\diagnostico.py 2>&1 | ForEach-Object { Log "$_" }
Log "===== git ====="
& git log --oneline 2>&1 | ForEach-Object { Log "$_" }
Log "===== FIN ====="
