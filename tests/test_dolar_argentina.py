from datetime import date

import dolar_argentina


def test_convierte_la_respuesta_de_la_api_en_filas_de_macro_con_promedio_compra_venta():
    datos = [
        {"casa": "contadoconliqui", "compra": 1000.0, "venta": 1010.0, "fecha": "2026-10-23"},
        {"casa": "contadoconliqui", "compra": 1649.2, "venta": 1654.0, "fecha": "2026-10-26"},
    ]
    filas = dolar_argentina.a_filas(datos, "AR_CCL")
    assert filas == [
        {"serie": "AR_CCL", "fecha": date(2026, 10, 23), "valor": 1005.0},
        {"serie": "AR_CCL", "fecha": date(2026, 10, 26), "valor": 1651.6},
    ]


def test_descarta_filas_sin_compra_o_venta():
    datos = [{"casa": "blue", "compra": None, "venta": 1000.0, "fecha": "2026-10-26"}]
    assert dolar_argentina.a_filas(datos, "AR_BLUE") == []


def test_todas_las_casas_tienen_serie_propia():
    assert set(dolar_argentina.CASAS.values()) == {
        "AR_OFICIAL", "AR_BLUE", "AR_MEP", "AR_CCL", "AR_MAYORISTA", "AR_CRIPTO", "AR_TARJETA"}
