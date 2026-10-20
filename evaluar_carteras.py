"""
evaluar_carteras.py
--------------------
¿El ranking de seleccionar_cartera.py funciona? Para cada cartera guardada
en fact_cartera_seleccionada compara cuanto rindio (equal-weight, desde el
precio de cierre del dia de la seleccion) contra:
  - la mediana del universo en el mismo periodo
  - el S&P 500 en el mismo periodo

Hasta octubre de 2026 el ranking nunca se habia probado: se armo, parecio
razonable y se uso. No se puede probar hacia atras porque los fundamentales
diarios recien se guardan desde el 14/08/2026, asi que la evaluacion es
hacia adelante: cuantas mas carteras guardadas (run_diario.ps1 guarda una
por semana), mas confiable la respuesta. Con pocas semanas de historia el
resultado es ruido -- lo dice el propio reporte.

Escribe reportes/evaluacion_carteras.md.

Correr: python evaluar_carteras.py
"""

from pathlib import Path

import pandas as pd

import db

MIN_DIAS_PARA_OPINAR = 63  # ~3 meses: antes de eso cualquier diferencia es ruido
DIR_REPORTES = Path("reportes")

SQL = """
WITH ultimo AS (
    SELECT ticker_usd, adj_close AS precio_hoy, fecha AS fecha_hoy
    FROM fact_precios_daily WHERE adj_close IS NOT NULL
    QUALIFY ROW_NUMBER() OVER (PARTITION BY ticker_usd ORDER BY fecha DESC) = 1
),
cartera AS (
    SELECT c.fecha_seleccion, c.origen,
           AVG(u.precio_hoy / c.precio_seleccion - 1) * 100 AS cartera_pct,
           COUNT(*) AS empresas, MAX(u.fecha_hoy) AS hasta
    FROM fact_cartera_seleccionada c JOIN ultimo u USING (ticker_usd)
    GROUP BY 1, 2
),
universo AS (
    SELECT c.fecha_seleccion, MEDIAN(u.precio_hoy / p.adj_close - 1) * 100 AS universo_mediana_pct
    FROM (SELECT DISTINCT fecha_seleccion FROM fact_cartera_seleccionada) c
    JOIN fact_precios_daily p ON p.fecha = (
        SELECT MAX(fecha) FROM fact_precios_daily WHERE fecha <= c.fecha_seleccion AND ticker_usd = 'AAPL')
    JOIN dim_empresa d ON d.ticker_usd = p.ticker_usd AND d.mercado != 'cripto'
    JOIN ultimo u ON u.ticker_usd = p.ticker_usd
    WHERE p.adj_close > 0
    GROUP BY 1
),
sp AS (
    SELECT c.fecha_seleccion,
           ((SELECT LAST(valor ORDER BY fecha) FROM fact_macro_daily WHERE serie = 'SP500')
            / (SELECT LAST(valor ORDER BY fecha) FROM fact_macro_daily WHERE serie = 'SP500' AND fecha <= c.fecha_seleccion) - 1) * 100 AS sp500_pct
    FROM (SELECT DISTINCT fecha_seleccion FROM fact_cartera_seleccionada) c
)
SELECT c.fecha_seleccion, c.origen, c.empresas, c.hasta,
       DATE_DIFF('day', c.fecha_seleccion, c.hasta) AS dias,
       ROUND(c.cartera_pct, 1) AS cartera_pct,
       ROUND(u.universo_mediana_pct, 1) AS universo_mediana_pct,
       ROUND(s.sp500_pct, 1) AS sp500_pct,
       ROUND(c.cartera_pct - u.universo_mediana_pct, 1) AS vs_universo_pp
FROM cartera c JOIN universo u USING (fecha_seleccion) JOIN sp s USING (fecha_seleccion)
ORDER BY c.fecha_seleccion, c.origen
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "evaluar_carteras") as log:
        df = con.execute(SQL).fetchdf()
        log["filas_afectadas"] = len(df)

    if df.empty:
        print("No hay carteras guardadas todavia.")
        return
    maduras = df[df["dias"] >= MIN_DIAS_PARA_OPINAR]
    lineas = ["# Evaluación de carteras", "", "```", df.to_string(index=False), "```", ""]
    if maduras.empty:
        lineas.append(f"Ninguna cartera tiene todavía {MIN_DIAS_PARA_OPINAR} días: cualquier diferencia es ruido, no conclusión.")
    else:
        gana = (maduras["vs_universo_pp"] > 0).mean() * 100
        lineas.append(f"Carteras con {MIN_DIAS_PARA_OPINAR}+ días: {len(maduras)} | le ganaron al universo: {gana:.0f}% | "
                      f"diferencia mediana: {maduras['vs_universo_pp'].median():+.1f} pp")
    DIR_REPORTES.mkdir(exist_ok=True)
    (DIR_REPORTES / "evaluacion_carteras.md").write_text("\n".join(lineas), encoding="utf-8")
    print("\n".join(lineas))
    con.close()


if __name__ == "__main__":
    main()
