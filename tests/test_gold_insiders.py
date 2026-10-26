from datetime import date, timedelta

import db
from gold import insiders as gold_insiders

HOY = date.today()


def _tx(ticker, dias_atras, insider, cargo, tipo, acciones, valor):
    return {"ticker_usd": ticker, "fecha": HOY - timedelta(days=dias_atras), "insider": insider, "cargo": cargo,
            "tipo": tipo, "acciones": acciones, "valor": valor, "texto": None}


def test_compra_del_ceo_es_relevante_y_las_ventas_no_suman(con):
    db.upsert_fact_insider_transacciones(con, [
        _tx("UBER", 40, "DARA", "Chief Executive Officer", "Compra", 141000, 10_005_952.0),
        _tx("UBER", 30, "JILL", "President", "Venta", 28170, 2_008_673.0),
        _tx("UBER", 10, "BALAJI", "Chief Financial Officer", "Otro", 4392, None),
    ])
    con.execute(gold_insiders.SQL_VISTA)
    f = con.execute("SELECT * FROM gold_insiders WHERE ticker_usd = 'UBER'").fetchdf().iloc[0]
    assert f.ins_compras_90d == 1
    assert f.ins_compras_monto_90d == 10_005_952.0
    assert f.ins_compro_ceo_cfo
    assert f.ins_ventas_monto_90d == 2_008_673.0
    assert f.ins_senal == "Compra relevante"


def test_compras_chicas_de_rutina_no_son_senal(con):
    # TSMC: un directivo compro 10-20 acciones por mes (4.000-8.000 USD): ruido
    db.upsert_fact_insider_transacciones(con, [
        _tx("TSM", 20, "TIEN", "Officer", "Compra", 15, 5850.0),
        _tx("TSM", 50, "TIEN", "Officer", "Compra", 10, 4050.0),
    ])
    con.execute(gold_insiders.SQL_VISTA)
    senal = con.execute("SELECT ins_senal FROM gold_insiders WHERE ticker_usd = 'TSM'").fetchone()[0]
    assert senal == "Sin compras relevantes"


def test_transacciones_viejas_no_cuentan(con):
    db.upsert_fact_insider_transacciones(con, [_tx("OLD", 200, "X", "Chief Executive Officer", "Compra", 1000, 500_000.0)])
    con.execute(gold_insiders.SQL_VISTA)
    assert con.execute("SELECT ins_compras_90d FROM gold_insiders WHERE ticker_usd = 'OLD'").fetchone()[0] == 0
