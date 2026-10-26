from datetime import date, datetime, timedelta

import db
from conftest import insertar


def test_conectar_crea_el_esquema_en_la_ruta_indicada(tmp_path):
    ruta = tmp_path / "otro.duckdb"
    con = db.conectar(ruta)
    tablas = {t[0] for t in con.execute("SHOW TABLES").fetchall()}
    con.close()
    assert ruta.exists()
    assert {"dim_empresa", "fact_precios_daily", "fact_metrics_daily", "log_ejecuciones"} <= tablas


def _log(con, script, inicio, estado="ok"):
    insertar(con, "log_ejecuciones", [{"script": script, "inicio": inicio, "fin": inicio, "estado": estado}])


def test_ya_corrio_hoy_no_cuenta_una_corrida_de_ayer_a_la_noche(con):
    # El bug de octubre 2026: una corrida de ayer contaba como "hoy" y se salteaban dias.
    ayer_noche = datetime.combine(date.today() - timedelta(days=1), datetime.min.time()) + timedelta(hours=23)
    _log(con, "precios_historicos", ayer_noche)
    assert db.ya_corrio_hoy(con, "precios_historicos") is False


def test_ya_corrio_hoy_cuenta_una_corrida_ok_de_hoy(con):
    _log(con, "precios_historicos", datetime.now())
    assert db.ya_corrio_hoy(con, "precios_historicos") is True


def test_ya_corrio_hoy_ignora_corridas_con_error(con):
    _log(con, "precios_historicos", datetime.now(), estado="error")
    assert db.ya_corrio_hoy(con, "precios_historicos") is False


def test_ya_corrio_reciente_con_dos_dias_no_bloquea_la_semana_siguiente(con):
    # La semanal del 11/10 se salteo porque la guarda era de 6 dias y la anterior fue un lunes.
    hace_seis_dias = datetime.now() - timedelta(days=6)
    _log(con, "analisis_fundamental_pesado", hace_seis_dias)
    assert db.ya_corrio_reciente(con, "analisis_fundamental_pesado", dias=2) is False
    assert db.ya_corrio_reciente(con, "analisis_fundamental_pesado", dias=6) is True
