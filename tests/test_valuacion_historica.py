from datetime import date

import pandas as pd
import pytest

from gold import valuacion_historica as vh


def _precios(valores, desde="2024-01-31"):
    return pd.Series(valores, index=pd.date_range(desde, periods=len(valores), freq="ME"))


def _ganancias(filas):
    return pd.DataFrame(filas, columns=["fecha_balance", "ganancia_neta"])


def test_con_ganancia_constante_el_indice_sigue_al_precio():
    precios = _precios([10, 20])
    ganancias = _ganancias([(date(2023, 6, 30), 100.0)])
    indice = vh.indice_valuacion(precios, ganancias)
    assert indice.iloc[1] / indice.iloc[0] == pytest.approx(2.0)


def test_la_ganancia_cuenta_recien_cuando_se_publica():
    # cierre 31/12/2023, publicada ~75 dias despues: en enero 2024 rige la del ejercicio anterior
    precios = _precios([10, 10, 10, 10])  # ene, feb, mar, abr 2024
    ganancias = _ganancias([(date(2022, 12, 31), 100.0), (date(2023, 12, 31), 200.0)])
    indice = vh.indice_valuacion(precios, ganancias)
    assert indice.iloc[0] == pytest.approx(10 / 100)   # enero: todavia la de 2022
    assert indice.iloc[3] == pytest.approx(10 / 200)   # abril: ya publicada la de 2023


def test_convierte_la_ganancia_a_la_moneda_del_precio():
    # ADR en dolares, balance en reales: si el real vale la mitad, la ganancia en dolares tambien
    precios = _precios([10, 10])
    ganancias = _ganancias([(date(2023, 6, 30), 1000.0)])
    fx = pd.Series([5.0, 10.0], index=precios.index)  # reales por dolar
    indice = vh.indice_valuacion(precios, ganancias, fx)
    assert indice.iloc[1] / indice.iloc[0] == pytest.approx(2.0)


def test_meses_con_perdida_no_tienen_valuacion():
    precios = _precios([10, 10, 10, 10])
    ganancias = _ganancias([(date(2022, 12, 31), -50.0), (date(2023, 12, 31), 100.0)])
    indice = vh.indice_valuacion(precios, ganancias)
    assert indice.iloc[:2].isna().all()
    assert indice.iloc[3] > 0


def test_resumen_ubica_el_valor_de_hoy_en_su_historia():
    indice = pd.Series([1.0, 2.0, 3.0, 4.0, 1.0] * 3)  # hoy (ultimo) igual al minimo historico
    r = vh.resumir(indice, pe_hoy=8.0)
    assert r["val_percentil_hoy"] == pytest.approx(40.0)  # 6 de 15 meses con valuacion <= hoy
    assert r["val_pe_mediana_hist"] == pytest.approx(8.0 * 2.0)  # P/E escalado al de hoy
    assert r["val_lectura"] == "En su rango normal"


def test_lectura_barata_y_cara():
    barata = vh.resumir(pd.Series([5.0] * 19 + [1.0]), pe_hoy=10.0)
    cara = vh.resumir(pd.Series([1.0] * 19 + [5.0]), pe_hoy=10.0)
    assert barata["val_lectura"] == "Barata vs su historia"
    assert cara["val_lectura"] == "Cara vs su historia"


def test_poca_historia_no_da_lectura():
    assert vh.resumir(pd.Series([1.0, 2.0]), pe_hoy=10.0)["val_lectura"] == "Sin historia suficiente"
