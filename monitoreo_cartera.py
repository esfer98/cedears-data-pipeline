"""
monitoreo_cartera.py
---------------------
Reporte diario de la ultima cartera guardada en fact_cartera_seleccionada.
Corre al final de run_diario.ps1, cuando ya estan los datos del dia.

Existe porque SK Hynix cayo 25% en 8 ruedas (octubre 2026) y nos enteramos
porque el usuario lo vio por su cuenta: nada miraba la cartera despues de
elegirla. Esto NO vende ni cambia nada: avisa para revisar.

Alertas por empresa:
  - caida de mas de CAIDA_SEMANA_PCT en las ultimas 5 ruedas
  - caida de mas de CAIDA_DESDE_SELECCION_PCT desde el precio de seleccion
  - RSI extremo (<= RSI_BAJO o >= RSI_ALTO)
  - presenta resultados en los proximos DIAS_RESULTADOS dias
  - sentimiento de noticias negativo
  - error de datos en silver_calidad_datos (ese dato ya no es confiable)

Escribe reportes/monitoreo_<fecha>.md y, si hay alertas, reportes/alertas_hoy.txt
(run_diario.ps1 lo usa para mandar una notificacion de Windows).

Correr: python monitoreo_cartera.py
"""

from datetime import date
from pathlib import Path

import pandas as pd

import db

CAIDA_SEMANA_PCT = -10
CAIDA_DESDE_SELECCION_PCT = -15
RSI_BAJO, RSI_ALTO = 25, 80
DIAS_RESULTADOS = 7
DIR_REPORTES = Path("reportes")

SQL = """
WITH cartera AS (
    SELECT * FROM fact_cartera_seleccionada
    WHERE (fecha_seleccion, origen) = (
        SELECT (fecha_seleccion, origen) FROM fact_cartera_seleccionada
        ORDER BY fecha_seleccion DESC, CASE origen WHEN 'manual' THEN 0 ELSE 1 END LIMIT 1
    )
),
px AS (
    SELECT ticker_usd, fecha, adj_close,
           LAG(adj_close, 5) OVER (PARTITION BY ticker_usd ORDER BY fecha) AS precio_hace_5
    FROM fact_precios_daily WHERE adj_close IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
),
resultados AS (
    SELECT ticker_usd, MIN(fecha_reporte) AS proximo_reporte
    FROM fact_eps_trimestral
    WHERE eps_reportado IS NULL AND fecha_reporte >= CURRENT_DATE
    GROUP BY ticker_usd
),
calidad AS (
    SELECT ticker_usd, STRING_AGG(regla, ', ') AS errores_datos
    FROM silver_calidad_datos WHERE severidad = 'error' GROUP BY ticker_usd
)
SELECT c.fecha_seleccion, c.origen, c.puesto, c.ticker_usd, d.nombre_empresa,
       c.precio_seleccion, px.fecha AS fecha_precio, px.adj_close AS precio_actual,
       ROUND((px.adj_close / c.precio_seleccion - 1) * 100, 1) AS desde_seleccion_pct,
       ROUND((px.adj_close / px.precio_hace_5 - 1) * 100, 1) AS ultimas_5_ruedas_pct,
       t.tech_rsi14, t.tech_dist_max_52s_pct,
       r.proximo_reporte, s.sent_clasificacion, q.errores_datos,
       rf.refugio_segmento
FROM cartera c
JOIN dim_empresa d USING (ticker_usd)
LEFT JOIN px USING (ticker_usd)
LEFT JOIN gold_technical t USING (ticker_usd)
LEFT JOIN resultados r USING (ticker_usd)
LEFT JOIN gold_sentimiento s USING (ticker_usd)
LEFT JOIN calidad q USING (ticker_usd)
LEFT JOIN gold_refugio rf USING (ticker_usd)
ORDER BY c.puesto
"""


