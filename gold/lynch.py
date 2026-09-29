"""
gold/lynch.py
-------------
Capa Gold: clasificacion estilo Peter Lynch ("Un paso por delante de Wall
Street") calculada con reglas sobre fact_metrics_daily + dim_empresa. Sin
LLM para la parte numerica -- todo lo que se puede medir con un umbral, se
mide con un umbral.

Ojo: el propio Lynch dice que invertir es un arte, no una ciencia exacta
("la gente que se ha formado para cuantificarlo todo rigidamente tiene una
gran desventaja"). Esto es una aproximacion de punto de partida con datos,
no un veredicto -- sirve para filtrar el universo de 340 empresas a un
puñado que merece mirarse mas de cerca, no para reemplazar el juicio.

Cada metodologia de analisis (Lynch, Quant, Tecnico...) vive en su propio
archivo dentro de gold/ y TODAS sus columnas van prefijadas ("lynch_") para
que nunca choquen con las de otra metodologia, aunque convivan en el mismo
dashboard o en una vista combinada mas adelante.

Que calcula:
  1. lynch_categoria_auto: las 6 categorias del libro, por regla (crecimiento
     de ingresos, margen, beta/sector). Es la MISMA categorizacion que
     dim_empresa.lynch_category iba a hacerse a mano/con LLM -- esta es la
     version automatica; conviven las dos (ver mas abajo).
  2. lynch_activo_oculto_potencial: 0 < price_to_book < 1 (categoria 6,
     tratada como flag aparte porque en la practica no es excluyente con
     las otras 5).
  3. lynch_valuacion_peg: la heuristica clasica de Lynch (PEG < 1 = barata)
     sobre el peg_ratio que ya veniamos trayendo de Yahoo.
  4. lynch_checklist_score: cuantos de los 13 rasgos de la "empresa
     perfecta" se pueden verificar con los datos que tenemos, y cuantos
     cumple cada empresa. De los 13 del libro, HOY se pueden medir 6:
       - baja cobertura institucional  (institutional_holding_pct < 30%)
       - deuda baja                    (deuda_patrimonio < 50%)
       - activo oculto                 (0 < price_to_book < 1 -- negativo NO
                                        cuenta, es patrimonio neto negativo)
       - empresa chica (espacio p/crecer) (market_cap < 10.000M USD)
       - PEG atractivo                 (peg_ratio < 1)
       - recompra de acciones          (shares_outstanding bajando) --
         *este ultimo da NULL hasta que haya >= 30 dias de historial*,
         porque recien empezamos a trackear shares_outstanding hoy.
     Los otros 7 (nombre aburrido, negocio desagradable, es un spin-off,
     rumores negativos, nicho, consumo recurrente, insiders comprando -- el
     de insiders tambien necesita historial) son cualitativos o necesitan
     datos que no tenemos (texto de noticias, historial de insider trades) y
     quedan fuera de este script.

Como se relaciona con dim_empresa.lynch_category:
    dim_empresa.lynch_category es la clasificacion CURADA (a mano o con LLM
    revisado por vos) -- la fuente de verdad para analisis serios.
    gold_lynch.lynch_categoria_auto es la propuesta automatica -- util como
    primer filtro y como insumo para llenar dim_empresa mas rapido, pero no
    la pisa (dim_empresa se sigue actualizando aparte, ver db.py).

Correr: python gold/lynch.py
"""

import sys
from pathlib import Path

# db.py vive un nivel arriba (raiz del proyecto): cada script en gold/ agrega
# esa carpeta a sys.path para poder importarlo sin convertir el proyecto en
# un paquete instalable -- no hace falta esa complejidad a esta escala.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

