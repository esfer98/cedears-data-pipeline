# run_diario.ps1
# ---------------
# Orquesta la cadencia DIARIA del pipeline, pensado para Task Scheduler
# (trigger diario, ~20:00 ART). Cada script de abajo ya tiene su propia
# guarda de idempotencia (db.ya_corrio_hoy()), asi que un catch-up de Task
# Scheduler (PC apagada a la hora programada, corre al prenderla) no repite
# trabajo si ya corrio bien ese dia.
#
# $ErrorActionPreference = "Continue": si un paso falla, los siguientes
# igual se intentan -- una falla puntual no tapa al resto de la cadena.
#
# Por que grabamos a logs/: Task Scheduler no muestra la salida de consola
# en ningun lado (ni siquiera en el Visor de eventos, que viene deshabilitado
# para esto por defecto). La primera vez que corrio solo via Task Scheduler
# (02/10), analisis_fundamental_liviano/precios_historicos/macro_diario NO
# dejaron registro en log_ejecuciones -- significa que crashearon ANTES de
# llegar a db.registrar(), y sin logs/ no habia forma de saber por que.
#
# Por que cada paso reintenta: investigando con logs/ se vio la causa real
# -- "Fatal Python error: PyEval_SaveThread" en precios_historicos.py, un
# crash a nivel interprete (no una excepcion comun, por eso ningun
# try/except de Python lo agarra) dentro de la libreria de red que usa
# yfinance (curl_cffi) cuando corre sin consola interactiva, tipico de
# Task Scheduler. Pasa de forma intermitente despues de varios cientos de
# tickers (condicion de carrera rara en la libreria nativa, no en nuestro
# codigo) -- se confirmo reproduciendolo dos veces en el mismo lugar.
# Como cada ticker ya queda cacheado en disco ANTES de fallar, un reintento
# retoma del cache (no vuelve a pedir lo que ya bajo) y tiene buenas chances
# de terminar en el segundo intento.
#
# Correr a mano para probar: powershell -File run_diario.ps1

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

# Mismo certificado combinado que se usa en las corridas manuales (ver
# Troubleshooting en README.md) -- se fija aca por las dudas de que no este
# en .env, no hace daño si ya esta.
$env:REQUESTS_CA_BUNDLE = Join-Path $PSScriptRoot "combined_ca.pem"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("diario_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

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

Run-Paso "precios_historicos" "precios_historicos.py"
Run-Paso "analisis_fundamental_liviano" "analisis_fundamental_liviano.py"
Run-Paso "macro_diario" "macro_diario.py"
Run-Paso "dolar_argentina" "dolar_argentina.py"
Run-Paso "gold_lynch" "gold\lynch.py"
Run-Paso "gold_comparables" "gold\comparables.py"
Run-Paso "gold_macro_sensitivity" "gold\macro_sensitivity.py"
Run-Paso "gold_technical" "gold\technical.py"
Run-Paso "gold_salud_financiera" "gold\salud_financiera.py"
