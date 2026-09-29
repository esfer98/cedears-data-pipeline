"""
analisis_fundamental_liviano.py
--------------------------------
Parte DIARIA del analisis fundamental: solo pide el snapshot .info de Yahoo
(1 llamada por ticker) -- P/E, P/B, EV/EBITDA, margenes, ROE, ROA, deuda/
patrimonio, liquidez, dividendo, beta, tenencia insider/institucional,
market cap y shares outstanding. Estos ratios reflejan el precio de HOY
aunque la empresa no haya cambiado, asi que tiene sentido refrescarlos todos
los dias -- a diferencia de los estados contables (income statement, balance,
cashflow, earnings), que solo cambian ~4 veces al año y viven en
analisis_fundamental_pesado.py (cadencia semanal).

Tambien trae sector/industria/pais_origen (para dim_empresa) -- vienen en el
mismo .info, sin costo extra.

Usa "ticker_yahoo" de cedears_normalizados.csv (no "ticker_usd") porque IOL y
Yahoo Finance no siempre usan el mismo simbolo (ver TICKERS_YAHOO en
listado_cedears.py). Cripto no participa: P/E, márgenes, etc. no tienen
sentido para BTC/ETH/SOL.

Yahoo banea (429) si le pegas a muchos tickers seguido sin pausa, asi que
cada ticker se cachea en disco: si el proceso corta a mitad de camino, se
puede volver a correr y retoma sin re-pedir lo que ya bajo.

Correr: python analisis_fundamental_liviano.py
"""

import json
import re
import time
from datetime import date
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import db

load_dotenv()

CACHE_DIR = Path("cache/fundamentals_liviano")
CACHE_DIAS = 1  # cadencia diaria: no repetir el mismo dia, pero refrescar todos los dias
PAUSA_ENTRE_TICKERS = 1.0  # segundos -- 1 sola llamada por ticker, menos presion que el pesado

# Sube este numero cada vez que obtener_info() empiece a guardar un campo
# nuevo: invalida el cache viejo (que no lo tiene) sin borrar la carpeta a mano.
VERSION_CACHE = 1


def _cache_path(ticker: str) -> Path:
    """Windows no admite *, /, \\, :, ?, ", <, > ni | en nombres de archivo
    (ej. el simbolo real 'BKC*' de IOL)."""
    nombre_seguro = re.sub(r'[\\/*?:"<>|]', "_", ticker)
    return CACHE_DIR / f"{nombre_seguro}.json"


# Los que terminan en "_%" ya vienen como fraccion (0.478) en yfinance y se
# multiplican x100; dividendYield ya viene en % (no fraccion).
CAMPOS_INFO_FRACCION = {
    "grossMargins": "margen_bruto_%",
    "operatingMargins": "margen_operativo_%",
    "profitMargins": "margen_neto_%",
    "returnOnEquity": "roe_%",
    "returnOnAssets": "roa_%",
    "payoutRatio": "payout_ratio_%",
    "heldPercentInsiders": "insider_holding_%",
    "heldPercentInstitutions": "institutional_holding_%",
}
CAMPOS_INFO_DIRECTOS = {
    "marketCap": "market_cap",
    "enterpriseValue": "enterprise_value",
    "trailingPE": "pe_trailing",
    "forwardPE": "pe_forward",
    "trailingPegRatio": "peg_ratio",
    "priceToBook": "price_to_book",
    "enterpriseToEbitda": "ev_ebitda",
    "debtToEquity": "deuda_patrimonio",
    "currentRatio": "razon_corriente",
    "dividendYield": "dividend_yield_%",
    "beta": "beta",
    "sharesOutstanding": "shares_outstanding",
}
# Estos van a dim_empresa (descriptivos), no a fact_metrics_daily.
CAMPOS_INFO_DIMENSION = {"sector": "sector", "industry": "industria", "country": "pais_origen"}

