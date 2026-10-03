# run_semanal.ps1
# ----------------
# Orquesta la cadencia SEMANAL del pipeline, pensado para Task Scheduler
# (trigger domingos, ~19:30 ART -- media hora antes que run_diario.ps1,
# para que listado_cedears.py termine de refrescar data/cedears_normalizados.csv
# antes de que la cadena diaria de ese mismo domingo lea el archivo).
#
# listado_cedears.py tiene que ir primero: analisis_fundamental_pesado.py
# (y todo lo demas) lee el CSV que este script genera.
#
# Grabamos a logs/ y reintentamos cada paso por el mismo motivo que
# run_diario.ps1: Task Scheduler no deja ver la salida de consola en ningun
# lado, y se confirmo un crash a nivel interprete ("Fatal Python error:
# PyEval_SaveThread") en la libreria de red de yfinance corriendo sin
# consola interactiva -- ver el comentario largo en run_diario.ps1. Como
# cada ticker queda cacheado en disco antes de fallar, un reintento retoma
# del cache.
#
# Correr a mano para probar: powershell -File run_semanal.ps1

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$env:REQUESTS_CA_BUNDLE = Join-Path $PSScriptRoot "combined_ca.pem"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("semanal_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

function Run-Paso {
    param([string]$Nombre, [string]$Script, [int]$MaxIntentos = 3)
    for ($intento = 1; $intento -le $MaxIntentos; $intento++) {
        "=== $Nombre intento $intento/$MaxIntentos ($(Get-Date -Format s)) ===" | Out-File -FilePath $logFile -Append -Encoding utf8
        & $python $Script 2>&1 | Out-File -FilePath $logFile -Append -Encoding utf8
        $exit = $LASTEXITCODE
        "=== $Nombre exit code: $exit ===" | Out-File -FilePath $logFile -Append -Encoding utf8
        if ($exit -eq 0) { return }
        if ($intento -lt $MaxIntentos) {
            "=== $Nombre fallo, reintentando en 10s (el cache en disco ya tiene lo que se alcanzo a bajar) ===" | Out-File -FilePath $logFile -Append -Encoding utf8
            Start-Sleep -Seconds 10
        }
    }
    "=== ${Nombre}: agotados los $MaxIntentos intentos, sigue fallando -- revisar $logFile ===" | Out-File -FilePath $logFile -Append -Encoding utf8
}

Run-Paso "listado_cedears" "listado_cedears.py"
Run-Paso "analisis_fundamental_pesado" "analisis_fundamental_pesado.py"
