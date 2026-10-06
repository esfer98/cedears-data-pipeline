"""
db.py
-----
Warehouse local en DuckDB (un solo archivo, sin servidor) con 9 tablas:

  dim_empresa                  - descriptiva, cambia poco (PK: ticker_usd)
  fact_metrics_daily           - ratios de valuacion y momentum, una fila por dia (PK: ticker_usd + fecha)
  fact_precios_daily           - OHLCV, una fila por dia (PK: ticker_usd + fecha)
  fact_income_statement_annual - ingresos, costo de ingresos, beneficio bruto, gastos operativos y
                                  ganancia neta en $ por balance anual (PK: ticker_usd + fecha_balance)
  fact_eps_trimestral          - EPS estimado/reportado por trimestre, incluye el proximo aun sin reportar (PK: ticker_usd + fecha_reporte)
  fact_balance_cashflow_annual - deuda total, efectivo y flujo de caja libre por balance anual (PK: ticker_usd + fecha_balance)
  fact_macro_daily             - series macro (tasas, VIX, FX, commodities, indices), una fila por
                                  serie y dia (PK: serie + fecha) -- grano de mercado, no de empresa
  fact_consenso_analistas      - precio objetivo y recomendaciones de Wall Street, una fila por
                                  dia (PK: ticker_usd + fecha) -- ver consenso_analistas.py
  fact_noticias                 - titulares + sentimiento FinBERT, una fila por titular unico
                                  (PK: ticker_usd + titular) -- ver sentimiento_noticias.py

listado_cedears.py, analisis_fundamental_liviano.py/_pesado.py,
precios_historicos.py y macro_diario.py escriben cada uno las columnas/tablas
que les corresponden. dim_empresa se actualiza con UPSERT que preserva
"sector"/"industria"/"pais_origen" (se refrescan) pero NUNCA pisa
"lynch_category"/"modelo_negocio"/"sector_corregido" una vez que se cargaron
a mano (esas columnas no forman parte del UPDATE SET del upsert de
metricas). sector_corregido lo carga correcciones_sector.py cuando la
clasificacion GICS automatica de Yahoo no refleja el negocio real (ej.
procesadoras de pago clasificadas como "Technology"); gold/lynch.py y
gold/comparables.py usan COALESCE(sector_corregido, sector) para agrupar,
asi que la correccion se propaga a toda la capa Gold sin perder el dato
crudo de Yahoo.

fact_income_statement_annual y fact_eps_trimestral son datos que
analisis_fundamental_pesado.py YA pedia a Yahoo para calcular
crecimiento_%/sorpresa_%, pero antes se descartaban despues de calcular el
derivado. Ahora se persisten en crudo tambien, para poder graficar la serie
real (ingresos/EPS por año o trimestre), no solo el porcentaje de cambio.

fact_macro_daily es deliberadamente angosta (serie, fecha, valor) en vez de
una columna por serie: son ~16 series de fuentes/unidades heterogeneas (tasas
en %, indices en puntos, FX y commodities en $) que no comparten grano con
ninguna empresa -- meterlas como columnas de fact_metrics_daily mezclaria
grano empresa-dia con grano mercado-dia. Con este diseño agregar una serie
nueva es una fila de config en macro_diario.py, no una migracion de schema.
"""

from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import duckdb
import pandas as pd

DB_PATH = Path("data/warehouse.duckdb")

