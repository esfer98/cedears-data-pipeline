"""Regresiones de los bugs de octubre 2026: cada test reproduce un caso real."""

from datetime import date, timedelta

import numpy as np
import pandas as pd

import monitoreo_cartera
import seleccionar_cartera
from conftest import insertar
from gold import ondas, salud_financiera
from silver import calidad_datos

HOY = date.today()


def _empresa(con, ticker, mercado="usa_otros"):
    insertar(con, "dim_empresa", [{"ticker_usd": ticker, "nombre_empresa": ticker, "mercado": mercado, "sector": "Technology"}])


def test_salud_usa_el_ultimo_margen_conocido_si_la_fila_del_dia_vino_a_medio_llenar(con):
    # 18/10/2026: pesado creo la fila del dia sin margen y la vista dio margen NULL para todas.
    _empresa(con, "AAA")
    insertar(con, "fact_metrics_daily", [
        {"ticker_usd": "AAA", "fecha": HOY - timedelta(days=1), "margen_neto_pct": 12.0, "roe_pct": 15.0},
        {"ticker_usd": "AAA", "fecha": HOY, "crecimiento_ganancia_anual_pct": 8.0},
    ])
    con.execute(salud_financiera.SQL_VISTA)
    margen, chk = con.execute("SELECT margen_neto_pct, salud_chk_margen_positivo FROM gold_salud_financiera").fetchone()
    assert margen == 12.0
    assert chk is True


def _precios(con, ticker, dias, volumen=1000, desde_hace=0):
    insertar(con, "fact_precios_daily", [
        {"ticker_usd": ticker, "fecha": HOY - timedelta(days=desde_hace + i), "open": 10, "high": 11, "low": 9,
         "close": 10, "adj_close": 10, "volume": volumen}
        for i in range(dias)
    ])


def test_silver_marca_error_si_la_cotizacion_no_se_mueve(con):
    # B-Gaming: precio clavado y volumen cero durante dias.
    _empresa(con, "MUERTA", "argentina_local")
    _precios(con, "MUERTA", dias=20, volumen=0)
    con.execute(calidad_datos.SQL)
    reglas = con.execute("SELECT regla, severidad FROM silver_calidad_datos WHERE ticker_usd = 'MUERTA'").fetchall()
    assert ("cotizacion_sin_movimiento", "error") in reglas


def test_silver_marca_error_si_el_precio_quedo_atrasado_respecto_de_su_mercado(con):
    # AXIA: sin precios desde agosto mientras el resto de su mercado seguia cotizando.
    _empresa(con, "VIVA")
    _empresa(con, "VIEJA")
    _precios(con, "VIVA", dias=5)
    _precios(con, "VIEJA", dias=5, desde_hace=30)
    con.execute(calidad_datos.SQL)
    marcadas = {r[0] for r in con.execute(
        "SELECT ticker_usd FROM silver_calidad_datos WHERE regla = 'precio_desactualizado'").fetchall()}
    assert marcadas == {"VIEJA"}


def test_guardado_semanal_guarda_una_sola_vez_por_semana(con):
    _empresa(con, "AAA")
    _precios(con, "AAA", dias=3)
    elegidos = pd.DataFrame([{"ticker_usd": "AAA", "score_compuesto": 0.9, "sector": "Technology", "region": "US",
                              "alerta_analistas": False, "alerta_sentimiento": False, "alerta_historia_corta": False,
                              "alerta_datos": False, "advertencias_datos": None, "dias_precio": 1500, "sent_score_promedio": 0.0}])
    seleccionar_cartera.guardar(con, elegidos, "semanal")
    seleccionar_cartera.guardar(con, elegidos, "semanal")
    assert con.execute("SELECT COUNT(*) FROM fact_cartera_seleccionada").fetchone()[0] == 1


def test_monitoreo_toma_la_ultima_de_dos_fechas_de_reporte_cercanas(con):
    # UBER: Yahoo traia 3/11 y 4/11; con la del 3 el aviso "reporto" salia un dia antes.
    _empresa(con, "UBER")
    _precios(con, "UBER", dias=10)
    insertar(con, "seguimiento", [{"ticker_usd": "UBER", "desde": HOY, "activo": True}])
    insertar(con, "fact_eps_trimestral", [
        {"ticker_usd": "UBER", "fecha_reporte": HOY + timedelta(days=8)},
        {"ticker_usd": "UBER", "fecha_reporte": HOY + timedelta(days=9)},
    ])
    con.execute("CREATE TABLE gold_technical (ticker_usd VARCHAR, tech_rsi14 DOUBLE, tech_dist_max_52s_pct DOUBLE)")
    con.execute("CREATE TABLE gold_sentimiento (ticker_usd VARCHAR, sent_clasificacion VARCHAR)")
    con.execute("CREATE TABLE silver_calidad_datos (ticker_usd VARCHAR, regla VARCHAR, severidad VARCHAR)")
    proximo = con.execute(monitoreo_cartera.SQL_SEGUIMIENTO).fetchdf()["proximo_reporte"].iloc[0]
    assert pd.Timestamp(proximo).date() == HOY + timedelta(days=9)


def test_zigzag_detecta_un_maximo_y_un_minimo_alternados():
    precios = np.array([100, 110, 120, 100, 90, 95, 110, 125])
    pivots = ondas.zigzag(precios, umbral_pct=15)
    # 100 -> 120 ya es +20% desde el minimo inicial: el indice 0 queda como primer pivot L
    assert [(i, tipo) for i, tipo, _ in pivots] == [(0, "L"), (2, "H"), (4, "L")]
    # el pivot se confirma cuando el precio ya se movio el umbral, nunca antes
    assert all(confirmacion > indice for indice, _, confirmacion in pivots)
