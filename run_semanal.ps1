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
# Correr a mano para probar: powershell -File run_semanal.ps1

$ErrorActionPreference = "Continue"
Set-Location -Path $PSScriptRoot

$env:REQUESTS_CA_BUNDLE = Join-Path $PSScriptRoot "combined_ca.pem"

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

& $python "listado_cedears.py"
& $python "analisis_fundamental_pesado.py"
