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
$env:SSL_CERT_FILE = Join-Path $PSScriptRoot "combined_ca.pem"
$env:CURL_CA_BUNDLE = Join-Path $PSScriptRoot "combined_ca.pem"
$env:PYTHONIOENCODING = "utf-8"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("semanal_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

# Cada paso corre en un proceso con su PROPIA consola oculta (Start-Process
# -WindowStyle Hidden), no con "& $python" que comparte la consola de este
# PowerShell. Visto el 11/10 y el 13/10: el crash de curl_cffi llega como
# evento de cierre de consola (exit code 0xC000013A) y, con la consola
# compartida, mataba tambien a este script -- el log se cortaba sin llegar
# a los reintentos. Con consola propia solo muere el paso y se reintenta.
# Probado con un crash real (access violation) y un evento de consola.
function Run-Paso {
    param([string]$Nombre, [string]$Script, [int]$MaxIntentos = 3)
    $salida = Join-Path $logDir "_tmp_$Nombre.out"
    $errores = Join-Path $logDir "_tmp_$Nombre.err"
    for ($intento = 1; $intento -le $MaxIntentos; $intento++) {
        "=== $Nombre intento $intento/$MaxIntentos ($(Get-Date -Format s)) ===" | Out-File -FilePath $logFile -Append -Encoding utf8
        $proc = Start-Process -FilePath $python -ArgumentList "`"$Script`"" -WorkingDirectory $PSScriptRoot `
            -WindowStyle Hidden -Wait -PassThru -RedirectStandardOutput $salida -RedirectStandardError $errores
        foreach ($f in @($salida, $errores)) {
            if (Test-Path $f) {
                Get-Content -Path $f -Encoding UTF8 | Out-File -FilePath $logFile -Append -Encoding utf8
                Remove-Item $f
            }
        }
        $exit = $proc.ExitCode
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
Run-Paso "consenso_analistas" "consenso_analistas.py"
