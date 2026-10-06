"""
gold/salud_financiera.py
--------------------------
Capa Gold: score de salud financiera/solvencia, combinando liquidez,
apalancamiento, rentabilidad y cobertura de deuda con flujo de caja. No es
un Altman Z-score real -- ese modelo necesita partidas del balance que no
tenemos en crudo (capital de trabajo, utilidades retenidas, valor de
mercado del patrimonio); esto es un score propio, más simple, con lo que
el pipeline ya trae de Yahoo.

Todas las columnas van prefijadas "salud_", misma convención que el resto
de gold/.

Que calcula (5 criterios, cada uno 1 punto si se cumple):
  1. salud_chk_liquidez: razon_corriente >= 1.2 (puede cubrir pasivos de
     corto plazo con activos de corto plazo, con margen).
  2. salud_chk_apalancamiento: deuda_patrimonio <= 100% (la deuda no supera
     al patrimonio).
  3. salud_chk_rentabilidad: roe_pct >= 10%.
  4. salud_chk_margen_positivo: margen_neto_pct > 0 (gana plata, no pierde).
  5. salud_chk_cobertura_deuda: podria pagar toda su deuda con <= 5 años de
     flujo de caja libre actual (o directamente no tiene deuda).

Bancos/fintechs no reportan razon_corriente ni deuda_patrimonio en el
formato estandar de Yahoo (su balance no se mide asi -- los depositos son
pasivo pero no son "deuda" en el sentido corporativo) -- mismo caso que el
waterfall de margenes en el notebook. Por eso el score NO divide por 5 a
secas: salud_evaluables cuenta cuantos de los 5 criterios tenian dato, y
salud_score es la suma sobre ESOS. Una empresa con salud_score=3 y
salud_evaluables=3 (ej. un banco) esta tan sólida como una con
salud_score=5 y salud_evaluables=5 -- leer siempre los dos juntos, nunca
salud_score solo.

Correr: python gold/salud_financiera.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

SQL_VISTA = """
CREATE OR REPLACE VIEW gold_salud_financiera AS
WITH ultimo_metrics AS (
    SELECT *
    FROM fact_metrics_daily
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
),
ultimo_balance AS (
    SELECT *
    FROM fact_balance_cashflow_annual
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha_balance DESC) = 1
),
base AS (
    SELECT
        m.ticker_usd, m.fecha,
        m.razon_corriente, m.deuda_patrimonio, m.roe_pct, m.margen_neto_pct,
        b.deuda_total, b.flujo_caja_libre,
        CASE WHEN b.flujo_caja_libre > 0 THEN ROUND(b.deuda_total / b.flujo_caja_libre, 1) END AS anios_pagar_deuda,
        (m.razon_corriente >= 1.2) AS chk_liquidez,
        (m.deuda_patrimonio <= 100) AS chk_apalancamiento,
        (m.roe_pct >= 10) AS chk_rentabilidad,
        (m.margen_neto_pct > 0) AS chk_margen,
        CASE
            WHEN b.deuda_total IS NOT NULL AND b.flujo_caja_libre IS NOT NULL
            THEN (b.deuda_total <= 0 OR b.deuda_total / NULLIF(b.flujo_caja_libre, 0) <= 5)
        END AS chk_cobertura
    FROM ultimo_metrics m
    LEFT JOIN ultimo_balance b USING (ticker_usd)
)
SELECT
    b.ticker_usd, d.nombre_empresa, d.sector, b.fecha,
    b.razon_corriente, b.deuda_patrimonio, b.roe_pct, b.margen_neto_pct,
    b.deuda_total, b.flujo_caja_libre, b.anios_pagar_deuda,

    b.chk_liquidez AS salud_chk_liquidez,
    b.chk_apalancamiento AS salud_chk_apalancamiento,
    b.chk_rentabilidad AS salud_chk_rentabilidad,
    b.chk_margen AS salud_chk_margen_positivo,
    b.chk_cobertura AS salud_chk_cobertura_deuda,

    COALESCE(b.chk_liquidez::INT, 0) + COALESCE(b.chk_apalancamiento::INT, 0)
        + COALESCE(b.chk_rentabilidad::INT, 0) + COALESCE(b.chk_margen::INT, 0)
        + COALESCE(b.chk_cobertura::INT, 0) AS salud_score,
    (b.chk_liquidez IS NOT NULL)::INT + (b.chk_apalancamiento IS NOT NULL)::INT
        + (b.chk_rentabilidad IS NOT NULL)::INT + (b.chk_margen IS NOT NULL)::INT
        + (b.chk_cobertura IS NOT NULL)::INT AS salud_evaluables,

    CASE
        WHEN (b.chk_liquidez IS NOT NULL)::INT + (b.chk_apalancamiento IS NOT NULL)::INT
             + (b.chk_rentabilidad IS NOT NULL)::INT + (b.chk_margen IS NOT NULL)::INT
             + (b.chk_cobertura IS NOT NULL)::INT = 0
            THEN 'Sin datos suficientes'
        WHEN (COALESCE(b.chk_liquidez::INT, 0) + COALESCE(b.chk_apalancamiento::INT, 0)
              + COALESCE(b.chk_rentabilidad::INT, 0) + COALESCE(b.chk_margen::INT, 0)
              + COALESCE(b.chk_cobertura::INT, 0))
             >= 0.8 * ((b.chk_liquidez IS NOT NULL)::INT + (b.chk_apalancamiento IS NOT NULL)::INT
                        + (b.chk_rentabilidad IS NOT NULL)::INT + (b.chk_margen IS NOT NULL)::INT
                        + (b.chk_cobertura IS NOT NULL)::INT)
            THEN 'Sólida'
        WHEN (COALESCE(b.chk_liquidez::INT, 0) + COALESCE(b.chk_apalancamiento::INT, 0)
              + COALESCE(b.chk_rentabilidad::INT, 0) + COALESCE(b.chk_margen::INT, 0)
              + COALESCE(b.chk_cobertura::INT, 0))
             >= 0.5 * ((b.chk_liquidez IS NOT NULL)::INT + (b.chk_apalancamiento IS NOT NULL)::INT
                        + (b.chk_rentabilidad IS NOT NULL)::INT + (b.chk_margen IS NOT NULL)::INT
                        + (b.chk_cobertura IS NOT NULL)::INT)
            THEN 'Aceptable'
        ELSE 'Riesgo'
    END AS salud_clasificacion
FROM base b
JOIN dim_empresa d USING (ticker_usd)
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_salud_financiera") as log:
        con.execute(SQL_VISTA)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM gold_salud_financiera").fetchone()[0]

    print("=== Distribución por clasificación ===")
    print(con.execute("""
        SELECT salud_clasificacion, COUNT(*) AS empresas
        FROM gold_salud_financiera GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf().to_string(index=False))

    print("\n=== Top 15 'Riesgo' con más historia detrás (para mirar con cuidado) ===")
    print(con.execute("""
        SELECT ticker_usd, nombre_empresa, sector, salud_score, salud_evaluables,
               razon_corriente, deuda_patrimonio, anios_pagar_deuda
        FROM gold_salud_financiera
        WHERE salud_clasificacion = 'Riesgo' AND salud_evaluables >= 4
        ORDER BY salud_score ASC
        LIMIT 15
    """).fetchdf().to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
