"""
precios_historicos.py
----------------------
Precios diarios (Open/High/Low/Close/Adj Close/Volume) de las empresas de
EE.UU. y BDRs detras de los CEDEARs, via Yahoo Finance.

Separado de analisis_fundamental_liviano.py/_pesado.py porque esto es una
serie de tiempo (para graficos, retornos, volatilidad) y lo otro es un
snapshot/momentum de resultados. Distinta naturaleza, distinto script.

Adj Close es el dato clave para calcular retornos reales: ya viene corregido
por splits y dividendos, a diferencia de Close.

Correr: python precios_historicos.py
"""

import time
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import db

load_dotenv()

CACHE_DIR = Path("cache/precios")
PERIODO = "5y"
PAUSA_ENTRE_TICKERS = 1.5  # segundos, para no gatillar el rate limit de Yahoo


def obtener_precios(ticker: str) -> pd.DataFrame:
    """Precios diarios de los ultimos PERIODO años, con cache en disco.
    Si el archivo ya se actualizo hoy, no vuelve a pedirlo a Yahoo."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{ticker.replace('/', '_')}.csv"

    if path.exists() and pd.Timestamp.fromtimestamp(path.stat().st_mtime).date() == pd.Timestamp.today().date():
        return pd.read_csv(path, parse_dates=["Date"])

    historico = yf.Ticker(ticker).history(period=PERIODO, auto_adjust=False)
    historico = historico.reset_index()[["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]]
    historico.to_csv(path, index=False)
    time.sleep(PAUSA_ENTRE_TICKERS)
    return historico


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "precios_historicos")
    con_check.close()
    if ya_corrio:
        print("precios_historicos ya corrio hoy con exito, no hace falta repetir.")
        return

    universo = pd.read_csv("data/cedears_normalizados.csv")

    series = []
    for i, fila in enumerate(universo.itertuples(), 1):
        print(f"[{i}/{len(universo)}] {fila.ticker_yahoo}", end="\r")
        try:
            precios = obtener_precios(fila.ticker_yahoo)
        except Exception as e:
            print(f"\n{fila.ticker_yahoo}: {e}")
            continue
        if precios.empty:
            continue
        precios = precios.copy()
        precios["ticker"] = fila.ticker_usd
        precios["nombre_empresa"] = fila.nombre_empresa
        series.append(precios)
    print()

    todo = pd.concat(series, ignore_index=True)

    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)
    destino = out_dir / "precios_historicos.csv"
    todo.to_csv(destino, index=False)
    print(f"Guardado en {destino} ({todo['ticker'].nunique()} tickers, {len(todo)} filas)")

    precios_db = todo.rename(columns={
        "ticker": "ticker_usd", "Open": "open", "High": "high", "Low": "low",
        "Close": "close", "Adj Close": "adj_close", "Volume": "volume",
    })
    # utc=True porque el rango de 5 años cruza cambios de horario (EDT/EST) y
    # las fechas quedan con offsets mixtos; a nosotros solo nos importa el dia.
    precios_db["fecha"] = pd.to_datetime(precios_db["Date"], utc=True).dt.date

    con = db.conectar()
    with db.registrar(con, "precios_historicos") as log:
        db.upsert_fact_precios_daily(con, precios_db)
        log["filas_afectadas"] = len(precios_db)
    con.close()
    print(f"fact_precios_daily actualizada ({len(precios_db)} filas)")


if __name__ == "__main__":
    main()
