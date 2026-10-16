"""
silver/calidad_datos.py
------------------------
Capa Silver: controles de calidad sobre lo que dejan los scripts de ingesta
en las tablas fact_*. Responde UNA pregunta: ¿el dato es correcto? -- no si
la empresa es buena o sirve para la cartera (eso son reglas de negocio y
viven en gold/ y en seleccionar_cartera.py).

No borra ni corrige nada: MARCA. Reescribe entera la tabla
silver_calidad_datos (una fila por empresa y regla que no pasa) con dos
severidades:

  error        el dato no sirve para puntuar. seleccionar_cartera.py excluye
               a la empresa del ranking.
  advertencia  algo raro que puede ser real (un salto de precio despues de
               una eleccion, una ganancia extraordinaria). Se usa igual pero
               se muestra para revisar a mano.

Por que existe: el 15-16/10/2026 aparecieron juntos B-Gaming con el precio
clavado y volumen cero durante dias (y un unico print de +2164%), AXIA y
WEGE3.SA sin precios desde agosto, y SK Hynix -- primera del ranking -- con
valor de empresa negativo (Yahoo mezcla wones con dolares) y una tenencia
institucional de 0.7% que solo refleja que el ADR empezo a cotizar en julio.
Todo eso entraba al warehouse y terminaba puntuando sin que nada avisara.

Reglas:

  error
    cotizacion_sin_movimiento   > MAX_DIAS_SIN_VOLUMEN ruedas con volumen 0 en
                                los ultimos 30 dias: el precio no es de mercado
                                y los indicadores tecnicos salen basura.
    precio_desactualizado       la ultima cotizacion tiene mas de
                                MAX_DIAS_ATRASO dias respecto de la ultima de
                                su mercado (se compara por mercado para no
                                marcar feriados locales).
    precio_invalido             precio <= 0 o maximo del dia < minimo, en el
                                ultimo año.
    metricas_desactualizadas    la ultima fila de fact_metrics_daily tiene mas
                                de MAX_DIAS_ATRASO dias: el ranking puntuaria
                                con fundamentales viejos.

  advertencia
    salto_de_precio             un dia con +100% o -50% en los ultimos 90 dias.
    margen_incoherente          margen neto positivo y 10 puntos o mas arriba
                                del operativo: ganancia no recurrente (venta
                                de un activo, revaluacion, impuestos) o dato
                                mal armado. Infla todo lo que usa el margen
                                neto. No se marca a las que pierden plata y
                                pierden menos por intereses: es normal.
    moneda_inconsistente        valor de empresa negativo o mas de 10 veces la
                                capitalizacion: Yahoo mezcla la moneda del
                                balance con la de la cotizacion. No aplica a
                                servicios financieros (el valor de empresa no
                                tiene sentido para un banco). Los ratios
                                que usamos para puntuar (P/E, margenes, deuda
                                sobre patrimonio) no dependen de esto, por eso
                                no es error.
    tenencia_institucional_no_confiable
                                tenencia < 5% en una empresa con menos de un
                                año cotizando: es un listado nuevo, no una
                                empresa ignorada por los fondos. gold/lynch.py
                                no le cuenta el punto de "baja cobertura".

Correr: python silver/calidad_datos.py  (en run_diario.ps1 va despues de la
ingesta y antes de gold/)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

MAX_DIAS_SIN_VOLUMEN = 5
MAX_DIAS_ATRASO = 7

SQL = f"""
CREATE OR REPLACE TABLE silver_calidad_datos AS
WITH ultima_por_mercado AS (
    SELECT d.mercado, MAX(p.fecha) AS ultima_mercado
    FROM fact_precios_daily p JOIN dim_empresa d USING (ticker_usd)
    GROUP BY d.mercado
),
precios_ticker AS (
    SELECT p.ticker_usd, d.mercado,
           MIN(p.fecha) AS primera_fecha, MAX(p.fecha) AS ultima_fecha,
           COUNT(*) FILTER (WHERE p.fecha >= CURRENT_DATE - 30 AND COALESCE(p.volume, 0) = 0) AS dias_sin_volumen,
           COUNT(*) FILTER (WHERE p.fecha >= CURRENT_DATE - 365
                              AND (p.adj_close <= 0 OR p.close <= 0 OR p.high < p.low)) AS filas_invalidas
    FROM fact_precios_daily p JOIN dim_empresa d USING (ticker_usd)
    GROUP BY p.ticker_usd, d.mercado
),
saltos AS (
    SELECT ticker_usd, fecha, ret FROM (
        SELECT ticker_usd, fecha,
               adj_close / LAG(adj_close) OVER (PARTITION BY ticker_usd ORDER BY fecha) - 1 AS ret
        FROM fact_precios_daily WHERE adj_close > 0
    )
    WHERE fecha >= CURRENT_DATE - 90 AND (ret >= 1.0 OR ret <= -0.5)
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY ABS(ret) DESC) = 1
),
metricas AS (
    SELECT * FROM fact_metrics_daily
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
),
reglas AS (
    SELECT ticker_usd, 'cotizacion_sin_movimiento' AS regla, 'error' AS severidad,
           dias_sin_volumen || ' ruedas con volumen 0 en los ultimos 30 dias' AS detalle
    FROM precios_ticker WHERE dias_sin_volumen > {MAX_DIAS_SIN_VOLUMEN} AND mercado != 'cripto'

    UNION ALL
    SELECT t.ticker_usd, 'precio_desactualizado', 'error',
           'ultima cotizacion ' || t.ultima_fecha || ' (su mercado: ' || m.ultima_mercado || ')'
    FROM precios_ticker t JOIN ultima_por_mercado m USING (mercado)
    WHERE DATE_DIFF('day', t.ultima_fecha, m.ultima_mercado) > {MAX_DIAS_ATRASO}

    UNION ALL
    SELECT ticker_usd, 'precio_invalido', 'error', filas_invalidas || ' filas con precio <= 0 o maximo < minimo'
    FROM precios_ticker WHERE filas_invalidas > 0

    UNION ALL
    SELECT ticker_usd, 'metricas_desactualizadas', 'error', 'ultimas metricas del ' || fecha
    FROM metricas WHERE DATE_DIFF('day', fecha, (SELECT MAX(fecha) FROM fact_metrics_daily)) > {MAX_DIAS_ATRASO}

    UNION ALL
    SELECT ticker_usd, 'salto_de_precio', 'advertencia',
           ROUND(ret * 100, 1) || '% el ' || fecha
    FROM saltos

    UNION ALL
    SELECT ticker_usd, 'margen_incoherente', 'advertencia',
           'margen neto ' || margen_neto_pct || '% vs operativo ' || margen_operativo_pct || '%'
    FROM metricas WHERE margen_neto_pct > 0 AND margen_neto_pct >= margen_operativo_pct + 10

    UNION ALL
    SELECT ticker_usd, 'moneda_inconsistente', 'advertencia',
           'valor de empresa ' || ROUND(enterprise_value / 1e9, 1) || ' mil M vs capitalizacion ' || ROUND(market_cap / 1e9, 1) || ' mil M'
    FROM metricas m JOIN dim_empresa d USING (ticker_usd)
    WHERE m.market_cap > 0 AND COALESCE(d.sector_corregido, d.sector) != 'Financial Services'
      AND (m.enterprise_value < 0 OR ABS(m.enterprise_value) > 10 * m.market_cap)

    UNION ALL
    SELECT m.ticker_usd, 'tenencia_institucional_no_confiable', 'advertencia',
           'tenencia ' || m.institutional_holding_pct || '% y cotiza desde ' || t.primera_fecha
    FROM metricas m JOIN precios_ticker t USING (ticker_usd)
    WHERE m.institutional_holding_pct < 5 AND t.primera_fecha > CURRENT_DATE - 365
)
SELECT ticker_usd, regla, severidad, detalle, CURRENT_DATE AS fecha_evaluacion
FROM reglas
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "silver_calidad_datos") as log:
        con.execute(SQL)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM silver_calidad_datos").fetchone()[0]

    print("=== Controles que no pasan, por regla ===")
    print(con.execute("""
        SELECT severidad, regla, COUNT(*) AS empresas
        FROM silver_calidad_datos GROUP BY 1, 2 ORDER BY 1 DESC, 3 DESC
    """).fetchdf().to_string(index=False))

    print("\n=== Detalle ===")
    print(con.execute("""
        SELECT s.severidad, s.regla, s.ticker_usd, d.mercado, s.detalle
        FROM silver_calidad_datos s JOIN dim_empresa d USING (ticker_usd)
        ORDER BY s.severidad DESC, s.regla, s.ticker_usd
    """).fetchdf().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
