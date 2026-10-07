"""
seleccionar_cartera.py
------------------------
Selecciona una cartera concentrada (7-9 empresas por default) cruzando
TODAS las metodologías gold que ya tenemos -- no es un script de pipeline
(no corre solo, no escribe al warehouse), es una herramienta a demanda:
"con todo lo que sabemos hoy, ¿cuáles elegimos?".

Proceso (4 pasos, mismo orden que se charló antes de armar esto):

  1. FILTRO DE CALIDAD -- saca lo que no sirve, sin importar cuán "barato"
     parezca:
       - gold_salud_financiera.salud_clasificacion != 'Riesgo'
       - crecimiento_ganancia_anual_pct > -20% (mismo umbral que usa
         gold_lynch para la categoría "Recuperable" -- evita las trampas
         tipo MGLU3/HAPV3 que vimos en Brasil: checklist alto, pero el
         negocio se cayó)
       - margen_neto_pct > 0 (gana plata)

  2. RANKING por calidad + valor, usando percentil (0-1, robusto a
     escalas distintas) de tres señales, promediadas:
       - lynch_checklist_score (mas alto, mejor)
       - -comp_pe_vs_sector_pct (mas barata vs. su sector real, mejor)
       - salud_score / salud_evaluables (ratio, no castiga a bancos que
         no tienen los 5 criterios evaluables)

  3. CRUCE EXTERNO -- no suma puntos al ranking, solo marca alertas para
     mirar a mano:
       - consenso de analistas con upside negativo o mayoría "sell"
       - sentimiento de noticias "Negativo"

  4. DIVERSIFICACIÓN SECTORIAL *Y* REGIONAL -- recorre el ranking de
     arriba hacia abajo, tope de `MAX_POR_SECTOR` por sector Y
     `MAX_POR_REGION` por región, hasta completar `TAMANIO_CARTERA`.

     La región no sale de `dim_empresa.pais_origen` tal cual -- ese campo
     es el domicilio LEGAL/fiscal de Yahoo, no necesariamente dónde está
     el negocio real (mismo problema que encontramos con Vista Energy:
     domicilio México, negocio Vaca Muerta/Argentina). Se vio corriendo
     portfolio_analytics.ipynb: PDD y TCOM (ambas 100% negocio chino)
     figuran como "Ireland"/"Singapore" en pais_origen, y terminaron
     correlacionando 0.59 entre sí sin que el tope por sector lo
     detectara (están en sectores GICS distintos). REGION_REAL corrige
     esos casos conocidos a mano -- mismo patrón que
     correcciones_sector.py, pero acá vive en este archivo porque es
     chico y específico a la selección de cartera, no al warehouse.

     Para `mercado IN ('argentina_local', 'brasil')` se usa el panel
     entero como región (todas las locales de un país comparten el mismo
     tipo de cambio/macro, no tiene sentido separarlas por sector GICS
     para este propósito).

Correr: python seleccionar_cartera.py [tamanio]   (default 9)
"""

import sys

import pandas as pd

import db

TAMANIO_CARTERA_DEFAULT = 9
MAX_POR_SECTOR = 2
MAX_POR_REGION = 2
UMBRAL_GANANCIA_COLAPSADA = -20  # mismo que gold/lynch.py usa para "Recuperable"

# Casos donde pais_origen de Yahoo es el domicilio legal/fiscal, no el pais
# real del negocio -- agregar acá si aparece un caso nuevo (mismo criterio
# que correcciones_sector.py: no inventar, solo corregir lo verificado).
REGION_REAL = {
    "PDD": "China",
    "TCOM": "China",
}


def region_de(fila) -> str:
    if fila["mercado"] in ("argentina_local", "brasil"):
        return fila["mercado"]  # el panel local entero comparte macro/FX, no tiene sentido separarlo por sector
    return REGION_REAL.get(fila["ticker_usd"], fila["pais_origen"])


def obtener_candidatos(con) -> pd.DataFrame:
    q = """
    SELECT
        l.ticker_usd, l.nombre_empresa, l.sector, l.mercado, l.pais_origen,
        l.lynch_categoria_auto, l.lynch_checklist_score,
        l.crecimiento_ingresos_anual_pct, l.crecimiento_ganancia_anual_pct,
        c.comp_pe_vs_sector_pct, c.comp_percentil_crecimiento_en_sector,
        s.salud_score, s.salud_evaluables, s.salud_clasificacion, s.margen_neto_pct,
        t.tech_rsi14, t.tech_rsi_senal, t.tech_tendencia,
        ca.precio_objetivo_mediana, ca.rec_strong_buy, ca.rec_buy, ca.rec_hold, ca.rec_sell, ca.rec_strong_sell,
        sent.sent_score_promedio, sent.sent_clasificacion,
        p.dias_precio, p.vol_prom_30d
    FROM gold_lynch l
    JOIN gold_comparables c USING (ticker_usd)
    JOIN gold_salud_financiera s USING (ticker_usd)
    LEFT JOIN gold_technical t USING (ticker_usd)
    LEFT JOIN (
        SELECT * FROM fact_consenso_analistas
        QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
    ) ca USING (ticker_usd)
    LEFT JOIN gold_sentimiento sent USING (ticker_usd)
    LEFT JOIN (
        -- dias_precio: para detectar tickers con poca historia (no alcanza
        -- para volatilidad/correlacion/drawdown a 5 años en el paso de
        -- portfolio analytics). vol_prom_30d: liquidez real -- una empresa
        -- puede pasar todo el screening fundamental y ser impracticable de
        -- operar en una cartera concentrada si nadie la opera.
        SELECT ticker_usd,
               COUNT(*) AS dias_precio,
               AVG(volume) FILTER (WHERE fecha >= CURRENT_DATE - 30) AS vol_prom_30d
        FROM fact_precios_daily
        WHERE adj_close IS NOT NULL
        GROUP BY ticker_usd
    ) p USING (ticker_usd)
    WHERE l.mercado != 'cripto'
    """
    return con.execute(q).fetchdf()


