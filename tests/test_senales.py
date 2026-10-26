from datetime import date, timedelta

import senales
from conftest import insertar

HOY = date(2026, 11, 20)  # fecha fija: las senales se evaluan "a esta fecha"


def _ruedas(n):
    """n dias habiles hacia atras desde HOY (lunes a viernes), del mas viejo al mas nuevo."""
    dias, d = [], HOY
    while len(dias) < n:
        if d.weekday() < 5:
            dias.append(d)
        d -= timedelta(days=1)
    return list(reversed(dias))


def _cargar_precios(con, ticker, cierres, volumenes=None, highs=None, lows=None):
    fechas = _ruedas(len(cierres))
    volumenes = volumenes or [1000] * len(cierres)
    highs = highs or [c * 1.01 for c in cierres]
    lows = lows or [c * 0.99 for c in cierres]
    insertar(con, "fact_precios_daily", [
        {"ticker_usd": ticker, "fecha": f, "open": c, "high": h, "low": lo, "close": c, "adj_close": c, "volume": v}
        for f, c, v, h, lo in zip(fechas, cierres, volumenes, highs, lows)
    ])
    return fechas


def _cargar_moneda(con, serie, valores):
    insertar(con, "fact_macro_daily", [{"serie": serie, "fecha": f, "valor": v} for f, v in zip(_ruedas(len(valores)), valores)])


def test_moneda_estable_si_el_dolar_no_subio_en_5_ruedas(con):
    _cargar_moneda(con, "USDBRL", [5.0, 5.4, 5.5, 5.45, 5.42, 5.40, 5.38])
    assert senales.moneda_estable(con, "USDBRL", HOY).activa is True


def test_moneda_no_estable_si_el_dolar_sigue_subiendo(con):
    _cargar_moneda(con, "USDBRL", [5.0, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6])
    assert senales.moneda_estable(con, "USDBRL", HOY).activa is False


def test_capitulacion_con_volumen_alto_y_cierre_en_la_parte_alta_del_dia(con):
    cierres = [100] * 95 + [80, 78, 79, 80, 81]
    volumenes = [1000] * 95 + [5000, 1200, 1100, 1000, 1000]
    highs = [c * 1.01 for c in cierres]
    lows = [c * 0.99 for c in cierres]
    lows[95], highs[95] = 70, 82  # dia de panico: toco 70 y cerro en 80, cerca del maximo
    _cargar_precios(con, "BBD", cierres, volumenes, highs, lows)
    assert senales.capitulacion(con, "BBD", HOY).activa is True


def test_no_es_capitulacion_si_el_dia_de_volumen_cerro_en_el_minimo(con):
    cierres = [100] * 95 + [71, 70, 69, 70, 70]
    volumenes = [1000] * 95 + [5000, 1200, 1100, 1000, 1000]
    highs = [c * 1.01 for c in cierres]
    lows = [c * 0.99 for c in cierres]
    highs[95], lows[95] = 90, 70.5  # vendieron hasta el final del dia
    _cargar_precios(con, "BBD", cierres, volumenes, highs, lows)
    assert senales.capitulacion(con, "BBD", HOY).activa is False


def test_dejo_de_hacer_minimos_si_el_minimo_quedo_atras_y_rebota(con):
    cierres = [100, 95, 90, 85, 80, 78, 79, 81, 82, 83, 82, 84, 85, 84, 86]
    _cargar_precios(con, "ITUB3", cierres)
    assert senales.dejo_de_hacer_minimos(con, "ITUB3", HOY).activa is True


def test_no_cuenta_como_fin_de_caida_una_suba_sin_caida_previa(con):
    # Caso real 26/10/2026: el rally pre-eleccion activaba la senal en todas las brasilenas.
    cierres = [80 + i for i in range(40)]
    _cargar_precios(con, "BBD", cierres)
    assert senales.dejo_de_hacer_minimos(con, "BBD", HOY).activa is False


def test_sigue_haciendo_minimos_si_el_ultimo_cierre_es_el_mas_bajo(con):
    cierres = [100, 98, 96, 94, 92, 90, 88, 86, 84, 82, 80, 78, 76, 74, 72]
    _cargar_precios(con, "ITUB3", cierres)
    assert senales.dejo_de_hacer_minimos(con, "ITUB3", HOY).activa is False


def test_post_reporte_activa_si_el_precio_aguanto_el_reporte(con):
    fechas = _cargar_precios(con, "NU", [10, 10, 10, 10, 10, 10, 10.2, 10.4, 10.3, 10.5])
    insertar(con, "fact_eps_trimestral", [{"ticker_usd": "NU", "fecha_reporte": fechas[6]}])
    assert senales.post_reporte(con, "NU", HOY).activa is True


def test_post_reporte_no_activa_si_el_reporte_lo_hundio(con):
    fechas = _cargar_precios(con, "NU", [10, 10, 10, 10, 10, 10, 9.0, 8.8, 8.9, 8.7])
    insertar(con, "fact_eps_trimestral", [{"ticker_usd": "NU", "fecha_reporte": fechas[6]}])
    assert senales.post_reporte(con, "NU", HOY).activa is False


def test_post_reporte_no_activa_si_todavia_no_reporto(con):
    _cargar_precios(con, "NU", [10] * 10)
    insertar(con, "fact_eps_trimestral", [{"ticker_usd": "NU", "fecha_reporte": HOY + timedelta(days=5)}])
    assert senales.post_reporte(con, "NU", HOY).activa is False


def test_rsi_recupera_si_vuelve_arriba_de_30_despues_de_estar_debajo(con):
    cierres = [100 - 2 * i for i in range(20)] + [62, 64, 66, 68, 70, 72]
    _cargar_precios(con, "PAGS", cierres)
    assert senales.rsi_recupera(con, "PAGS", HOY).activa is True


def test_rsi_es_100_si_no_hubo_ninguna_rueda_en_baja():
    import pandas as pd
    rsi = senales._rsi(pd.Series([10 + i for i in range(20)], dtype=float))
    assert rsi.iloc[-1] == 100


def test_evaluar_cuenta_las_senales_activas_y_omite_moneda_si_no_aplica(con):
    _cargar_precios(con, "PAGS", [100] * 30)
    resultado = senales.evaluar(con, "PAGS", moneda=None, hoy=HOY)
    assert set(resultado) == {"capitulacion", "dejo_de_hacer_minimos", "post_reporte", "rsi_recupera"}
    assert senales.cantidad_activas(resultado) == 0