def alertas_de(f) -> list[str]:
    a = []
    if pd.notna(f.ultimas_5_ruedas_pct) and f.ultimas_5_ruedas_pct <= CAIDA_SEMANA_PCT:
        a.append(f"cayó {f.ultimas_5_ruedas_pct}% en 5 ruedas")
    if pd.notna(f.desde_seleccion_pct) and f.desde_seleccion_pct <= CAIDA_DESDE_SELECCION_PCT:
        a.append(f"{f.desde_seleccion_pct}% desde la selección")
    if pd.notna(f.tech_rsi14) and f.tech_rsi14 <= RSI_BAJO:
        a.append(f"RSI {f.tech_rsi14} (sobreventa fuerte)")
    if pd.notna(f.tech_rsi14) and f.tech_rsi14 >= RSI_ALTO:
        a.append(f"RSI {f.tech_rsi14} (sobrecompra fuerte)")
    if pd.notna(f.proximo_reporte) and (pd.Timestamp(f.proximo_reporte).date() - date.today()).days <= DIAS_RESULTADOS:
        a.append(f"presenta resultados el {pd.Timestamp(f.proximo_reporte).date()}")
    if f.sent_clasificacion == "Negativo":
        a.append("noticias negativas")
    if pd.notna(f.errores_datos):
        a.append(f"error de datos: {f.errores_datos}")
    return a


def contexto_mercado(con) -> str:
    m = con.execute("""
        SELECT serie, LAST(valor ORDER BY fecha) AS ultimo, MAX(valor) AS maximo
        FROM fact_macro_daily
        WHERE serie IN ('SP500', 'VIX', 'MERVAL') AND fecha >= CURRENT_DATE - 365
        GROUP BY serie
    """).fetchdf().set_index("serie")
    partes = []
    if "SP500" in m.index:
        partes.append(f"S&P 500 {(m.loc['SP500', 'ultimo'] / m.loc['SP500', 'maximo'] - 1) * 100:.1f}% desde su máximo de 12 meses")
    if "MERVAL" in m.index:
        partes.append(f"Merval {(m.loc['MERVAL', 'ultimo'] / m.loc['MERVAL', 'maximo'] - 1) * 100:.1f}% desde su máximo")
    if "VIX" in m.index:
        partes.append(f"VIX {m.loc['VIX', 'ultimo']:.1f}")
    return " | ".join(partes)


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "monitoreo_cartera") as log:
        df = con.execute(SQL).fetchdf()
        if df.empty:
            print("No hay ninguna cartera guardada (seleccionar_cartera.py --guardar=manual).")
            log["filas_afectadas"] = 0
            return
        df["alertas"] = df.apply(alertas_de, axis=1)
        con_alerta = df[df["alertas"].str.len() > 0]
        log["filas_afectadas"] = len(con_alerta)

        hoy = date.today()
        cab = df.iloc[0]
        lineas = [
            f"# Monitoreo de cartera — {hoy}",
            "",
            f"Cartera del {cab.fecha_seleccion.date() if hasattr(cab.fecha_seleccion, 'date') else cab.fecha_seleccion} (origen: {cab.origen}). {contexto_mercado(con)}",
            "",
            "| # | Empresa | Desde selección | Últimas 5 ruedas | RSI | Desde máx. 52s | Refugio |",
            "|---|---|---|---|---|---|---|",
        ]
        for _, f in df.iterrows():
            lineas.append(f"| {f.puesto} | {f.ticker_usd} — {f.nombre_empresa} | {f.desde_seleccion_pct}% | "
                          f"{f.ultimas_5_ruedas_pct}% | {f.tech_rsi14} | {f.tech_dist_max_52s_pct}% | {f.refugio_segmento if pd.notna(f.refugio_segmento) else '— (no mide panel local)'} |")
        lineas += ["", "## Para revisar", ""]
        if con_alerta.empty:
            lineas.append("Sin alertas.")
        for _, f in con_alerta.iterrows():
            lineas.append(f"- **{f.ticker_usd}**: {'; '.join(f.alertas)}")
        promedio = df["desde_seleccion_pct"].mean()
        lineas += ["", f"Cartera equal-weight desde la selección: {promedio:.1f}%"]

        DIR_REPORTES.mkdir(exist_ok=True)
        (DIR_REPORTES / f"monitoreo_{hoy}.md").write_text("\n".join(lineas), encoding="utf-8")
        alertas_hoy = DIR_REPORTES / "alertas_hoy.txt"
        if con_alerta.empty:
            alertas_hoy.unlink(missing_ok=True)
        else:
            alertas_hoy.write_text(
                f"{len(con_alerta)} de {len(df)} con alertas: " + ", ".join(con_alerta["ticker_usd"]), encoding="utf-8")

    print("\n".join(lineas))
    con.close()


if __name__ == "__main__":
    main()
