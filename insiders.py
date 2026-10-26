"""
insiders.py
------------
Compras y ventas de directivos (formulario 4 de la SEC, via Yahoo), cadencia
semanal -> fact_insider_transacciones.

Solo las empresas que reportan en EE.UU. (mercado usa_otros): los ADR
extranjeros casi no traen datos (TSMC: 5 filas en 2026) y el panel local
argentino y las BDR no tienen formulario 4.

Lo que importa son las COMPRAS en el mercado (el CEO de Uber puso 10 M
propios a 70.96 el 10/09/2026): nadie compra con su plata si cree que la
accion va a bajar. Las ventas casi no dicen nada (impuestos, diversificar,
planes programados) y las filas sin texto son acciones recibidas como sueldo.
Ver gold/insiders.py.

Correr: python insiders.py
"""

import json
import time
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import db

load_dotenv()

CACHE_DIR = Path("cache/insiders")
CACHE_DIAS = 7
PAUSA_ENTRE_TICKERS = 1.2
DIAS_ENTRE_CORRIDAS = 2


def tipo_transaccion(texto: str | None) -> str:
    texto = (texto or "").strip().lower()
    if texto.startswith("purchase"):
        return "Compra"
    if texto.startswith("sale"):
        return "Venta"
    return "Otro"


def filas_insiders(ticker: str, tabla: pd.DataFrame) -> list[dict]:
    if tabla is None or tabla.empty:
        return []
    filas = []
    for _, r in tabla.iterrows():
        texto = r.get("Text") if pd.notna(r.get("Text")) else None
        filas.append({
            "ticker_usd": ticker,
            "fecha": pd.Timestamp(r["Start Date"]).date(),
            "insider": r.get("Insider"),
            "cargo": r.get("Position"),
            "tipo": tipo_transaccion(texto),
            "acciones": int(r["Shares"]) if pd.notna(r.get("Shares")) else None,
            "valor": float(r["Value"]) if pd.notna(r.get("Value")) else None,
            "texto": texto,
        })
    return filas


def obtener(ticker_yahoo: str) -> pd.DataFrame:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{ticker_yahoo.replace('/', '_')}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) / 86400 < CACHE_DIAS:
        return pd.read_json(path, orient="records", convert_dates=["Start Date"])
    tabla = yf.Ticker(ticker_yahoo).insider_transactions
    tabla = tabla if tabla is not None else pd.DataFrame()
    path.write_text(tabla.to_json(orient="records", date_format="iso") if not tabla.empty else "[]", encoding="utf-8")
    time.sleep(PAUSA_ENTRE_TICKERS)
    return tabla


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_reciente(con_check, "insiders", dias=DIAS_ENTRE_CORRIDAS)
    con_check.close()
    if ya_corrio:
        print("insiders ya corrio en los ultimos dias, no hace falta repetir.")
        return

    universo = pd.read_csv("data/cedears_normalizados.csv")
    universo = universo[universo["mercado"] == "usa_otros"]
    filas = []
    for i, f in enumerate(universo.itertuples(), 1):
        print(f"[{i}/{len(universo)}] {f.ticker_yahoo}", end="\r")
        try:
            filas.extend(filas_insiders(f.ticker_usd, obtener(f.ticker_yahoo)))
        except Exception as e:
            print(f"\n{f.ticker_yahoo}: {e}")
    print()

    con = db.conectar()
    with db.registrar(con, "insiders") as log:
        db.upsert_fact_insider_transacciones(con, filas)
        log["filas_afectadas"] = len(filas)
    compras = sum(f["tipo"] == "Compra" for f in filas)
    print(f"fact_insider_transacciones: {len(filas)} transacciones ({compras} compras en el mercado)")
    con.close()


if __name__ == "__main__":
    main()
