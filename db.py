"""
db.py
-----
Warehouse local en DuckDB (un solo archivo, sin servidor) con 5 tablas:

  dim_empresa                  - descriptiva, cambia poco (PK: ticker_usd)
  fact_metrics_daily           - ratios de valuacion y momentum, una fila por dia (PK: ticker_usd + fecha)
  fact_precios_daily           - OHLCV, una fila por dia (PK: ticker_usd + fecha)
  fact_income_statement_annual - ingresos/ganancia neta en $ por balance anual (PK: ticker_usd + fecha_balance)
  fact_eps_trimestral          - EPS estimado/reportado por trimestre, incluye el proximo aun sin reportar (PK: ticker_usd + fecha_reporte)

listado_cedears.py, analisis_fundamental.py y precios_historicos.py escriben
cada uno las columnas que les corresponden. dim_empresa se actualiza con
UPSERT que preserva "sector"/"industria"/"pais_origen" (se refrescan) pero
NUNCA pisa "lynch_category"/"modelo_negocio" una vez que se cargaron a mano
(esas columnas no forman parte del UPDATE SET del upsert de metricas).

fact_income_statement_annual y fact_eps_trimestral son datos que
analisis_fundamental.py YA pedia a Yahoo para calcular crecimiento_%/sorpresa_%,
pero antes se descartaban despues de calcular el derivado. Ahora se persisten
en crudo tambien, para poder graficar la serie real (ingresos/EPS por año o
trimestre), no solo el porcentaje de cambio.
"""

