"""
resultados_trimestrales.py
---------------------------
Resultados por trimestre de cada empresa (Yahoo, ~5 trimestres), cadencia
semanal -> fact_resultados_trimestrales.

Por que: la ganancia neta sola engana. Con Uber (octubre 2026) la ganancia
neta saltaba +-1.600 M por trimestre por revaluar sus participaciones en
otras empresas, mientras la ganancia operativa crecia estable; el P/E y las
"sorpresas" de -82% / +353% eran ruido. Hubo que bajarlo a mano. Aca queda
separado: ganancia operativa, resultado por participaciones, no recurrentes,
flujo de caja libre, recompras y pagos en acciones.

Correr: python resultados_trimestrales.py
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

CACHE_DIR = Path("cache/resultados_trimestrales")
CACHE_DIAS = 7
PAUSA_ENTRE_TICKERS = 1.2
DIAS_ENTRE_CORRIDAS = 2  # mismo criterio que los otros semanales (ver analisis_fundamental_pesado.py)

# concepto de Yahoo -> columna propia
CONCEPTOS_RESULTADOS = {
    "Total Revenue": "ingresos",
    "Operating Income": "ganancia_operativa",
    "Net Income": "ganancia_neta",
    "Gain On Sale Of Security": "resultado_participaciones",
    "Total Unusual Items": "resultados_no_recurrentes",
    "Diluted EPS": "eps_diluido",
    "Diluted Average Shares": "acciones_diluidas",
}
CONCEPTOS_CAJA = {
    "Operating Cash Flow": "flujo_caja_operativo",
    "Free Cash Flow": "flujo_caja_libre",
    "Repurchase Of Capital Stock": "recompras",
    "Stock Based Compensation": "pagos_en_acciones",
}


def _valor(tabla: pd.DataFrame, concepto: str, fecha) -> float | None:
    if tabla is None or tabla.empty or concepto not in tabla.index or fecha not in tabla.columns:
        return None
    v = tabla.loc[concepto, fecha]
    return None if pd.isna(v) else float(v)


def filas_trimestrales(ticker: str, resultados: pd.DataFrame, caja: pd.DataFrame) -> list[dict]:
    """Tablas de yfinance (conceptos en filas, trimestres en columnas) -> filas."""
    if resultados is None or resultados.empty:
        return []
    filas = []
    for fecha in sorted(resultados.columns):
        fila = {"ticker_usd": ticker, "fecha_trimestre": pd.Timestamp(fecha).date()}
        for concepto, col in CONCEPTOS_RESULTADOS.items():
            fila[col] = _valor(resultados, concepto, fecha)
        for concepto, col in CONCEPTOS_CAJA.items():
            fila[col] = _valor(caja, concepto, fecha)
        if fila["recompras"] is not None:
            fila["recompras"] = abs(fila["recompras"])  # Yahoo la trae negativa (salida de caja)
        filas.append(fila)
    return filas


def _a_json(tabla: pd.DataFrame | None) -> dict:
    if tabla is None or tabla.empty:
        return {}
    return {str(pd.Timestamp(c).date()): {k: (None if pd.isna(v) else float(v)) for k, v in tabla[c].items()} for c in tabla.columns}


def _de_json(d: dict) -> pd.DataFrame:
    if not d:
        return pd.DataFrame()
    return pd.DataFrame({pd.Timestamp(k): v for k, v in d.items()})


def obtener(ticker_yahoo: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{ticker_yahoo.replace('/', '_')}.json"
    if path.exists() and (time.time() - path.stat().st_mtime) / 86400 < CACHE_DIAS:
        datos = json.loads(path.read_text(encoding="utf-8"))
        return _de_json(datos["resultados"]), _de_json(datos["caja"])
    t = yf.Ticker(ticker_yahoo)
    resultados, caja = t.quarterly_income_stmt, t.quarterly_cashflow
    path.write_text(json.dumps({"resultados": _a_json(resultados), "caja": _a_json(caja)}), encoding="utf-8")
    time.sleep(PAUSA_ENTRE_TICKERS)
    return resultados, caja


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_reciente(con_check, "resultados_trimestrales", dias=DIAS_ENTRE_CORRIDAS)
    con_check.close()
    if ya_corrio:
        print("resultados_trimestrales ya corrio en los ultimos dias, no hace falta repetir.")
        return

    universo = db.con_fundamentales(pd.read_csv("data/cedears_normalizados.csv"))
    filas = []
    for i, f in enumerate(universo.itertuples(), 1):
        print(f"[{i}/{len(universo)}] {f.ticker_yahoo}", end="\r")
        try:
            resultados, caja = obtener(f.ticker_yahoo)
        except Exception as e:
            print(f"\n{f.ticker_yahoo}: {e}")
            continue
        filas.extend(filas_trimestrales(f.ticker_usd, resultados, caja))
    print()

    con = db.conectar()
    with db.registrar(con, "resultados_trimestrales") as log:
        db.upsert_fact_resultados_trimestrales(con, filas)
        log["filas_afectadas"] = len(filas)
    print(f"fact_resultados_trimestrales: {len(filas)} filas, {len({f['ticker_usd'] for f in filas})} empresas ({date.today()})")
    con.close()


if __name__ == "__main__":
    main()
