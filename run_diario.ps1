# run_diario.ps1
# ---------------
# Orquesta la cadencia DIARIA del pipeline, pensado para Task Scheduler
# (trigger diario, ~20:00 ART). Cada script de abajo ya tiene su propia
# guarda de idempotencia (db.ya_corrio_hoy()), asi que un catch-up de Task
# Scheduler (PC apagada a la hora programada, corre al prenderla) no repite
# trabajo si ya corrio bien ese dia.
#
# $ErrorActionPreference = "Continue": si un paso falla (ej. Yahoo con rate
# limit en liviano), los siguientes igual se intentan -- cada script deja su
# propio registro en log_ejecuciones (ok/error), asi que una falla puntual
# no tapa al resto de la cadena. Revisar con "python estado_pipeline.py".
#
# Correr a mano para probar: powershell -File run_diario.ps1

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

# Mismo certificado combinado que se usa en las corridas manuales (ver
# Troubleshooting en README.md) -- se fija aca por las dudas de que no este
# en .env, no hace daño si ya esta.
$env:REQUESTS_CA_BUNDLE = Join-Path $PSScriptRoot "combined_ca.pem"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

& $python "precios_historicos.py"
& $python "analisis_fundamental_liviano.py"
& $python "macro_diario.py"
& $python "gold\lynch.py"
& $python "gold\comparables.py"
& $python "gold\macro_sensitivity.py"
