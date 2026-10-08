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
  5. tech_volumen_prom_30d / tech_volumen_tendencia_pct: promedio de
     volumen de los ultimos 30 dias, y cuanto cambio contra los 90 dias
     ANTERIORES a esa ventana (dias 31 a 120 atras) -- mismo calculo que
     se hizo a mano analizando el volumen "agotandose" de $MU (ver
     notebooks/eda_cedears.ipynb seccion 8), ahora disponible para las
     420 sin repetirlo a mano cada vez. Positivo = mas volumen que antes,
     negativo = menos (tipico antes de un movimiento fuerte, para
     cualquier lado -- esto NO dice la direccion, solo que hay menos
     gente operando).
  6. tech_bb_pct_b / tech_bb_ancho_pct: Bandas de Bollinger (SMA20 +/- 2
     desvios). %B = donde esta el precio dentro de la banda (0 = banda
     inferior, 100 = superior, puede salirse). Ancho = (sup - inf) / SMA20;
     un ancho muy bajo ("squeeze") es la version numerica de "el precio
     esta comprimido" -- igual que el volumen, no dice para que lado sale.
  7. tech_macd / tech_macd_hist_pct / tech_macd_cruce: MACD 12-26-9. Las
     EMA son recursivas y no salen con un AVG de ventana; se usa la
     identidad EMA_t = SUM(x_k * w^k) / SUM(w^k) con w = 1/(1-alpha), que
     si es una suma acumulada (equivale a pandas ewm(adjust=True)). Como
     w^k crece exponencialmente, se limita a los ultimos 400 dias por
     ticker: sobra para que la EMA26 converja y evita desbordar un DOUBLE
     cuando la historia siga creciendo. El histograma va en % del precio
     para que sea comparable entre tickers.
  8. tech_atr14_pct: Average True Range de 14 dias en % del precio --
     cuanto se mueve la accion en un dia tipico contando gaps. Usa
     high/low/close SIN ajustar (el true range es intradiario).
  9. tech_dist_max_52s_pct / tech_dist_min_52s_pct: distancia al maximo y
     minimo de 52 semanas (252 dias habiles).

Los swings (ZigZag), niveles de Fibonacci y el conteo candidato de Elliott
NO estan aca: dependen del recorrido completo del precio y no se pueden
expresar como vista -- ver gold/ondas.py.

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
        AVG(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS sma20,
        STDDEV_POP(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) AS std20,
        MAX(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS max_52s,
        MIN(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 251 PRECEDING AND CURRENT ROW) AS min_52s,
        LAG(adj_close, 21) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS precio_1m,
        LAG(adj_close, 63) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS precio_3m,
        LAG(adj_close, 126) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS precio_6m
    FROM fact_precios_daily
    WHERE adj_close IS NOT NULL
),
volumen AS (
    SELECT
        ticker_usd, fecha,
        AVG(volume) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 29 PRECEDING AND CURRENT ROW) AS vol_prom_30d,
        AVG(volume) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 119 PRECEDING AND 30 PRECEDING) AS vol_prom_90d_previo
    FROM fact_precios_daily
    WHERE volume IS NOT NULL
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
atr_base AS (
    SELECT ticker_usd, fecha, close,
           GREATEST(
               high - low,
               ABS(high - LAG(close) OVER (PARTITION BY ticker_usd ORDER BY fecha)),
               ABS(low - LAG(close) OVER (PARTITION BY ticker_usd ORDER BY fecha))
           ) AS true_range
    FROM fact_precios_daily
    WHERE close IS NOT NULL AND high IS NOT NULL AND low IS NOT NULL
),
atr AS (
    SELECT ticker_usd, fecha, close,
           AVG(true_range) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS atr14,
           COUNT(true_range) OVER (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN 13 PRECEDING AND CURRENT ROW) AS dias_atr
    FROM atr_base
),
macd_ventana AS (
    SELECT ticker_usd, fecha, adj_close
    FROM fact_precios_daily
    WHERE adj_close IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) <= 400
),
macd_k AS (
    SELECT *, ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha) AS k
    FROM macd_ventana
),
macd_ema AS (
    SELECT ticker_usd, fecha, k,
           SUM(adj_close * POWER(13.0 / 11, k)) OVER w / SUM(POWER(13.0 / 11, k)) OVER w
         - SUM(adj_close * POWER(27.0 / 25, k)) OVER w / SUM(POWER(27.0 / 25, k)) OVER w AS macd
    FROM macd_k
    WINDOW w AS (PARTITION BY ticker_usd ORDER BY fecha ROWS UNBOUNDED PRECEDING)
),
macd AS (
    SELECT ticker_usd, fecha, k, macd,
           SUM(macd * POWER(10.0 / 8, k)) OVER w / SUM(POWER(10.0 / 8, k)) OVER w AS macd_senal
    FROM macd_ema
    WINDOW w AS (PARTITION BY ticker_usd ORDER BY fecha ROWS UNBOUNDED PRECEDING)
),
combinado AS (
    SELECT
        p.ticker_usd, p.fecha, p.adj_close, p.sma50, p.sma200, p.dias_sma200,
        p.sma20, p.std20, p.max_52s, p.min_52s,
        p.precio_1m, p.precio_3m, p.precio_6m,
        r.avg_gain, r.avg_loss, r.dias_rsi,
        v.vol_prom_30d, v.vol_prom_90d_previo,
        a.atr14, a.dias_atr, a.close AS close_sin_ajustar,
        m.macd, m.macd_senal, m.k AS dias_macd
    FROM precios p
    JOIN rsi r USING (ticker_usd, fecha)
    LEFT JOIN volumen v USING (ticker_usd, fecha)
    LEFT JOIN atr a USING (ticker_usd, fecha)
    LEFT JOIN macd m USING (ticker_usd, fecha)
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
    CASE WHEN precio_6m IS NOT NULL THEN ROUND((adj_close / precio_6m - 1) * 100, 1) END AS tech_retorno_6m_pct,
    ROUND(vol_prom_30d, 0) AS tech_volumen_prom_30d,
    CASE WHEN vol_prom_90d_previo IS NOT NULL
         THEN ROUND((vol_prom_30d / NULLIF(vol_prom_90d_previo, 0) - 1) * 100, 1)
    END AS tech_volumen_tendencia_pct,
    CASE WHEN dias_sma200 >= 20 THEN ROUND((adj_close - (sma20 - 2 * std20)) / NULLIF(4 * std20, 0) * 100, 1) END AS tech_bb_pct_b,
    CASE WHEN dias_sma200 >= 20 THEN ROUND(4 * std20 / NULLIF(sma20, 0) * 100, 1) END AS tech_bb_ancho_pct,
    CASE WHEN dias_macd >= 35 THEN ROUND(macd, 3) END AS tech_macd,
    CASE WHEN dias_macd >= 35 THEN ROUND((macd - macd_senal) / adj_close * 100, 2) END AS tech_macd_hist_pct,
    CASE WHEN dias_macd >= 35 THEN (CASE WHEN macd > macd_senal THEN 'Alcista' ELSE 'Bajista' END) END AS tech_macd_cruce,
    CASE WHEN dias_atr >= 14 THEN ROUND(atr14 / NULLIF(close_sin_ajustar, 0) * 100, 2) END AS tech_atr14_pct,
    ROUND((adj_close / max_52s - 1) * 100, 1) AS tech_dist_max_52s_pct,
    ROUND((adj_close / min_52s - 1) * 100, 1) AS tech_dist_min_52s_pct
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