SQL_VISTA = """
CREATE OR REPLACE VIEW gold_lynch AS
WITH ultimo AS (
    SELECT *
    FROM fact_metrics_daily
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
),
primero AS (
    SELECT
        ticker_usd,
        fecha AS fecha_primera,
        shares_outstanding AS shares_outstanding_primera
    FROM fact_metrics_daily
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha ASC) = 1
),
base AS (
    SELECT
        u.*,
        d.nombre_empresa,
        -- COALESCE con sector_corregido: Yahoo clasifica mal algunos casos
        -- (ej. procesadoras de pago como "Technology"), ver correcciones_sector.py.
        COALESCE(d.sector_corregido, d.sector) AS sector,
        d.industria, d.pais_origen, d.mercado,
        d.lynch_category AS lynch_categoria_manual, d.modelo_negocio,
        p.fecha_primera, p.shares_outstanding_primera
    FROM ultimo u
    JOIN dim_empresa d USING (ticker_usd)
    LEFT JOIN primero p USING (ticker_usd)
)
SELECT
    ticker_usd, nombre_empresa, sector, industria, pais_origen, mercado, fecha,

    CASE
        WHEN crecimiento_ganancia_anual_pct < -20 OR margen_neto_pct < 0
            THEN 'Recuperable'
        WHEN beta >= 1.4 OR sector IN ('Energy', 'Basic Materials', 'Industrials')
            THEN 'Ciclica'
        WHEN crecimiento_ingresos_anual_pct >= 18 THEN 'Alto Crecimiento'
        WHEN crecimiento_ingresos_anual_pct >= 5 THEN 'Estable'
        WHEN crecimiento_ingresos_anual_pct IS NOT NULL THEN 'Bajo Crecimiento'
        ELSE 'Sin clasificar (falta crecimiento de ingresos)'
    END AS lynch_categoria_auto,

    (price_to_book IS NOT NULL AND price_to_book > 0 AND price_to_book < 1)
        AS lynch_activo_oculto_potencial,

    CASE
        WHEN peg_ratio IS NULL THEN 'Sin dato'
        WHEN peg_ratio < 0.5 THEN 'Muy barata'
        WHEN peg_ratio < 1 THEN 'Barata'
        WHEN peg_ratio < 1.5 THEN 'Razonable'
        WHEN peg_ratio < 2 THEN 'Cara'
        ELSE 'Muy cara'
    END AS lynch_valuacion_peg,

    (institutional_holding_pct IS NOT NULL AND institutional_holding_pct < 30)
        AS lynch_chk_baja_cobertura_institucional,
    (deuda_patrimonio IS NOT NULL AND deuda_patrimonio < 50) AS lynch_chk_deuda_baja,
    (price_to_book IS NOT NULL AND price_to_book > 0 AND price_to_book < 1)
        AS lynch_chk_activo_oculto,
    (market_cap IS NOT NULL AND market_cap < 10000000000) AS lynch_chk_empresa_pequena,
    (peg_ratio IS NOT NULL AND peg_ratio < 1) AS lynch_chk_peg_atractivo,
    CASE
        WHEN fecha_primera IS NULL OR DATE_DIFF('day', fecha_primera, fecha) < 30 THEN NULL
        WHEN shares_outstanding_primera IS NULL OR shares_outstanding IS NULL THEN NULL
        ELSE shares_outstanding < shares_outstanding_primera
    END AS lynch_chk_recompra_acciones,

    (
        (institutional_holding_pct IS NOT NULL AND institutional_holding_pct < 30)::INT +
        (deuda_patrimonio IS NOT NULL AND deuda_patrimonio < 50)::INT +
        (price_to_book IS NOT NULL AND price_to_book > 0 AND price_to_book < 1)::INT +
        (market_cap IS NOT NULL AND market_cap < 10000000000)::INT +
        (peg_ratio IS NOT NULL AND peg_ratio < 1)::INT +
        COALESCE((
            CASE
                WHEN fecha_primera IS NOT NULL
                     AND DATE_DIFF('day', fecha_primera, fecha) >= 30
                     AND shares_outstanding_primera IS NOT NULL
                     AND shares_outstanding IS NOT NULL
                THEN (shares_outstanding < shares_outstanding_primera)::INT
            END
        ), 0)
    ) AS lynch_checklist_score,

    crecimiento_ingresos_anual_pct, crecimiento_ganancia_anual_pct, aceleracion_ingresos_pct,
    peg_ratio, pe_trailing, price_to_book, deuda_patrimonio, dividend_yield_pct,
    institutional_holding_pct, insider_holding_pct, market_cap, beta,
    lynch_categoria_manual, modelo_negocio
FROM base
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_lynch") as log:
        con.execute(SQL_VISTA)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM gold_lynch").fetchone()[0]

    print("=== Distribucion por categoria Lynch (automatica) ===")
    print(con.execute("""
        SELECT lynch_categoria_auto, COUNT(*) AS empresas
        FROM gold_lynch
        GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf().to_string(index=False))

    print("\n=== Top 15 por lynch_checklist_score (mas rasgos de Lynch cumplidos) ===")
    print(con.execute("""
        SELECT ticker_usd, nombre_empresa, lynch_categoria_auto, lynch_checklist_score,
               lynch_valuacion_peg, crecimiento_ingresos_anual_pct
        FROM gold_lynch
        ORDER BY lynch_checklist_score DESC, crecimiento_ingresos_anual_pct DESC
        LIMIT 15
    """).fetchdf().to_string(index=False))

    print("\n=== Alto crecimiento + PEG barato/muy barato (candidatas 'growth a precio razonable') ===")
    print(con.execute("""
        SELECT ticker_usd, nombre_empresa, crecimiento_ingresos_anual_pct, peg_ratio, lynch_valuacion_peg
        FROM gold_lynch
        WHERE lynch_categoria_auto = 'Alto Crecimiento' AND lynch_valuacion_peg IN ('Muy barata', 'Barata')
        ORDER BY peg_ratio ASC
    """).fetchdf().to_string(index=False))

    print("\n=== Activo oculto potencial (0 < price_to_book < 1) ===")
    print(con.execute("""
        SELECT ticker_usd, nombre_empresa, price_to_book, lynch_categoria_auto
        FROM gold_lynch
        WHERE lynch_activo_oculto_potencial
        ORDER BY price_to_book ASC
    """).fetchdf().to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
