"""
gold/comparables.py
--------------------
Capa Gold: compara cada empresa contra su sector (medianas) y devuelve sus
pares mas similares. Responde la pregunta "esto es caro/barato/crece mas o
menos QUE SUS COMPETIDORES", no en el vacio.

Mismo criterio que gold/lynch.py: vive en su propio archivo, crea su propia
vista, y prefija todas sus columnas ("comp_") para no chocar con Lynch ni
con futuras metodologias sobre las mismas empresas.

Que calcula:
  1. gold_comparables (vista): para cada empresa, la mediana de su sector en
     P/E, PEG, margen neto, crecimiento de ingresos y ROE, mas la diferencia
     de la empresa contra esa mediana.
  2. pares_similares(): dado un ticker, sus N empresas mas parecidas (mismo
     sector, misma moneda de cotizacion, tamaño de market cap mas cercano)
     -- la version programable de "comparalo contra acciones similares".
     La moneda importa: acciones argentinas (.BA) cotizan en ARS y BDRs
     brasileños (.SA) en BRL, no USD -- comparar market cap en crudo entre
     monedas da resultados sin sentido (ver comp_moneda).

Correr: python gold/comparables.py <TICKER>   (ej. python gold/comparables.py NU)
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

SQL_VISTA = """
CREATE OR REPLACE VIEW gold_comparables AS
WITH rellenado AS (
    -- Mismo motivo que gold_lynch: fact_metrics_daily la escriben liviano
    -- (diario) y pesado (semanal), cada uno solo trae sus columnas -- un dia
    -- que corre solo liviano, la fila de ESE dia tiene NULL en crecimiento_%
    -- aunque el dato siga vigente. Forward-fill con LAST_VALUE IGNORE NULLS
    -- antes de quedarnos con la fila mas reciente (ver comentario largo en
    -- gold/lynch.py).
    SELECT
        ticker_usd, fecha,
        LAST_VALUE(crecimiento_ingresos_anual_pct IGNORE NULLS) OVER w AS crecimiento_ingresos_anual_pct,
        LAST_VALUE(crecimiento_ganancia_anual_pct IGNORE NULLS) OVER w AS crecimiento_ganancia_anual_pct,
        LAST_VALUE(aceleracion_ingresos_pct IGNORE NULLS) OVER w AS aceleracion_ingresos_pct,
        LAST_VALUE(eps_growth_3y_pct IGNORE NULLS) OVER w AS eps_growth_3y_pct,
        LAST_VALUE(eps_growth_5y_pct IGNORE NULLS) OVER w AS eps_growth_5y_pct,
        LAST_VALUE(sorpresa_eps_prom_4q_pct IGNORE NULLS) OVER w AS sorpresa_eps_prom_4q_pct,
        LAST_VALUE(trimestres_superando_estimado IGNORE NULLS) OVER w AS trimestres_superando_estimado,
        LAST_VALUE(market_cap IGNORE NULLS) OVER w AS market_cap,
        LAST_VALUE(enterprise_value IGNORE NULLS) OVER w AS enterprise_value,
        LAST_VALUE(pe_trailing IGNORE NULLS) OVER w AS pe_trailing,
        LAST_VALUE(pe_forward IGNORE NULLS) OVER w AS pe_forward,
        LAST_VALUE(peg_ratio IGNORE NULLS) OVER w AS peg_ratio,
        LAST_VALUE(price_to_book IGNORE NULLS) OVER w AS price_to_book,
        LAST_VALUE(ev_ebitda IGNORE NULLS) OVER w AS ev_ebitda,
        LAST_VALUE(margen_bruto_pct IGNORE NULLS) OVER w AS margen_bruto_pct,
        LAST_VALUE(margen_operativo_pct IGNORE NULLS) OVER w AS margen_operativo_pct,
        LAST_VALUE(margen_neto_pct IGNORE NULLS) OVER w AS margen_neto_pct,
        LAST_VALUE(roe_pct IGNORE NULLS) OVER w AS roe_pct,
        LAST_VALUE(roa_pct IGNORE NULLS) OVER w AS roa_pct,
        LAST_VALUE(deuda_patrimonio IGNORE NULLS) OVER w AS deuda_patrimonio,
        LAST_VALUE(razon_corriente IGNORE NULLS) OVER w AS razon_corriente,
        LAST_VALUE(dividend_yield_pct IGNORE NULLS) OVER w AS dividend_yield_pct,
        LAST_VALUE(payout_ratio_pct IGNORE NULLS) OVER w AS payout_ratio_pct,
        LAST_VALUE(beta IGNORE NULLS) OVER w AS beta,
        LAST_VALUE(insider_holding_pct IGNORE NULLS) OVER w AS insider_holding_pct,
        LAST_VALUE(institutional_holding_pct IGNORE NULLS) OVER w AS institutional_holding_pct,
        LAST_VALUE(shares_outstanding IGNORE NULLS) OVER w AS shares_outstanding
    FROM fact_metrics_daily
    WINDOW w AS (PARTITION BY ticker_usd ORDER BY fecha ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
),
ultimo AS (
    SELECT *
    FROM rellenado
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
),
base AS (
    SELECT
        u.*, d.nombre_empresa,
        -- COALESCE con sector_corregido: Yahoo clasifica mal algunos casos
        -- (ej. procesadoras de pago como "Technology"), ver correcciones_sector.py.
        -- Sin esto, STNE/PAGS/XYZ se comparaban contra la mediana de software
        -- de alto multiplo en vez de contra sus competidores reales (PYPL/MA/V).
        COALESCE(d.sector_corregido, d.sector) AS sector,
        d.industria, d.mercado,
        -- market_cap viene en la moneda de cotizacion de Yahoo, no siempre
        -- USD: acciones argentinas (.BA) cotizan en ARS, BDRs brasileños
        -- (.SA) en BRL. Los ratios (P/E, margenes, ROE, crecimiento) son
        -- adimensionales y no les afecta, pero comparar market_cap en crudo
        -- entre monedas distintas da resultados sin sentido -- por eso
        -- pares_similares() exige que coincida esta columna.
        CASE d.mercado WHEN 'argentina_local' THEN 'ARS' WHEN 'brasil' THEN 'BRL' ELSE 'USD' END
            AS comp_moneda
    FROM ultimo u
    JOIN dim_empresa d USING (ticker_usd)
    WHERE COALESCE(d.sector_corregido, d.sector) IS NOT NULL
),
sector_stats AS (
    SELECT
        sector,
        COUNT(*) AS comp_pares_en_sector,
        MEDIAN(pe_trailing) FILTER (WHERE pe_trailing > 0 AND pe_trailing < 200)
            AS comp_pe_mediana_sector,
        MEDIAN(peg_ratio) FILTER (WHERE peg_ratio > 0) AS comp_peg_mediana_sector,
        MEDIAN(margen_neto_pct) AS comp_margen_neto_mediana_sector,
        MEDIAN(crecimiento_ingresos_anual_pct) AS comp_crecimiento_mediana_sector,
        MEDIAN(roe_pct) AS comp_roe_mediana_sector
    FROM base
    GROUP BY sector
)
SELECT
    b.ticker_usd, b.nombre_empresa, b.sector, b.industria, b.mercado, b.comp_moneda, b.fecha,
    b.market_cap,

    b.pe_trailing, s.comp_pe_mediana_sector,
    ROUND((b.pe_trailing / NULLIF(s.comp_pe_mediana_sector, 0) - 1) * 100, 1)
        AS comp_pe_vs_sector_pct,

    b.peg_ratio, s.comp_peg_mediana_sector,

    b.margen_neto_pct, s.comp_margen_neto_mediana_sector,
    ROUND(b.margen_neto_pct - s.comp_margen_neto_mediana_sector, 1)
        AS comp_margen_vs_sector_pp,

    b.crecimiento_ingresos_anual_pct, s.comp_crecimiento_mediana_sector,
    ROUND(b.crecimiento_ingresos_anual_pct - s.comp_crecimiento_mediana_sector, 1)
        AS comp_crecimiento_vs_sector_pp,

    b.roe_pct, s.comp_roe_mediana_sector,

    s.comp_pares_en_sector,
    ROUND(PERCENT_RANK() OVER (
        PARTITION BY b.sector ORDER BY b.crecimiento_ingresos_anual_pct
    ) * 100, 0) AS comp_percentil_crecimiento_en_sector
FROM base b
JOIN sector_stats s USING (sector)
"""


def pares_similares(con, ticker_usd: str, n: int = 5):
    """Las N empresas mas parecidas a `ticker_usd`: mismo sector Y misma
    moneda de cotizacion, ordenadas por cercania de market cap (una empresa
    de $50B no es "similar" a una de $500M aunque compartan sector -- y una
    de ARS 50B no es comparable en absoluto con una de USD 50B, son unidades
    distintas)."""
    return con.execute("""
        WITH objetivo AS (
            SELECT sector, comp_moneda, market_cap FROM gold_comparables WHERE ticker_usd = ?
        )
        SELECT c.ticker_usd, c.nombre_empresa, c.market_cap, c.pe_trailing,
               c.crecimiento_ingresos_anual_pct, c.margen_neto_pct, c.roe_pct
        FROM gold_comparables c, objetivo o
        WHERE c.sector = o.sector AND c.comp_moneda = o.comp_moneda AND c.ticker_usd != ?
        ORDER BY ABS(c.market_cap - o.market_cap) ASC
        LIMIT ?
    """, [ticker_usd, ticker_usd, n]).fetchdf()


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_comparables") as log:
        con.execute(SQL_VISTA)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM gold_comparables").fetchone()[0]

    tickers = sys.argv[1:] or ["NU", "META"]
    for ticker in tickers:
        fila = con.execute("SELECT * FROM gold_comparables WHERE ticker_usd = ?", [ticker]).fetchdf()
        if fila.empty:
            print(f"{ticker}: no encontrado en gold_comparables (¿esta en dim_empresa con sector cargado?)")
            continue

        f = fila.iloc[0]
        print(f"\n=== {ticker} — {f['nombre_empresa']} ({f['sector']}, {f['comp_pares_en_sector']} pares en el sector) ===")
        print(f"  P/E: {f['pe_trailing']:.1f}  (mediana sector: {f['comp_pe_mediana_sector']:.1f}, "
              f"{f['comp_pe_vs_sector_pct']:+.0f}%)")
        print(f"  Margen neto: {f['margen_neto_pct']:.1f}%  (mediana sector: {f['comp_margen_neto_mediana_sector']:.1f}%, "
              f"{f['comp_margen_vs_sector_pp']:+.1f}pp)")
        print(f"  Crecimiento ingresos: {f['crecimiento_ingresos_anual_pct']:.1f}%  "
              f"(mediana sector: {f['comp_crecimiento_mediana_sector']:.1f}%, "
              f"{f['comp_crecimiento_vs_sector_pp']:+.1f}pp, percentil {f['comp_percentil_crecimiento_en_sector']:.0f})")

        print(f"\n  Pares mas similares (mismo sector, market cap parecido):")
        print(pares_similares(con, ticker).to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
