from datetime import date, timedelta

import monitoreo_cartera
from conftest import insertar


def _ruedas(n):
    dias, d = [], date.today()
    while len(dias) < n:
        if d.weekday() < 5:
            dias.append(d)
        d -= timedelta(days=1)
    return list(reversed(dias))


def _tablas_gold_vacias(con):
    con.execute("CREATE TABLE gold_technical (ticker_usd VARCHAR, tech_rsi14 DOUBLE, tech_dist_max_52s_pct DOUBLE)")
    con.execute("CREATE TABLE gold_sentimiento (ticker_usd VARCHAR, sent_clasificacion VARCHAR)")
    con.execute("CREATE TABLE silver_calidad_datos (ticker_usd VARCHAR, regla VARCHAR, severidad VARCHAR)")


def test_seguimiento_guarda_la_moneda_a_vigilar(con):
    insertar(con, "seguimiento", [{"ticker_usd": "BBD", "desde": date.today(), "moneda": "USDBRL", "activo": True}])
    assert con.execute("SELECT moneda FROM seguimiento").fetchone()[0] == "USDBRL"


def test_monitoreo_avisa_posible_entrada_con_tres_senales_activas(con):
    insertar(con, "dim_empresa", [{"ticker_usd": "BBD", "nombre_empresa": "Bradesco", "mercado": "usa_otros"}])
    insertar(con, "seguimiento", [{"ticker_usd": "BBD", "desde": date.today(), "moneda": "USDBRL", "activo": True}])
    fechas = _ruedas(100)
    # caida fuerte, dia de panico (hace 8 ruedas) con mucho volumen que cierra arriba,
    # despues rebote sin nuevos minimos
    cierres = [10.0] * 84 + [9.0, 8.0, 7.0, 6.0, 5.8, 5.7, 5.65] + [5.6] + [6.2, 6.8, 7.4, 8.0, 8.4, 8.8, 9.0, 9.2]
    volumenes = [1000] * 91 + [6000] + [1500] * 8
    lows = [c * 0.99 for c in cierres]
    lows[91] = 5.0
    insertar(con, "fact_precios_daily", [
        {"ticker_usd": "BBD", "fecha": f, "open": c, "high": c * 1.01, "low": lo, "close": c, "adj_close": c, "volume": v}
        for f, c, v, lo in zip(fechas, cierres, volumenes, lows)
    ])
    insertar(con, "fact_macro_daily", [{"serie": "USDBRL", "fecha": f, "valor": 5.6 - 0.01 * i} for i, f in enumerate(_ruedas(8))])
    _tablas_gold_vacias(con)

    lineas, con_alerta = monitoreo_cartera.seccion_seguimiento(con)

    assert con_alerta == ["BBD"]
    texto = "\n".join(lineas)
    assert "posible punto de entrada" in texto
    assert "moneda_estable" in texto
