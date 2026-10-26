from datetime import date

import pandas as pd

import db
import insiders
import resultados_trimestrales as rt


def _tabla_yf(filas: dict, fechas: list[str]) -> pd.DataFrame:
    """Mismo formato que yfinance: filas = conceptos, columnas = fechas de cierre."""
    return pd.DataFrame(filas, index=pd.to_datetime(fechas)).T


def test_trimestrales_separa_ganancia_operativa_y_resultado_por_participaciones():
    # Uber 2T 2026: la ganancia neta (2.39) la inflo un +1.66 de participaciones
    resultados = _tabla_yf({
        "Total Revenue": [14.19e9], "Operating Income": [1.89e9], "Net Income": [2.39e9],
        "Gain On Sale Of Security": [1.66e9], "Total Unusual Items": [1.70e9],
        "Diluted EPS": [1.17], "Diluted Average Shares": [2.05e9],
    }, ["2026-06-30"])
    caja = _tabla_yf({
        "Operating Cash Flow": [2.86e9], "Free Cash Flow": [2.79e9],
        "Repurchase Of Capital Stock": [-0.52e9], "Stock Based Compensation": [0.55e9],
    }, ["2026-06-30"])
    filas = rt.filas_trimestrales("UBER", resultados, caja)
    assert filas == [{
        "ticker_usd": "UBER", "fecha_trimestre": date(2026, 6, 30),
        "ingresos": 14.19e9, "ganancia_operativa": 1.89e9, "ganancia_neta": 2.39e9,
        "resultado_participaciones": 1.66e9, "resultados_no_recurrentes": 1.70e9,
        "eps_diluido": 1.17, "acciones_diluidas": 2.05e9,
        "flujo_caja_operativo": 2.86e9, "flujo_caja_libre": 2.79e9,
        "recompras": 0.52e9, "pagos_en_acciones": 0.55e9,
    }]


def test_trimestrales_deja_vacio_lo_que_yahoo_no_trae():
    resultados = _tabla_yf({"Total Revenue": [1e9], "Net Income": [1e8]}, ["2026-03-31"])
    filas = rt.filas_trimestrales("AAA", resultados, pd.DataFrame())
    assert filas[0]["ganancia_operativa"] is None
    assert filas[0]["flujo_caja_libre"] is None


def test_upsert_trimestral_es_idempotente(con):
    fila = {"ticker_usd": "AAA", "fecha_trimestre": date(2026, 3, 31), "ingresos": 1.0}
    db.upsert_fact_resultados_trimestrales(con, [fila])
    db.upsert_fact_resultados_trimestrales(con, [{**fila, "ingresos": 2.0}])
    assert con.execute("SELECT COUNT(*), MAX(ingresos) FROM fact_resultados_trimestrales").fetchone() == (1, 2.0)


def test_tipo_de_transaccion_de_directivos():
    assert insiders.tipo_transaccion("Purchase at price 70.96 per share.") == "Compra"
    assert insiders.tipo_transaccion("Sale at price 71.31 per share.") == "Venta"
    assert insiders.tipo_transaccion("Stock Gift at price 0.00 per share.") == "Otro"
    assert insiders.tipo_transaccion("") == "Otro"  # acciones recibidas como sueldo: sin texto ni precio
    assert insiders.tipo_transaccion(None) == "Otro"


def test_filas_de_directivos_desde_la_tabla_de_yahoo():
    df = pd.DataFrame([{
        "Shares": 141000, "Value": 10005952.0, "Text": "Purchase at price 70.96 per share.",
        "Insider": "KHOSROWSHAHI DARA", "Position": "Chief Executive Officer",
        "Start Date": pd.Timestamp("2026-09-10"), "Ownership": "D",
    }])
    assert insiders.filas_insiders("UBER", df) == [{
        "ticker_usd": "UBER", "fecha": date(2026, 9, 10), "insider": "KHOSROWSHAHI DARA",
        "cargo": "Chief Executive Officer", "tipo": "Compra", "acciones": 141000, "valor": 10005952.0,
        "texto": "Purchase at price 70.96 per share.",
    }]


def test_upsert_de_directivos_no_duplica_la_misma_transaccion(con):
    fila = {"ticker_usd": "UBER", "fecha": date(2026, 9, 10), "insider": "X", "cargo": "CEO", "tipo": "Compra",
            "acciones": 100, "valor": 7000.0, "texto": "Purchase at price 70 per share."}
    db.upsert_fact_insider_transacciones(con, [fila])
    db.upsert_fact_insider_transacciones(con, [fila])
    assert con.execute("SELECT COUNT(*) FROM fact_insider_transacciones").fetchone()[0] == 1