def aplicar_filtro_calidad(df: pd.DataFrame) -> pd.DataFrame:
    return df[
        (df["salud_clasificacion"] != "Riesgo")
        & (df["crecimiento_ganancia_anual_pct"] > UMBRAL_GANANCIA_COLAPSADA)
        & (df["margen_neto_pct"] > 0)
    ].copy()


def calcular_ranking(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["pct_checklist"] = df["lynch_checklist_score"].rank(pct=True)
    df["pct_barata"] = (-df["comp_pe_vs_sector_pct"]).rank(pct=True)
    df["salud_ratio"] = df["salud_score"] / df["salud_evaluables"].replace(0, pd.NA)
    df["pct_salud"] = df["salud_ratio"].rank(pct=True)
    df["score_compuesto"] = df[["pct_checklist", "pct_barata", "pct_salud"]].mean(axis=1)
    return df.sort_values("score_compuesto", ascending=False)


UMBRAL_DIAS_PRECIO = 1000  # ~4 años -- menos que esto no alcanza para vol/correlacion/drawdown a 5y confiables
UMBRAL_VOLUMEN_BAJO = 50_000  # acciones/dia promedio ultimos 30 dias -- por debajo, dificil de operar en una cartera concentrada


def marcar_alertas_externas(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    ventas = df["rec_sell"].fillna(0) + df["rec_strong_sell"].fillna(0)
    compras = df["rec_buy"].fillna(0) + df["rec_strong_buy"].fillna(0)
    df["alerta_analistas"] = ventas > compras
    df["alerta_sentimiento"] = df["sent_clasificacion"] == "Negativo"
    df["alerta_historia_corta"] = df["dias_precio"] < UMBRAL_DIAS_PRECIO
    df["alerta_liquidez_baja"] = df["vol_prom_30d"] < UMBRAL_VOLUMEN_BAJO
    return df


def elegir_diversificado(df: pd.DataFrame, tamanio: int, max_por_sector: int, max_por_region: int = MAX_POR_REGION) -> pd.DataFrame:
    elegidos = []
    conteo_sector: dict[str, int] = {}
    conteo_region: dict[str, int] = {}
    for _, fila in df.iterrows():
        if len(elegidos) >= tamanio:
            break
        sector = fila["sector"]
        region = region_de(fila)
        if conteo_sector.get(sector, 0) >= max_por_sector:
            continue
        if conteo_region.get(region, 0) >= max_por_region:
            continue
        elegidos.append(fila)
        conteo_sector[sector] = conteo_sector.get(sector, 0) + 1
        conteo_region[region] = conteo_region.get(region, 0) + 1
    resultado = pd.DataFrame(elegidos)
    if not resultado.empty:
        resultado["region"] = resultado.apply(region_de, axis=1)
    return resultado


def main() -> None:
    tamanio = int(sys.argv[1]) if len(sys.argv) > 1 else TAMANIO_CARTERA_DEFAULT

    con = db.conectar()
    candidatos = obtener_candidatos(con)
    con.close()

    print(f"Universo: {len(candidatos)} empresas")
    filtrados = aplicar_filtro_calidad(candidatos)
    print(f"Pasan el filtro de calidad (paso 1): {len(filtrados)}")

    rankeados = calcular_ranking(filtrados)
    rankeados = marcar_alertas_externas(rankeados)

    elegidos = elegir_diversificado(rankeados, tamanio, MAX_POR_SECTOR)

    print(f"\n=== Cartera seleccionada ({len(elegidos)} empresas, max {MAX_POR_SECTOR} por sector, max {MAX_POR_REGION} por región) ===\n")
    cols = [
        "ticker_usd", "nombre_empresa", "sector", "region", "score_compuesto",
        "lynch_checklist_score", "comp_pe_vs_sector_pct", "salud_score", "salud_evaluables",
        "dias_precio", "tech_rsi_senal",
    ]
    print(elegidos[cols].round(3).to_string(index=False))

    alertas = elegidos[
        elegidos["alerta_analistas"] | elegidos["alerta_sentimiento"]
        | elegidos["alerta_historia_corta"] | elegidos["alerta_liquidez_baja"]
    ]
    if not alertas.empty:
        print("\nRevisar a mano (pasaron el ranking pero tienen alerta externa):")
        for _, f in alertas.iterrows():
            motivos = []
            if f["alerta_analistas"]:
                motivos.append("mayoría de analistas en sell/strong sell")
            if f["alerta_sentimiento"]:
                motivos.append(f"sentimiento negativo (score {f['sent_score_promedio']:.2f})")
            if f["alerta_historia_corta"]:
                motivos.append(f"solo {int(f['dias_precio'])} días de historial de precio (< {UMBRAL_DIAS_PRECIO})")
            if f["alerta_liquidez_baja"]:
                motivos.append(f"volumen promedio bajo ({f['vol_prom_30d']:.0f} acciones/día)")
            print(f"  {f['ticker_usd']}: {', '.join(motivos)}")

    print(f"\nTickers: {', '.join(elegidos['ticker_usd'].tolist())}")


if __name__ == "__main__":
    main()
