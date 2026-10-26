from datetime import date

import pandas as pd

import db
from conftest import insertar
from gold import refugio


def test_con_fundamentales_saca_cripto_y_etf():
    universo = pd.DataFrame({"ticker_usd": ["AAPL", "BTC", "GLD", "YPFD"],
                             "mercado": ["usa_otros", "cripto", "etf", "argentina_local"]})
    assert db.con_fundamentales(universo)["ticker_usd"].tolist() == ["AAPL", "YPFD"]


def test_refugio_mide_los_etf_y_no_los_paneles_locales(con):
    insertar(con, "dim_empresa", [
        {"ticker_usd": "GLD", "mercado": "etf"},
        {"ticker_usd": "KO", "mercado": "usa_otros"},
        {"ticker_usd": "YPFD", "mercado": "argentina_local"},
    ])
    insertar(con, "fact_precios_daily", [
        {"ticker_usd": t, "fecha": date(2026, 10, 23), "adj_close": 10.0} for t in ["GLD", "KO", "YPFD"]
    ])
    assert set(refugio.precios_para_refugio(con)["ticker_usd"]) == {"GLD", "KO"}
