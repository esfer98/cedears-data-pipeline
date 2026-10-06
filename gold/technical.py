"""
gold/technical.py
------------------
Capa Gold: indicadores tecnicos clasicos sobre fact_precios_daily -- medias
moviles, RSI y momentum de precio. Cierra un hueco real: en varias
conversaciones de analisis (Banco do Brasil "sobrecomprado", Micron con PEG
bajisimo) no habia forma de confirmar "sobrecompra" con un numero, solo con
la forma del grafico de precio a ojo.

Todas las columnas van prefijadas "tech_" siguiendo la misma convencion que
gold_lynch (lynch_*), gold_comparables (comp_*) y gold_macro_sensitivity
(macro_*).

Que calcula (todo en SQL, con funciones de ventana -- no hace falta pandas
ni numpy):
  1. tech_sma50 / tech_sma200 y tech_precio_vs_sma50_pct / _sma200_pct:
     medias moviles simples y cuanto esta el precio por encima/debajo.
  2. tech_tendencia: "Alcista" si SMA50 > SMA200 (golden cross), "Bajista"
     si no -- NULL hasta tener 200 dias de historia (si no, es ruido).
  3. tech_rsi14: RSI de 14 periodos, version simple (promedio movil de
     ganancias/perdidas, SMA -- no el suavizado exponencial de Wilder del
     RSI "oficial". Difiere un par de puntos del que muestra un bróker,
     pero la lectura direccional -- sobrecomprado/sobrevendido -- es la
     misma). >=70 sobrecomprado, <=30 sobrevendido, ver tech_rsi_senal.
  4. tech_retorno_1m_pct / _3m_pct / _6m_pct: momentum de precio, con
     aproximacion de 21/63/126 dias HABILES (no de calendario) por mes --
     convencion estandar en finanzas, evita un self-join correlacionado
     caro por fecha calendario exacta.

Correr: python gold/technical.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

SQL_VISTA = """
CREATE OR REPLACE VIEW gold_technical AS
WITH precios AS (
    SELECT
        ticker_usd, fecha, adj_close,
        AVG(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) AS sma50,
        AVG(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS sma200,
        COUNT(*) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 199 PRECEDING AND CURRENT ROW) AS dias_sma200,
        LAG(adj_close, 21) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS precio_1m,
        LAG(adj_close, 63) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS precio_3m,
        LAG(adj_close, 126) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS precio_6m
    FROM fact_precios_daily
    WHERE adj_close IS NOT NULL
),
rsi_base AS (
    SELECT ticker_usd, fecha,
           adj_close - LAG(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS delta
    FROM fact_precios_daily
    WHERE adj_close IS NOT NULL
),
rsi AS (
    SELECT
        ticker_usd, fecha,
        AVG(GREATEST(delta, 0)) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_gain,
        AVG(GREATEST(-delta, 0)) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS avg_loss,
        COUNT(*) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS dias_rsi
    FROM rsi_base
),
combinado AS (
    SELECT
        p.ticker_usd, p.fecha, p.adj_close, p.sma50, p.sma200, p.dias_sma200,
        p.precio_1m, p.precio_3m, p.precio_6m,
        r.avg_gain, r.avg_loss, r.dias_rsi
    FROM precios p
    JOIN rsi r USING (ticker_usd, fecha)
)
SELECT
    ticker_usd, fecha,
    adj_close AS tech_precio,
    ROUND(sma50, 2) AS tech_sma50,
    ROUND(sma200, 2) AS tech_sma200,
    CASE WHEN dias_sma200 >= 200 THEN ROUND((adj_close / sma50 - 1) * 100, 1) END AS tech_precio_vs_sma50_pct,
    CASE WHEN dias_sma200 >= 200 THEN ROUND((adj_close / sma200 - 1) * 100, 1) END AS tech_precio_vs_sma200_pct,
    CASE WHEN dias_sma200 >= 200 THEN (CASE WHEN sma50 > sma200 THEN 'Alcista' ELSE 'Bajista' END) END AS tech_tendencia,
    CASE WHEN dias_rsi >= 14 THEN ROUND(100 - 100 / (1 + avg_gain / NULLIF(avg_loss, 0)), 1) END AS tech_rsi14,
    CASE
        WHEN dias_rsi < 14 THEN NULL
        WHEN 100 - 100 / (1 + avg_gain / NULLIF(avg_loss, 0)) >= 70 THEN 'Sobrecomprado'
        WHEN 100 - 100 / (1 + avg_gain / NULLIF(avg_loss, 0)) <= 30 THEN 'Sobrevendido'
        ELSE 'Neutral'
    END AS tech_rsi_senal,
    CASE WHEN precio_1m IS NOT NULL THEN ROUND((adj_close / precio_1m - 1) * 100, 1) END AS tech_retorno_1m_pct,
    CASE WHEN precio_3m IS NOT NULL THEN ROUND((adj_close / precio_3m - 1) * 100, 1) END AS tech_retorno_3m_pct,
    CASE WHEN precio_6m IS NOT NULL THEN ROUND((adj_close / precio_6m - 1) * 100, 1) END AS tech_retorno_6m_pct
FROM combinado
QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_technical") as log:
        con.execute(SQL_VISTA)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM gold_technical").fetchone()[0]

    print("=== Distribución de señal RSI ===")
    print(con.execute("""
        SELECT tech_rsi_senal, COUNT(*) AS empresas
        FROM gold_technical GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf().to_string(index=False))

    print("\n=== Top 15 más sobrecompradas (RSI más alto) ===")
    print(con.execute("""
        SELECT t.ticker_usd, d.nombre_empresa, t.tech_rsi14, t.tech_tendencia, t.tech_retorno_1m_pct
        FROM gold_technical t JOIN dim_empresa d USING (ticker_usd)
        ORDER BY t.tech_rsi14 DESC LIMIT 15
    """).fetchdf().to_string(index=False))

    print("\n=== Top 15 más sobrevendidas (RSI más bajo) ===")
    print(con.execute("""
        SELECT t.ticker_usd, d.nombre_empresa, t.tech_rsi14, t.tech_tendencia, t.tech_retorno_1m_pct
        FROM gold_technical t JOIN dim_empresa d USING (ticker_usd)
        ORDER BY t.tech_rsi14 ASC LIMIT 15
    """).fetchdf().to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