# calcular_fila() arma columnas con "%" (para que se lean bien en consola);
# DuckDB las guarda con sufijo "_pct" (SQL no admite "%" en nombres de
# columna). Este dict es la unica fuente de verdad de esa traduccion.
COLUMNAS_A_DB = {
    "market_cap": "market_cap",
    "enterprise_value": "enterprise_value",
    "pe_trailing": "pe_trailing",
    "pe_forward": "pe_forward",
    "peg_ratio": "peg_ratio",
    "price_to_book": "price_to_book",
    "ev_ebitda": "ev_ebitda",
    "margen_bruto_%": "margen_bruto_pct",
    "margen_operativo_%": "margen_operativo_pct",
    "margen_neto_%": "margen_neto_pct",
    "roe_%": "roe_pct",
    "roa_%": "roa_pct",
    "deuda_patrimonio": "deuda_patrimonio",
    "razon_corriente": "razon_corriente",
    "dividend_yield_%": "dividend_yield_pct",
    "payout_ratio_%": "payout_ratio_pct",
    "beta": "beta",
    "insider_holding_%": "insider_holding_pct",
    "institutional_holding_%": "institutional_holding_pct",
    "shares_outstanding": "shares_outstanding",
}


def _cache_vigente(path: Path) -> bool:
    if not path.exists():
        return False
    datos = json.loads(path.read_text(encoding="utf-8"))
    if datos.get("_version_cache") != VERSION_CACHE:
        return False
    edad_dias = (time.time() - path.stat().st_mtime) / 86400
    return edad_dias < CACHE_DIAS


def obtener_info(ticker: str) -> dict:
    """Snapshot .info de Yahoo (1 llamada), con cache en disco de 1 dia."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker)

    if _cache_vigente(path):
        return json.loads(path.read_text(encoding="utf-8"))

    datos = {"ticker": ticker, "_version_cache": VERSION_CACHE}
    try:
        info = yf.Ticker(ticker).get_info() or {}
        campos_a_guardar = {**CAMPOS_INFO_FRACCION, **CAMPOS_INFO_DIRECTOS, **CAMPOS_INFO_DIMENSION}
        datos["info"] = {campo: info.get(campo) for campo in campos_a_guardar}
    except Exception as e:
        datos["error"] = str(e)

    path.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    time.sleep(PAUSA_ENTRE_TICKERS)
    return datos


def calcular_fila(datos: dict) -> dict:
    fila = {"ticker": datos.get("ticker")}
    info = datos.get("info", {})

    for campo, nombre_salida in CAMPOS_INFO_FRACCION.items():
        valor = info.get(campo)
        fila[nombre_salida] = round(valor * 100, 1) if valor is not None else None
    for campo, nombre_salida in CAMPOS_INFO_DIRECTOS.items():
        valor = info.get(campo)
        fila[nombre_salida] = round(valor, 2) if isinstance(valor, (int, float)) else valor
    for campo, nombre_salida in CAMPOS_INFO_DIMENSION.items():
        fila[nombre_salida] = info.get(campo)

    if "error" in datos:
        fila["error"] = datos["error"]

    return fila


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "analisis_fundamental_liviano")
    con_check.close()
    if ya_corrio:
        print("analisis_fundamental_liviano ya corrio hoy con exito, no hace falta repetir.")
        return

    universo = pd.read_csv("data/cedears_normalizados.csv")
    universo = universo[universo["mercado"] != "cripto"].reset_index(drop=True)

    filas = []
    for i, fila_universo in enumerate(universo.itertuples(), 1):
        print(f"[{i}/{len(universo)}] {fila_universo.ticker_yahoo}", end="\r")
        datos = obtener_info(fila_universo.ticker_yahoo)
        fila = calcular_fila(datos)
        fila["ticker"] = fila_universo.ticker_usd  # el simbolo que se ve en IOL
        filas.append(fila)
    print()

    hoy = date.today()
    filas_metrics = [
        {
            "ticker_usd": f["ticker"],
            "fecha": hoy,
            **{COLUMNAS_A_DB[col]: f[col] for col in COLUMNAS_A_DB if col in f},
        }
        for f in filas
    ]
    filas_dim = universo.merge(
        pd.DataFrame(filas)[["ticker", "sector", "industria", "pais_origen"]],
        left_on="ticker_usd", right_on="ticker",
    ).to_dict("records")

    con = db.conectar()
    with db.registrar(con, "analisis_fundamental_liviano") as log:
        db.upsert_fact_metrics_daily(con, filas_metrics)
        db.upsert_dim_empresa(con, filas_dim)
        log["filas_afectadas"] = len(filas_metrics)
    con.close()
    print(f"fact_metrics_daily (liviano) actualizada ({len(filas_metrics)} filas, fecha {hoy})")


if __name__ == "__main__":
    main()
