"""
gold/macro_sensitivity.py
--------------------------
Capa Gold: que tan sensible es el RETORNO de cada empresa a movimientos de
la tasa a 10 y 30 años, medido con datos reales (no con una regla de
categoria) -- correlacion entre el retorno diario de fact_precios_daily y
el delta diario de fact_macro_daily.

Motivo del archivo: la intuicion clasica dice "empresas de Alto Crecimiento
(PEG alto) sufren mas la compresion de multiplos cuando sube la tasa" --
pero al calcularlo con datos reales sobre nuestro universo, el efecto que
mas pesa es otro: acciones "bond proxy" (utilities, REITs, dividendo alto)
y mineras de oro (el oro compite con el bono como reserva de valor) son las
mas negativamente correlacionadas: bancos y petroleras, las que mas se
banefician. La categoria Lynch por si sola NO alcanza para predecir esto --
por eso esta metodologia vive aparte, con sus propias columnas (`macro_*`),
en vez de agregarse como una regla mas a gold_lynch.

Todas las columnas van prefijadas "macro_" siguiendo la misma convencion que
gold_lynch (lynch_*) y gold_comparables (comp_*): conviven en el mismo
warehouse sin pisarse.

Que calcula (todo en SQL, con CORR() -- correlacion de Pearson nativa de
DuckDB, no hace falta pandas/numpy para esto):
  1. macro_corr_ret_vs_ust10y / _ust30y: correlacion entre el retorno diario
     (adj_close, toda la historia de 5 años disponible) y el delta diario
     de la tasa correspondiente.
  2. macro_sensibilidad_tasa: etiqueta legible sobre macro_corr_ret_vs_ust10y.
  3. macro_dias_muestra: cuantos dias entraron en el calculo -- para no
     confiar en una correlacion calculada con pocos dias de historia
     (se exige >= 200 en el HAVING de la vista).

Limitacion real: correlacion no es causalidad, y una ventana de ~1 año de
historia no es suficiente para separar "sensible a la tasa" de "goteo de
otro factor que coincidio en el tiempo" (ej. el rally de IA en growth tech).
Sirve como señal exploratoria, no como input de un modelo de riesgo.

Correr: python gold/macro_sensitivity.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

SQL_VISTA = """
CREATE OR REPLACE VIEW gold_macro_sensitivity AS
WITH retornos AS (
    SELECT
        ticker_usd, fecha,
        adj_close / LAG(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha) - 1 AS retorno
    FROM fact_precios_daily
    WHERE adj_close IS NOT NULL
),
tasas AS (
    SELECT
        fecha,
        MAX(CASE WHEN serie = 'UST10Y' THEN valor END) AS ust10y,
        MAX(CASE WHEN serie = 'UST30Y' THEN valor END) AS ust30y
    FROM fact_macro_daily
    WHERE serie IN ('UST10Y', 'UST30Y')
    GROUP BY fecha
),
deltas AS (
    SELECT
        fecha,
        ust10y - LAG(ust10y) OVER (ORDER BY fecha) AS d_ust10y,
        ust30y - LAG(ust30y) OVER (ORDER BY fecha) AS d_ust30y
    FROM tasas
),
combinado AS (
    SELECT r.ticker_usd, r.retorno, d.d_ust10y, d.d_ust30y
    FROM retornos r
    JOIN deltas d USING (fecha)
    WHERE r.retorno IS NOT NULL AND d.d_ust10y IS NOT NULL
)
SELECT
    ticker_usd,
    COUNT(*) AS macro_dias_muestra,
    ROUND(CORR(retorno, d_ust10y), 3) AS macro_corr_ret_vs_ust10y,
    ROUND(CORR(retorno, d_ust30y), 3) AS macro_corr_ret_vs_ust30y,
    CASE
        WHEN CORR(retorno, d_ust10y) <= -0.15 THEN 'Muy sensible (cae con suba de tasa)'
        WHEN CORR(retorno, d_ust10y) <= -0.05 THEN 'Sensible (cae con suba de tasa)'
        WHEN CORR(retorno, d_ust10y) < 0.05 THEN 'Neutral'
        WHEN CORR(retorno, d_ust10y) < 0.15 THEN 'Se beneficia levemente'
        ELSE 'Se beneficia con suba de tasa'
    END AS macro_sensibilidad_tasa
FROM combinado
GROUP BY ticker_usd
HAVING COUNT(*) >= 200
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_macro_sensitivity") as log:
        con.execute(SQL_VISTA)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM gold_macro_sensitivity").fetchone()[0]

    print("=== Distribucion por sensibilidad a la tasa (UST10Y) ===")
    print(con.execute("""
        SELECT macro_sensibilidad_tasa, COUNT(*) AS empresas
        FROM gold_macro_sensitivity
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf().to_string(index=False))

    print("\n=== Top 15 mas sensibles NEGATIVAMENTE (caen cuando sube la tasa) ===")
    print(con.execute("""
        SELECT m.ticker_usd, d.nombre_empresa, d.sector, l.lynch_categoria_auto,
               m.macro_corr_ret_vs_ust10y, m.macro_dias_muestra
        FROM gold_macro_sensitivity m
        JOIN dim_empresa d USING (ticker_usd)
        LEFT JOIN gold_lynch l USING (ticker_usd)
        ORDER BY m.macro_corr_ret_vs_ust10y ASC
        LIMIT 15
    """).fetchdf().to_string(index=False))

    print("\n=== Top 15 que mas se BENEFICIAN cuando sube la tasa ===")
    print(con.execute("""
        SELECT m.ticker_usd, d.nombre_empresa, d.sector, l.lynch_categoria_auto,
               m.macro_corr_ret_vs_ust10y, m.macro_dias_muestra
        FROM gold_macro_sensitivity m
        JOIN dim_empresa d USING (ticker_usd)
        LEFT JOIN gold_lynch l USING (ticker_usd)
        ORDER BY m.macro_corr_ret_vs_ust10y DESC
        LIMIT 15
    """).fetchdf().to_string(index=False))

    print("\n=== Promedio de correlacion por categoria Lynch ===")
    print(con.execute("""
        SELECT l.lynch_categoria_auto, ROUND(AVG(m.macro_corr_ret_vs_ust10y), 3) AS corr_promedio, COUNT(*) AS empresas
        FROM gold_macro_sensitivity m
        JOIN gold_lynch l USING (ticker_usd)
        GROUP BY 1 ORDER BY 2 ASC
    """).fetchdf().to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