ESQUEMA = """
CREATE TABLE IF NOT EXISTS dim_empresa (
    ticker_usd       VARCHAR PRIMARY KEY,
    ticker_cedear    VARCHAR,
    ticker_yahoo     VARCHAR,
    ticker_adr_usa   VARCHAR,
    mercado          VARCHAR,
    nombre_empresa   VARCHAR,
    sector           VARCHAR,  -- crudo, tal cual lo clasifica Yahoo (GICS) -- se refresca solo
    sector_corregido VARCHAR,  -- override manual cuando Yahoo clasifica mal (ver correcciones_sector.py) -- NUNCA se pisa con upsert automatico
    industria        VARCHAR,
    pais_origen      VARCHAR,
    modelo_negocio   VARCHAR,
    lynch_category   VARCHAR,
    variantes        VARCHAR,
    actualizado_en   TIMESTAMP
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
    ticker_usd         VARCHAR,
    fecha_balance      DATE,
    ingresos           DOUBLE,
    costo_ingresos     DOUBLE,
    beneficio_bruto    DOUBLE,
    gastos_operativos  DOUBLE,
    ganancia_neta      DOUBLE,
    margen_neto_pct    DOUBLE,
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

CREATE TABLE IF NOT EXISTS fact_balance_cashflow_annual (
    ticker_usd        VARCHAR,
    fecha_balance     DATE,
    deuda_total       DOUBLE,
    efectivo          DOUBLE,
    flujo_caja_libre  DOUBLE,
    PRIMARY KEY (ticker_usd, fecha_balance)
);

CREATE TABLE IF NOT EXISTS fact_macro_daily (
    serie   VARCHAR,  -- codigo propio (UST10Y, VIX, DXY, USDBRL...), no el ticker de Yahoo
    fecha   DATE,
    valor   DOUBLE,   -- unidad depende de la serie: % para tasas/VIX, puntos para indices, $ para FX/commodities
    PRIMARY KEY (serie, fecha)
);

CREATE TABLE IF NOT EXISTS fact_consenso_analistas (
    ticker_usd              VARCHAR,
    fecha                   DATE,
    precio_objetivo_actual  DOUBLE,
    precio_objetivo_bajo    DOUBLE,
    precio_objetivo_alto    DOUBLE,
    precio_objetivo_mediana DOUBLE,
    rec_strong_buy          INTEGER,
    rec_buy                 INTEGER,
    rec_hold                INTEGER,
    rec_sell                INTEGER,
    rec_strong_sell         INTEGER,
    PRIMARY KEY (ticker_usd, fecha)
);

CREATE TABLE IF NOT EXISTS fact_noticias (
    ticker_usd          VARCHAR,
    titular             VARCHAR,
    fuente              VARCHAR,
    fecha_publicacion   TIMESTAMP,
    link                VARCHAR,
    sentimiento         VARCHAR,  -- 'positive' | 'negative' | 'neutral' (label de FinBERT)
    score_sentimiento   DOUBLE,   -- confianza del label elegido (0-1, softmax)
    fecha_procesado     DATE,
    PRIMARY KEY (ticker_usd, titular)
);

-- Log append-only (sin PK a proposito): cada corrida de cada script deja una
-- fila. Sirve para tres cosas: (1) idempotencia -- no repetir un job que ya
-- corrio hoy si el catch-up de Task Scheduler dispara dos veces el mismo dia,
-- (2) tablero de salud -- ver de un vistazo hace cuanto no corre cada pieza
-- sin tener que acordarse de mirarlo (nos paso de verdad con la rotacion del
-- certificado de Norton, que rompio el pipeline sin aviso), (3) diagnostico
-- -- el error queda guardado, no hay que rescatarlo de la terminal.
CREATE TABLE IF NOT EXISTS log_ejecuciones (
    script          VARCHAR,
    inicio          TIMESTAMP,
    fin             TIMESTAMP,
    estado          VARCHAR,  -- 'ok' | 'error'
    filas_afectadas INTEGER,
    error_detalle   VARCHAR
);
"""


def conectar() -> duckdb.DuckDBPyConnection:
    DB_PATH.parent.mkdir(exist_ok=True)
    con = duckdb.connect(str(DB_PATH))
    con.execute(ESQUEMA)
    # Migracion idempotente: agrega columnas nuevas a bases ya existentes
    # (CREATE TABLE IF NOT EXISTS no alcanza si la tabla ya existia sin esta columna).
    con.execute("ALTER TABLE fact_metrics_daily ADD COLUMN IF NOT EXISTS shares_outstanding DOUBLE")
    con.execute("ALTER TABLE fact_income_statement_annual ADD COLUMN IF NOT EXISTS costo_ingresos DOUBLE")
    con.execute("ALTER TABLE fact_income_statement_annual ADD COLUMN IF NOT EXISTS beneficio_bruto DOUBLE")
    con.execute("ALTER TABLE fact_income_statement_annual ADD COLUMN IF NOT EXISTS gastos_operativos DOUBLE")
    con.execute("ALTER TABLE dim_empresa ADD COLUMN IF NOT EXISTS ticker_adr_usa VARCHAR")
    con.execute("ALTER TABLE dim_empresa ADD COLUMN IF NOT EXISTS sector_corregido VARCHAR")
    return con


