"""
macro_diario.py
----------------
Serie de tiempo de indicadores macro (tasas, riesgo, dolar, commodities,
indices de referencia), via Yahoo Finance. Mismo motivo que
precios_historicos.py -- es una serie de tiempo, no un snapshot -- pero
separado porque el grano es distinto: una fila por SERIE y dia, no por
empresa y dia (ver fact_macro_daily en db.py).

Por que estas series y no otras: cada una explica un mecanismo concreto que
afecta el precio de las acciones del universo, no solo "informacion de mas".

  Tasas (curva completa, no un solo punto):
    UST3M, UST5Y, UST10Y, UST30Y -- a mayor tasa, mayor descuento de
    flujos futuros -> multiplos (P/E) mas bajos, mas fuerte en las
    empresas de "Alto Crecimiento" (gold_lynch).

  Apetito por riesgo:
    VIX      -- volatilidad implicita, el termometro clasico de risk-on/off.
    HYG/LQD  -- ETFs de bonos high-yield vs investment-grade; el spread entre
                ambos es un proxy de estres crediticio (el spread real de
                mercado no esta gratis en Yahoo).

  Dolar / FX:
    DXY      -- dolar contra una canasta de monedas.
    USDBRL   -- pega directo en las BDRs brasileñas del universo (ITUB3.SA,
                BBAS3.SA, etc.): dolar fuerte = real mas debil = BDR mas cara
                en pesos sin que la empresa haya cambiado nada.
    USDARS   -- dolar oficial (no CCL/MEP -- eso no esta gratis en Yahoo,
                queda para una fuente futura si hace falta).

  Commodities (pegan directo en las mineras/petroleras/agro del universo):
    WTI    -- XOM, CVX, PBR, YPFD, GPRK.
    GOLD   -- GFI, HMY, KGC, PAAS, HL, MUX.
    COPPER -- GGB, VALE, FCX, SCCO.

  Indices de referencia (para medir rendimiento relativo, no solo nivel):
    SP500, MERVAL, BOVESPA.

Yahoo devuelve la barra "de hoy" con Close=NaN antes de que cierre el
mercado (mismo comportamiento que se vio en fact_precios_daily) -- se
descarta directo aca, asi la tabla nunca tiene filas con valor NULL para
quien la consuma despues.

Correr: python macro_diario.py
"""

import time
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import db

load_dotenv()

CACHE_DIR = Path("cache/macro")
PERIODO = "5y"
PAUSA_ENTRE_SERIES = 1.0  # segundos, para no gatillar el rate limit de Yahoo

# codigo propio -> (ticker de Yahoo, descripcion)
SERIES = {
    "UST3M":   ("^IRX",     "Tasa Treasury 3 meses (%)"),
    "UST5Y":   ("^FVX",     "Tasa Treasury 5 años (%)"),
    "UST10Y":  ("^TNX",     "Tasa Treasury 10 años (%)"),
    "UST30Y":  ("^TYX",     "Tasa Treasury 30 años (%)"),
    "VIX":     ("^VIX",     "Indice de volatilidad implicita S&P500"),
    "HYG":     ("HYG",      "ETF bonos high-yield (proxy riesgo credito)"),
    "LQD":     ("LQD",      "ETF bonos investment-grade (proxy riesgo credito)"),
    "DXY":     ("DX-Y.NYB", "Indice dolar (DXY)"),
    "USDBRL":  ("USDBRL=X", "Dolar vs Real brasileño"),
    "USDARS":  ("USDARS=X", "Dolar oficial vs Peso argentino"),
    "WTI":     ("CL=F",     "Petroleo WTI ($/barril)"),
    "GOLD":    ("GC=F",     "Oro ($/onza)"),
    "COPPER":  ("HG=F",     "Cobre ($/libra)"),
    "SP500":   ("^GSPC",    "Indice S&P500"),
    "MERVAL":  ("^MERV",    "Indice Merval (Argentina)"),
    "BOVESPA": ("^BVSP",    "Indice Bovespa (Brasil)"),
}


def obtener_serie(codigo: str, ticker_yahoo: str) -> pd.DataFrame:
    """Historico diario de una serie macro, con cache en disco (misma logica
    de 1-dia que precios_historicos.obtener_precios)."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{codigo}.csv"

    if path.exists() and pd.Timestamp.fromtimestamp(path.stat().st_mtime).date() == pd.Timestamp.today().date():
        return pd.read_csv(path, parse_dates=["Date"])

    historico = yf.Ticker(ticker_yahoo).history(period=PERIODO, auto_adjust=False)
    historico = historico.reset_index()[["Date", "Close"]]
    historico.to_csv(path, index=False)
    time.sleep(PAUSA_ENTRE_SERIES)
    return historico


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "macro_diario")
    con_check.close()
    if ya_corrio:
        print("macro_diario ya corrio hoy con exito, no hace falta repetir.")
        return

    series = []
    for i, (codigo, (ticker_yahoo, descripcion)) in enumerate(SERIES.items(), 1):
        print(f"[{i}/{len(SERIES)}] {codigo} ({ticker_yahoo})", end="\r")
        try:
            historico = obtener_serie(codigo, ticker_yahoo)
        except Exception as e:
            print(f"\n{codigo} ({ticker_yahoo}): {e}")
            continue
        if historico.empty:
            continue
        historico = historico.dropna(subset=["Close"]).copy()
        historico["serie"] = codigo
        series.append(historico)
    print()

    if not series:
        print("No se pudo bajar ninguna serie macro.")
        return

    todo = pd.concat(series, ignore_index=True)
    # utc=True: mismo motivo que precios_historicos (el rango de 5 años cruza
    # cambios de horario y algunas series traen offsets mixtos).
    todo["fecha"] = pd.to_datetime(todo["Date"], utc=True).dt.date
    todo = todo.rename(columns={"Close": "valor"})[["serie", "fecha", "valor"]]

    con = db.conectar()
    with db.registrar(con, "macro_diario") as log:
        db.upsert_fact_macro_daily(con, todo)
        log["filas_afectadas"] = len(todo)
    con.close()
    print(f"fact_macro_daily actualizada ({len(todo)} filas, {todo['serie'].nunique()} series)")


if __name__ == "__main__":
    main()