from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path("data/warehouse.duckdb")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS dim_empresa (
    ticker_usd      VARCHAR PRIMARY KEY,
    ticker_cedear   VARCHAR,
    ticker_yahoo    VARCHAR,
    mercado         VARCHAR,
    nombre_empresa  VARCHAR,
    sector          VARCHAR,
    industria       VARCHAR,
    pais_origen     VARCHAR,
    modelo_negocio  VARCHAR,
    lynch_category  VARCHAR,
    variantes       VARCHAR,
    actualizado_en  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fact_metrics_daily (
    ticker_usd                      VARCHAR,
    fecha                           DATE,
    crecimiento_ingresos_anual_pct  DOUBLE,
    crecimiento_ganancia_anual_pct  DOUBLE,
    aceleracion_ingresos_pct        DOUBLE,
    eps_growth_3y_pct               DOUBLE,
    eps_growth_5y_pct               DOUBLE,
    sorpresa_eps_prom_4q_pct        DOUBLE,
    trimestres_superando_estimado   INTEGER,
    market_cap                      DOUBLE,
    enterprise_value                DOUBLE,
    pe_trailing                     DOUBLE,
    pe_forward                      DOUBLE,
    peg_ratio                       DOUBLE,
    price_to_book                   DOUBLE,
    ev_ebitda                       DOUBLE,
    margen_bruto_pct                DOUBLE,
    margen_operativo_pct            DOUBLE,
    margen_neto_pct                 DOUBLE,
    roe_pct                         DOUBLE,
    roa_pct                         DOUBLE,
    deuda_patrimonio                DOUBLE,
    razon_corriente                 DOUBLE,
    dividend_yield_pct              DOUBLE,
    payout_ratio_pct                DOUBLE,
    beta                            DOUBLE,
    insider_holding_pct             DOUBLE,
    institutional_holding_pct       DOUBLE,
    shares_outstanding              DOUBLE,
    PRIMARY KEY (ticker_usd, fecha)
);

CREATE TABLE IF NOT EXISTS fact_precios_daily (
    ticker_usd  VARCHAR,
    fecha       DATE,
    open        DOUBLE,
    high        DOUBLE,
    low         DOUBLE,
    close       DOUBLE,
    adj_close   DOUBLE,
    volume      BIGINT,
    PRIMARY KEY (ticker_usd, fecha)
);

CREATE TABLE IF NOT EXISTS fact_income_statement_annual (
    ticker_usd      VARCHAR,
    fecha_balance   DATE,
    ingresos        DOUBLE,
    ganancia_neta   DOUBLE,
    margen_neto_pct DOUBLE,
    PRIMARY KEY (ticker_usd, fecha_balance)
);

CREATE TABLE IF NOT EXISTS fact_eps_trimestral (
    ticker_usd    VARCHAR,
    fecha_reporte DATE,
    eps_estimado  DOUBLE,
    eps_reportado DOUBLE,
    sorpresa_pct  DOUBLE,
    PRIMARY KEY (ticker_usd, fecha_reporte)
);
"""


def conectar() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(ESQUEMA)
    # Migracion idempotente: agrega columnas nuevas a bases ya existentes
    # (CREATE TABLE IF NOT EXISTS no alcanza si la tabla ya existia sin esta columna).
    con.execute("ALTER TABLE fact_metrics_daily ADD COLUMN IF NOT EXISTS shares_outstanding DOUBLE")
    return con


def upsert_dim_empresa(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    """Inserta/actualiza identidad y clasificacion. No toca lynch_category ni
    modelo_negocio si la fila ya existia (se cargan aparte, a mano/con LLM)."""
    if not filas:
        return
    con.executemany(
        """
        INSERT INTO dim_empresa (
            ticker_usd, ticker_cedear, ticker_yahoo, mercado, nombre_empresa,
            sector, industria, pais_origen, variantes, actualizado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (ticker_usd) DO UPDATE SET
            ticker_cedear = excluded.ticker_cedear,
            ticker_yahoo = excluded.ticker_yahoo,
            mercado = excluded.mercado,
            nombre_empresa = excluded.nombre_empresa,
            sector = COALESCE(excluded.sector, dim_empresa.sector),
            industria = COALESCE(excluded.industria, dim_empresa.industria),
            pais_origen = COALESCE(excluded.pais_origen, dim_empresa.pais_origen),
            variantes = excluded.variantes,
            actualizado_en = now()
        """,
        [
            (f["ticker_usd"], f["ticker_cedear"], f["ticker_yahoo"], f["mercado"],
             f["nombre_empresa"], f.get("sector"), f.get("industria"), f.get("pais_origen"),
             f["variantes"])
            for f in filas
        ],
    )


def upsert_fact_metrics_daily(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    if not filas:
        return
    columnas = [
        "ticker_usd", "fecha", "crecimiento_ingresos_anual_pct", "crecimiento_ganancia_anual_pct",
        "aceleracion_ingresos_pct", "eps_growth_3y_pct", "eps_growth_5y_pct",
        "sorpresa_eps_prom_4q_pct", "trimestres_superando_estimado", "market_cap",
        "enterprise_value", "pe_trailing", "pe_forward", "peg_ratio", "price_to_book",
        "ev_ebitda", "margen_bruto_pct", "margen_operativo_pct", "margen_neto_pct",
        "roe_pct", "roa_pct", "deuda_patrimonio", "razon_corriente", "dividend_yield_pct",
        "payout_ratio_pct", "beta", "insider_holding_pct", "institutional_holding_pct",
        "shares_outstanding",
    ]
    placeholders = ", ".join("?" for _ in columnas)
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "fecha"))
    con.executemany(
        f"""
        INSERT INTO fact_metrics_daily ({", ".join(columnas)}) VALUES ({placeholders})
        ON CONFLICT (ticker_usd, fecha) DO UPDATE SET {actualizaciones}
        """,
        [tuple(f.get(c) for c in columnas) for f in filas],
    )


def upsert_fact_income_statement_annual(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    if not filas:
        return
    columnas = ["ticker_usd", "fecha_balance", "ingresos", "ganancia_neta", "margen_neto_pct"]
    placeholders = ", ".join("?" for _ in columnas)
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "fecha_balance"))
    con.executemany(
        f"""
        INSERT INTO fact_income_statement_annual ({", ".join(columnas)}) VALUES ({placeholders})
        ON CONFLICT (ticker_usd, fecha_balance) DO UPDATE SET {actualizaciones}
        """,
        [tuple(f.get(c) for c in columnas) for f in filas],
    )


def upsert_fact_eps_trimestral(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    if not filas:
        return
    columnas = ["ticker_usd", "fecha_reporte", "eps_estimado", "eps_reportado", "sorpresa_pct"]
    placeholders = ", ".join("?" for _ in columnas)
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "fecha_reporte"))
    con.executemany(
        f"""
        INSERT INTO fact_eps_trimestral ({", ".join(columnas)}) VALUES ({placeholders})
        ON CONFLICT (ticker_usd, fecha_reporte) DO UPDATE SET {actualizaciones}
        """,
        [tuple(f.get(c) for c in columnas) for f in filas],
    )


def upsert_fact_precios_daily(con: duckdb.DuckDBPyConnection, precios: pd.DataFrame) -> None:
    """Carga masiva (decenas/cientos de miles de filas): en vez de insertar
    fila por fila, se registra el DataFrame como vista temporal y se hace un
    unico INSERT ... SELECT set-based. Mucho mas rapido que executemany() para
    este volumen -- ese es tambien el motivo por el que existe esta funcion
    aparte de upsert_fact_metrics_daily (esa es chica, una fila por ticker)."""
    if precios.empty:
        return
    columnas = ["ticker_usd", "fecha", "open", "high", "low", "close", "adj_close", "volume"]
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "fecha"))
    con.register("precios_temp", precios[columnas])
    con.execute(f"""
        INSERT INTO fact_precios_daily ({", ".join(columnas)})
        SELECT {", ".join(columnas)} FROM precios_temp
        ON CONFLICT (ticker_usd, fecha) DO UPDATE SET {actualizaciones}
    """)
    con.unregister("precios_temp")