def upsert_dim_empresa(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    """Inserta/actualiza identidad y clasificacion. No toca lynch_category ni
    modelo_negocio si la fila ya existia (se cargan aparte, a mano/con LLM)."""
    if not filas:
        return
    con.executemany(
        """
        INSERT INTO dim_empresa (
            ticker_usd, ticker_cedear, ticker_yahoo, ticker_adr_usa, mercado,
            nombre_empresa, sector, industria, pais_origen, variantes, actualizado_en
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, now())
        ON CONFLICT (ticker_usd) DO UPDATE SET
            ticker_cedear = excluded.ticker_cedear,
            ticker_yahoo = excluded.ticker_yahoo,
            ticker_adr_usa = excluded.ticker_adr_usa,
            mercado = excluded.mercado,
            nombre_empresa = excluded.nombre_empresa,
            sector = COALESCE(excluded.sector, dim_empresa.sector),
            industria = COALESCE(excluded.industria, dim_empresa.industria),
            pais_origen = COALESCE(excluded.pais_origen, dim_empresa.pais_origen),
            variantes = excluded.variantes,
            actualizado_en = now()
        """,
        [
            (f["ticker_usd"], f.get("ticker_cedear"), f["ticker_yahoo"], f.get("ticker_adr_usa"),
             f["mercado"], f["nombre_empresa"], f.get("sector"), f.get("industria"),
             f.get("pais_origen"), f["variantes"])
            for f in filas
        ],
    )


def aplicar_correcciones_sector(con: duckdb.DuckDBPyConnection, correcciones: list[dict]) -> None:
    """Carga dim_empresa.sector_corregido a mano, para los casos donde la
    clasificacion GICS automatica de Yahoo no refleja el negocio real (ej.
    procesadoras de pago clasificadas como "Technology" en vez de "Financial
    Services"). Mismo criterio que lynch_category/modelo_negocio: se corrige
    aca, a mano, y analisis_fundamental_liviano.py NUNCA la pisa (no forma
    parte del UPDATE SET de upsert_dim_empresa) aunque el "sector" crudo de
    Yahoo se siga refrescando solo."""
    if not correcciones:
        return
    con.executemany(
        "UPDATE dim_empresa SET sector_corregido = ? WHERE ticker_usd = ?",
        [(c["sector_corregido"], c["ticker_usd"]) for c in correcciones],
    )


def upsert_fact_metrics_daily(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    """fact_metrics_daily la escriben DOS scripts con cadencias distintas:
    analisis_fundamental_liviano.py (diario, solo columnas de .info: P/E,
    P/B, market cap, margenes...) y analisis_fundamental_pesado.py (semanal,
    solo columnas derivadas de income_stmt/earnings: crecimiento_%,
    sorpresa_eps_%...). Los domingos corren los dos el mismo dia -- si el
    UPDATE SET fuera "col = excluded.col" a secas, el que corra segundo
    pisaria con NULL las columnas que el otro ya habia escrito esa fecha
    (una fila de un script no trae las columnas del otro). Por eso cada
    columna usa COALESCE(excluded.col, valor_actual): un valor nuevo la
    actualiza, un NULL entrante la deja como estaba."""
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
    actualizaciones = ", ".join(
        f"{c} = COALESCE(excluded.{c}, fact_metrics_daily.{c})"
        for c in columnas if c not in ("ticker_usd", "fecha")
    )
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
    columnas = [
        "ticker_usd", "fecha_balance", "ingresos", "costo_ingresos", "beneficio_bruto",
        "gastos_operativos", "ganancia_neta", "margen_neto_pct",
    ]
    placeholders = ", ".join("?" for _ in columnas)
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "fecha_balance"))
    con.executemany(
        f"""
        INSERT INTO fact_income_statement_annual ({", ".join(columnas)}) VALUES ({placeholders})
        ON CONFLICT (ticker_usd, fecha_balance) DO UPDATE SET {actualizaciones}
        """,
        [tuple(f.get(c) for c in columnas) for f in filas],
    )


