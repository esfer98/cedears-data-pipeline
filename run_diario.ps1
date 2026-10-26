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
# en .env, no hace daño si ya esta. huggingface_hub (sentimiento_noticias.py)
# usa otra libreria HTTP que no respeta REQUESTS_CA_BUNDLE -- hacen falta
# tambien SSL_CERT_FILE/CURL_CA_BUNDLE, no hacen daño para el resto de los pasos.
$env:REQUESTS_CA_BUNDLE = Join-Path $PSScriptRoot "combined_ca.pem"
$env:SSL_CERT_FILE = Join-Path $PSScriptRoot "combined_ca.pem"
$env:CURL_CA_BUNDLE = Join-Path $PSScriptRoot "combined_ca.pem"
$env:HF_HUB_CACHE = Join-Path $PSScriptRoot ".cache_hf"
$env:PYTHONIOENCODING = "utf-8"  # la salida va a archivo: sin esto Python escribe en cp1252 y los acentos llegan rotos al log

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logFile = Join-Path $logDir ("diario_{0}.log" -f (Get-Date -Format "yyyy-MM-dd_HHmmss"))

# Cada paso corre en un proceso con su PROPIA consola oculta (Start-Process
# -WindowStyle Hidden), no con "& $python" que comparte la consola de este
# PowerShell. Visto el 11/10 y el 13/10: el crash de curl_cffi llega como
# evento de cierre de consola (exit code 0xC000013A) y, con la consola
# compartida, mataba tambien a este script -- el log se cortaba sin llegar
# a los reintentos. Con consola propia solo muere el paso y se reintenta.
# Probado con un crash real (access violation) y un evento de consola.
function Run-Paso {
    param([string]$Nombre, [string]$Script, [string]$Argumentos = "", [int]$MaxIntentos = 3)
    $salida = Join-Path $logDir "_tmp_$Nombre.out"
    $errores = Join-Path $logDir "_tmp_$Nombre.err"
    for ($intento = 1; $intento -le $MaxIntentos; $intento++) {
        "=== $Nombre intento $intento/$MaxIntentos ($(Get-Date -Format s)) ===" | Out-File -FilePath $logFile -Append -Encoding utf8
        $proc = Start-Process -FilePath $python -ArgumentList "`"$Script`" $Argumentos" -WorkingDirectory $PSScriptRoot `
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

Run-Paso "precios_historicos" "precios_historicos.py"
Run-Paso "analisis_fundamental_liviano" "analisis_fundamental_liviano.py"
Run-Paso "macro_diario" "macro_diario.py"
Run-Paso "dolar_argentina" "dolar_argentina.py"
Run-Paso "sentimiento_noticias" "sentimiento_noticias.py"
Run-Paso "silver_calidad_datos" "silver\calidad_datos.py"
Run-Paso "gold_lynch" "gold\lynch.py"
Run-Paso "gold_comparables" "gold\comparables.py"
Run-Paso "gold_macro_sensitivity" "gold\macro_sensitivity.py"
Run-Paso "gold_technical" "gold\technical.py"
Run-Paso "gold_ondas" "gold\ondas.py"
Run-Paso "gold_refugio" "gold\refugio.py"
Run-Paso "gold_valuacion_historica" "gold\valuacion_historica.py"
Run-Paso "gold_insiders" "gold\insiders.py"
Run-Paso "gold_salud_financiera" "gold\salud_financiera.py"
Run-Paso "gold_sentimiento" "gold\sentimiento.py"
# Guarda una cartera por semana (la primera corrida de cada semana; las demas no hacen nada).
Run-Paso "cartera_semanal" "seleccionar_cartera.py" "--guardar=semanal"
Run-Paso "evaluar_carteras" "evaluar_carteras.py"
Run-Paso "monitoreo_cartera" "monitoreo_cartera.py"

# Aviso visible si el monitoreo encontro algo: un reporte que nadie abre es lo
# mismo que no tenerlo (asi nos enteramos tarde de la caida de SK Hynix).
$alertas = Join-Path $PSScriptRoot "reportes\alertas_hoy.txt"
if (Test-Path $alertas) {
    try {
        [Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
        $xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $textos = $xml.GetElementsByTagName("text")
        $textos.Item(0).AppendChild($xml.CreateTextNode("CEDEARs: revisar cartera")) | Out-Null
        $textos.Item(1).AppendChild($xml.CreateTextNode((Get-Content $alertas -Raw -Encoding UTF8))) | Out-Null
        $appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
    } catch {
        "=== no se pudo mostrar la notificacion: $_ ===" | Out-File -FilePath $logFile -Append -Encoding utf8
    }
}
