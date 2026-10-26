from datetime import date

import pandas as pd

import db
import precios_historicos


def test_pares_adr_toma_solo_las_acciones_locales_con_adr():
    universo = pd.DataFrame([
        {"ticker_usd": "GGAL", "ticker_adr_usa": "GGAL", "mercado": "argentina_local"},
        {"ticker_usd": "YPFD", "ticker_adr_usa": "YPF", "mercado": "argentina_local"},
        {"ticker_usd": "ALUA", "ticker_adr_usa": None, "mercado": "argentina_local"},
        {"ticker_usd": "AAPL", "ticker_adr_usa": None, "mercado": "usa_otros"},
    ])
    assert precios_historicos.pares_adr(universo) == [("GGAL", "GGAL"), ("YPFD", "YPF")]


def test_upsert_de_precios_adr_es_idempotente(con):
    filas = pd.DataFrame([
        {"ticker_usd": "YPFD", "ticker_adr": "YPF", "fecha": date(2026, 10, 23), "close": 40.0, "adj_close": 39.5, "volume": 1000},
    ])
    db.upsert_fact_precios_adr(con, filas)
    filas.loc[0, "close"] = 41.0
    db.upsert_fact_precios_adr(con, filas)
    assert con.execute("SELECT COUNT(*), MAX(close) FROM fact_precios_adr").fetchone() == (1, 41.0)
