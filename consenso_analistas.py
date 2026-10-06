"""
consenso_analistas.py
-----------------------
Precio objetivo y recomendaciones de Wall Street (`yf.Ticker.analyst_price_
targets` y `.recommendations`), vía Yahoo Finance. Cadencia SEMANAL (igual
que analisis_fundamental_pesado.py): los analistas no actualizan su precio
objetivo todos los días, pedirlo a diario sería desperdiciar llamadas sin
ganar nada.

Por qué importa: es una opinión EXTERNA para contrastar contra nuestro
propio screening (Lynch, comparables, técnico, salud financiera) -- todas
esas vistas son reglas propias sobre datos crudos; esto es lo que
realmente piensa Wall Street sobre cada empresa.

Cripto no participa (no tiene analistas de renta variable cubriéndola).
Un ticker sin cobertura de analistas no es un error: `analyst_price_
targets` devuelve solo `current` (el precio de hoy, sin estimaciones) y
`recommendations` viene vacío -- se guarda igual, con las columnas de
recomendación en NULL, no se descarta la fila.

Correr: python consenso_analistas.py
"""

import json
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import db

load_dotenv()

CACHE_DIR = Path("cache/consenso_analistas")
CACHE_DIAS = 7  # cadencia semanal, mismo motivo que analisis_fundamental_pesado.py
PAUSA_ENTRE_TICKERS = 1.2
DIAS_ENTRE_CORRIDAS = 6

VERSION_CACHE = 1


def _cache_path(ticker: str) -> Path:
    import re
    nombre_seguro = re.sub(r'[\\/*?:"<>|]', "_", ticker)
    return CACHE_DIR / f"{nombre_seguro}.json"


def _cache_vigente(path: Path) -> bool:
    if not path.exists():
        return False
    datos = json.loads(path.read_text(encoding="utf-8"))
    if datos.get("_version_cache") != VERSION_CACHE:
        return False
    edad_dias = (time.time() - path.stat().st_mtime) / 86400
    return edad_dias < CACHE_DIAS


def obtener_consenso(ticker: str) -> dict:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker)

    if _cache_vigente(path):
        return json.loads(path.read_text(encoding="utf-8"))

    datos = {"ticker": ticker, "_version_cache": VERSION_CACHE}
    try:
        t = yf.Ticker(ticker)

        pt = t.analyst_price_targets
        if pt:
            datos["precio_objetivo_actual"] = pt.get("current")
            datos["precio_objetivo_bajo"] = pt.get("low")
            datos["precio_objetivo_alto"] = pt.get("high")
            datos["precio_objetivo_mediana"] = pt.get("median")

        rec = t.recommendations
        if rec is not None and not rec.empty:
            fila_actual = rec[rec["period"] == "0m"]
            if not fila_actual.empty:
                f = fila_actual.iloc[0]
                datos["rec_strong_buy"] = int(f["strongBuy"])
                datos["rec_buy"] = int(f["buy"])
                datos["rec_hold"] = int(f["hold"])
                datos["rec_sell"] = int(f["sell"])
                datos["rec_strong_sell"] = int(f["strongSell"])
    except Exception as e:
        datos["error"] = str(e)

    path.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    time.sleep(PAUSA_ENTRE_TICKERS)
    return datos


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_reciente(con_check, "consenso_analistas", dias=DIAS_ENTRE_CORRIDAS)
    con_check.close()
    if ya_corrio:
        print(f"consenso_analistas ya corrio en los ultimos {DIAS_ENTRE_CORRIDAS} dias, no hace falta repetir.")
        return

    universo = pd.read_csv("data/cedears_normalizados.csv")
    universo = universo[universo["mercado"] != "cripto"].reset_index(drop=True)

    hoy = date.today()
    filas = []
    for i, fila_universo in enumerate(universo.itertuples(), 1):
        print(f"[{i}/{len(universo)}] {fila_universo.ticker_yahoo}", end="\r")
        datos = obtener_consenso(fila_universo.ticker_yahoo)
        filas.append({
            "ticker_usd": fila_universo.ticker_usd,
            "fecha": hoy,
            "precio_objetivo_actual": datos.get("precio_objetivo_actual"),
            "precio_objetivo_bajo": datos.get("precio_objetivo_bajo"),
            "precio_objetivo_alto": datos.get("precio_objetivo_alto"),
            "precio_objetivo_mediana": datos.get("precio_objetivo_mediana"),
            "rec_strong_buy": datos.get("rec_strong_buy"),
            "rec_buy": datos.get("rec_buy"),
            "rec_hold": datos.get("rec_hold"),
            "rec_sell": datos.get("rec_sell"),
            "rec_strong_sell": datos.get("rec_strong_sell"),
        })
    print()

    con = db.conectar()
    with db.registrar(con, "consenso_analistas") as log:
        db.upsert_fact_consenso_analistas(con, filas)
        log["filas_afectadas"] = len(filas)
    con.close()

    con_cobertura = sum(1 for f in filas if f["precio_objetivo_mediana"] is not None)
    print(f"fact_consenso_analistas actualizada ({len(filas)} filas, {con_cobertura} con precio objetivo, fecha {hoy})")


if __name__ == "__main__":
    main()
