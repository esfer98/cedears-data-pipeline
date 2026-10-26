from datetime import date

import db
from conftest import insertar


def _cargar(con, tickers):
    for t in tickers:
        insertar(con, "dim_empresa", [{"ticker_usd": t, "mercado": "usa_otros"}])
        insertar(con, "fact_precios_daily", [{"ticker_usd": t, "fecha": date(2026, 10, 23), "adj_close": 1.0}])
        insertar(con, "fact_metrics_daily", [{"ticker_usd": t, "fecha": date(2026, 10, 23)}])


def test_depura_lo_que_salio_del_universo_en_dim_y_en_los_facts(con):
    _cargar(con, [f"T{i}" for i in range(20)] + ["SCHW"])
    # precios huerfanos de un ticker viejo que nunca estuvo en dim (WEGE3.SA)
    insertar(con, "fact_precios_daily", [{"ticker_usd": "WEGE3.SA", "fecha": date(2026, 8, 13), "adj_close": 1.0}])
    borrados = db.depurar_fuera_de_universo(con, {f"T{i}" for i in range(20)})
    assert set(borrados) == {"SCHW", "WEGE3.SA"}
    assert con.execute("SELECT COUNT(*) FROM dim_empresa WHERE ticker_usd = 'SCHW'").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM fact_precios_daily WHERE ticker_usd IN ('SCHW', 'WEGE3.SA')").fetchone()[0] == 0
    assert con.execute("SELECT COUNT(*) FROM fact_metrics_daily").fetchone()[0] == 20


def test_no_borra_nada_si_el_universo_nuevo_vino_incompleto(con):
    # Si IOL devuelve un panel a medias, no se puede vaciar el warehouse por eso.
    _cargar(con, [f"T{i}" for i in range(20)])
    borrados = db.depurar_fuera_de_universo(con, {"T0", "T1"})
    assert borrados == []
    assert con.execute("SELECT COUNT(*) FROM dim_empresa").fetchone()[0] == 20
