"""
gold/insiders.py
-----------------
Capa Gold: resumen por empresa de las transacciones de directivos de los
ultimos 90 dias (fact_insider_transacciones). Columnas prefijadas "ins_".

Solo las COMPRAS en el mercado cuentan como senal: las ventas se muestran como
dato (impuestos, diversificacion, planes programados) y las transacciones
"Otro" (acciones recibidas como sueldo, regalos) se ignoran. Es el rasgo
"los directivos compran" de la lista de Peter Lynch.

  ins_senal = "Compra relevante" si en 90 dias compro el CEO o el CFO, o las
              compras suman MONTO_RELEVANTE o mas. Compras de rutina chicas
              (TSMC: 10-20 acciones por mes, 4.000-8.000 USD) no cuentan.

Todavia NO entra al ranking de seleccionar_cartera.py: primero se prueba si
predice algo (REGLAS.md, forma de trabajo). Lo usan el monitoreo y las notebooks.

Correr: python gold/insiders.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

DIAS = 90
MONTO_RELEVANTE = 100_000

SQL_VISTA = f"""
CREATE OR REPLACE VIEW gold_insiders AS
WITH ventana AS (
    SELECT * FROM fact_insider_transacciones WHERE fecha >= CURRENT_DATE - {DIAS}
),
empresas AS (SELECT DISTINCT ticker_usd FROM fact_insider_transacciones)
SELECT
    e.ticker_usd,
    COUNT(v.ticker_usd) FILTER (WHERE v.tipo = 'Compra') AS ins_compras_90d,
    COALESCE(SUM(v.valor) FILTER (WHERE v.tipo = 'Compra'), 0) AS ins_compras_monto_90d,
    COUNT(DISTINCT v.insider) FILTER (WHERE v.tipo = 'Compra') AS ins_compradores_90d,
    COALESCE(BOOL_OR(v.tipo = 'Compra' AND regexp_matches(lower(v.cargo), 'chief executive|chief financial|\\bceo\\b|\\bcfo\\b')), FALSE)
        AS ins_compro_ceo_cfo,
    MAX(v.fecha) FILTER (WHERE v.tipo = 'Compra') AS ins_ultima_compra,
    COUNT(v.ticker_usd) FILTER (WHERE v.tipo = 'Venta') AS ins_ventas_90d,
    COALESCE(SUM(v.valor) FILTER (WHERE v.tipo = 'Venta'), 0) AS ins_ventas_monto_90d,
    CASE
        WHEN COALESCE(BOOL_OR(v.tipo = 'Compra' AND regexp_matches(lower(v.cargo), 'chief executive|chief financial|\\bceo\\b|\\bcfo\\b')), FALSE)
          OR COALESCE(SUM(v.valor) FILTER (WHERE v.tipo = 'Compra'), 0) >= {MONTO_RELEVANTE}
        THEN 'Compra relevante'
        ELSE 'Sin compras relevantes'
    END AS ins_senal
FROM empresas e
LEFT JOIN ventana v USING (ticker_usd)
GROUP BY e.ticker_usd
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_insiders") as log:
        con.execute(SQL_VISTA)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM gold_insiders").fetchone()[0]
    print("=== Compras relevantes de directivos (ultimos 90 dias) ===")
    print(con.execute("""
        SELECT i.ticker_usd, d.nombre_empresa, i.ins_compras_90d, ROUND(i.ins_compras_monto_90d / 1e6, 2) AS compras_mill_usd,
               i.ins_compradores_90d, i.ins_compro_ceo_cfo, i.ins_ultima_compra, ROUND(i.ins_ventas_monto_90d / 1e6, 2) AS ventas_mill_usd
        FROM gold_insiders i JOIN dim_empresa d USING (ticker_usd)
        WHERE i.ins_senal = 'Compra relevante' ORDER BY i.ins_compras_monto_90d DESC
    """).fetchdf().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