def upsert_fact_balance_cashflow_annual(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    if not filas:
        return
    columnas = ["ticker_usd", "fecha_balance", "deuda_total", "efectivo", "flujo_caja_libre"]
    placeholders = ", ".join("?" for _ in columnas)
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "fecha_balance"))
    con.executemany(
        f"""
        INSERT INTO fact_balance_cashflow_annual ({", ".join(columnas)}) VALUES ({placeholders})
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


@contextmanager
def registrar(con: duckdb.DuckDBPyConnection, script: str):
    """Envuelve el trabajo de un script y deja una fila en log_ejecuciones al
    terminar, 'ok' o 'error' segun corresponda -- incluido el mensaje de la
    excepcion si fallo. Vuelve a lanzar la excepcion (para que el proceso
    termine con codigo de error y Task Scheduler lo detecte como fallido).

    Uso:
        with db.registrar(con, "analisis_fundamental") as log:
            ...hacer el trabajo...
            log["filas_afectadas"] = len(filas_metrics)
    """
    inicio = datetime.now()
    info = {"filas_afectadas": None}
    try:
        yield info
    except Exception as e:
        con.execute(
            "INSERT INTO log_ejecuciones (script, inicio, fin, estado, filas_afectadas, error_detalle) "
            "VALUES (?, ?, ?, 'error', ?, ?)",
            [script, inicio, datetime.now(), info["filas_afectadas"], str(e)],
        )
        raise
    else:
        con.execute(
            "INSERT INTO log_ejecuciones (script, inicio, fin, estado, filas_afectadas, error_detalle) "
            "VALUES (?, ?, ?, 'ok', ?, NULL)",
            [script, inicio, datetime.now(), info["filas_afectadas"]],
        )


def ya_corrio_hoy(con: duckdb.DuckDBPyConnection, script: str) -> bool:
    """True si `script` ya tuvo una corrida exitosa hoy. Para la guarda de
    idempotencia de los jobs diarios: si Task Scheduler dispara un catch-up
    (PC apagada a la hora programada, corre al prenderla) y el job normal de
    esa misma noche ya habia corrido bien, no tiene sentido repetirlo."""
    return ya_corrio_reciente(con, script, dias=1)


def ya_corrio_reciente(con: duckdb.DuckDBPyConnection, script: str, dias: int) -> bool:
    """True si `script` tuvo una corrida exitosa en los ultimos `dias` dias.
    Generalizacion de ya_corrio_hoy() para jobs que no son diarios -- p.ej.
    el fetch pesado semanal usa dias=6: si por catch-up termina corriendo el
    lunes en vez del domingo, no hace falta que vuelva a correr esa semana."""
    fila = con.execute(
        "SELECT COUNT(*) FROM log_ejecuciones "
        "WHERE script = ? AND estado = 'ok' AND inicio >= CURRENT_DATE - INTERVAL (?) DAY",
        [script, dias],
    ).fetchone()
    return fila[0] > 0


def estado_pipeline(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    """Ultima corrida (ok o no) de cada script -- el tablero de salud del
    pipeline completo en una sola consulta."""
    return con.execute("""
        SELECT
            script,
            MAX(inicio) AS ultima_corrida,
            MAX(inicio) FILTER (WHERE estado = 'ok') AS ultima_corrida_ok,
            COUNT(*) FILTER (WHERE estado = 'error') AS errores_totales
        FROM log_ejecuciones
        GROUP BY script
        ORDER BY script
    """).fetchdf()


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


def upsert_fact_macro_daily(con: duckdb.DuckDBPyConnection, macro: pd.DataFrame) -> None:
    """Carga masiva set-based, mismo motivo que upsert_fact_precios_daily:
    ~16 series x 5 años de historia diaria es demasiado para executemany()."""
    if macro.empty:
        return
    columnas = ["serie", "fecha", "valor"]
    con.register("macro_temp", macro[columnas])
    con.execute("""
        INSERT INTO fact_macro_daily (serie, fecha, valor)
        SELECT serie, fecha, valor FROM macro_temp
        ON CONFLICT (serie, fecha) DO UPDATE SET valor = excluded.valor
    """)
    con.unregister("macro_temp")


def upsert_fact_consenso_analistas(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    """Una fila por ticker y dia (igual que fact_metrics_daily), ~420 filas
    por corrida -- executemany() alcanza, no hace falta el patron set-based
    de precios_daily/macro_daily."""
    if not filas:
        return
    columnas = [
        "ticker_usd", "fecha", "precio_objetivo_actual", "precio_objetivo_bajo",
        "precio_objetivo_alto", "precio_objetivo_mediana",
        "rec_strong_buy", "rec_buy", "rec_hold", "rec_sell", "rec_strong_sell",
    ]
    placeholders = ", ".join("?" for _ in columnas)
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "fecha"))
    con.executemany(
        f"""
        INSERT INTO fact_consenso_analistas ({", ".join(columnas)}) VALUES ({placeholders})
        ON CONFLICT (ticker_usd, fecha) DO UPDATE SET {actualizaciones}
        """,
        [tuple(f.get(c) for c in columnas) for f in filas],
    )


def titulares_ya_procesados(con: duckdb.DuckDBPyConnection, ticker_usd: str) -> set[str]:
    """Titulares que ya tienen sentimiento calculado para este ticker --
    para no volver a correr FinBERT (lo caro) sobre una noticia ya vista.
    Un titular repetido (mismo texto, reeditado/sindicado por otro medio) es
    el mismo PK, no hace falta reprocesarlo."""
    filas = con.execute(
        "SELECT titular FROM fact_noticias WHERE ticker_usd = ?", [ticker_usd]
    ).fetchall()
    return {f[0] for f in filas}


def upsert_fact_noticias(con: duckdb.DuckDBPyConnection, filas: list[dict]) -> None:
    if not filas:
        return
    columnas = ["ticker_usd", "titular", "fuente", "fecha_publicacion", "link",
                "sentimiento", "score_sentimiento", "fecha_procesado"]
    placeholders = ", ".join("?" for _ in columnas)
    actualizaciones = ", ".join(f"{c} = excluded.{c}" for c in columnas if c not in ("ticker_usd", "titular"))
    con.executemany(
        f"""
        INSERT INTO fact_noticias ({", ".join(columnas)}) VALUES ({placeholders})
        ON CONFLICT (ticker_usd, titular) DO UPDATE SET {actualizaciones}
        """,
        [tuple(f.get(c) for c in columnas) for f in filas],
    )
