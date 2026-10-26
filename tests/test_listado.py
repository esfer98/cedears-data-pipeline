import listado_cedears as lc

TITULOS = [
    {"simbolo": "GLD", "descripcion": "Cedear Etf Spdr Gold Trust"},
    {"simbolo": "GLDC", "descripcion": "Cedear Etf Spdr Gold Trust"},
    {"simbolo": "GLDD", "descripcion": "Cedear Etf Spdr Gold Trust"},
    {"simbolo": "XLK", "descripcion": "Cedear State Street Technology Select Sector Spdr"},
    {"simbolo": "XLKC", "descripcion": "Cedear The Technology Select S"},  # descripcion cortada por IOL
    {"simbolo": "XLC", "descripcion": "Cedear The Communication Servi"},  # base con descripcion cortada
    {"simbolo": "XLCC", "descripcion": "Cedear The Communication Servi"},
    {"simbolo": "XLCD", "descripcion": "Cedear The Communication Servi"},
    {"simbolo": "XLYD", "descripcion": "Cedear The Consumer Discretionary Select Sector"},
    {"simbolo": "COPX", "descripcion": "CEDEAR GLOBAL X COPPER MINERS ETF"},
    {"simbolo": "AAPL", "descripcion": "Cedear Apple Inc"},
    {"simbolo": "AAPLC", "descripcion": "Cedear Apple Inc"},
]


def test_separa_etfs_de_empresas_aunque_la_descripcion_venga_cortada():
    empresas, etfs = lc.separar_etfs(TITULOS)
    assert {t["simbolo"] for t in empresas} == {"AAPL", "AAPLC"}
    assert {t["simbolo"] for t in etfs} == {"GLD", "GLDC", "GLDD", "XLK", "XLKC", "XLC", "XLCC", "XLCD", "XLYD", "COPX"}


def test_una_fila_por_etf_con_el_ticker_base_para_yahoo():
    _, etfs = lc.separar_etfs(TITULOS)
    filas = lc.filas_etf(etfs)
    por_ticker = {f["ticker_usd"]: f for f in filas}
    assert set(por_ticker) == {"GLD", "XLK", "XLC", "XLY", "COPX"}
    assert all(f["mercado"] == "etf" for f in filas)
    assert por_ticker["GLD"]["ticker_yahoo"] == "GLD"
    assert por_ticker["GLD"]["variantes"] == "GLD, GLDC, GLDD"
    assert por_ticker["XLC"]["ticker_yahoo"] == "XLC"
    assert por_ticker["XLC"]["variantes"] == "XLC, XLCC, XLCD"


def test_mapeos_de_yahoo_verificados_para_empresas_que_no_tenian_precio():
    assert lc.TICKERS_YAHOO["BAYN"] == "BAYRY"
    assert lc.TICKERS_YAHOO["YZCA"] == "YZCAY"
    assert lc.es_duplicado("BMNRD")
