import analisis_fundamental_liviano as liviano
import db
import macro_diario


def test_calcular_fila_trae_moneda_de_cotizacion_y_de_balance():
    datos = {"ticker": "ITUB", "info": {"currency": "USD", "financialCurrency": "BRL"}}
    fila = liviano.calcular_fila(datos)
    assert fila["moneda_cotizacion"] == "USD"
    assert fila["moneda_balance"] == "BRL"


def test_dim_empresa_guarda_las_monedas_y_no_las_pisa_con_vacio(con):
    base = {"ticker_usd": "ITUB", "ticker_yahoo": "ITUB", "mercado": "usa_otros", "nombre_empresa": "Itau", "variantes": "ITUB"}
    db.upsert_dim_empresa(con, [{**base, "moneda_cotizacion": "USD", "moneda_balance": "BRL"}])
    db.upsert_dim_empresa(con, [base])  # el listado semanal no trae monedas
    assert con.execute("SELECT moneda_cotizacion, moneda_balance FROM dim_empresa").fetchone() == ("USD", "BRL")


def test_macro_baja_las_monedas_de_los_balances_extranjeros():
    for moneda in ["EUR", "GBP", "JPY", "CNY", "HKD", "TWD", "KRW", "INR", "MXN", "CHF", "CAD", "AUD", "ZAR", "CLP", "COP"]:
        assert f"USD{moneda}" in macro_diario.SERIES
        assert macro_diario.SERIES[f"USD{moneda}"][0] == f"USD{moneda}=X"
