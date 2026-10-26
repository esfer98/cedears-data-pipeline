"""
gold/refugio.py
----------------
Capa Gold: ¿que empresas sirvieron de refugio cuando cayo la bolsa?
Columnas prefijadas "refugio_".

No usa opiniones del tipo "consumo basico es defensivo": mide como se
comporto cada empresa en las caidas reales del S&P 500 que hay en
fact_precios_daily. Como un episodio depende de detectar pico y piso sobre
toda la serie, se calcula en Python y se materializa (igual que
gold/ondas.py) en dos tablas que se reescriben enteras:

  gold_refugio_episodios  -- un episodio por fila: cada caida del S&P de mas
                             de UMBRAL_EPISODIO desde su maximo previo, de
                             pico a piso, y cuanto hizo cada empresa en ese
                             mismo tramo.
  gold_refugio            -- una fila por empresa con historia suficiente:

    refugio_beta              cuanto se mueve contra el S&P en general
    refugio_beta_bajista      lo mismo, solo en los dias en que el S&P cae
    refugio_captura_bajista   promedio de la empresa en dias de baja del S&P
                              dividido el promedio del S&P esos dias: 1 = cae
                              igual, 0 = no cae, negativo = sube. La mediana
                              del universo es ~1.
    refugio_ep_mediana / refugio_ep_peor
                              retorno mediano y peor en los episodios de caida
    refugio_max_dd            peor caida propia en toda la ventana
    refugio_segmento          "Refugio": captura <= 0.3 y nunca perdio mas de
                              10% en un episodio.
                              "Defensiva": captura <= 0.6.
                              "No es refugio": el resto.

Mide mercado = 'usa_otros' y 'etf' (CEDEARs, ADR y ETF en dolares: GLD,
COPX, SPY...). No mide los paneles locales: el panel
argentino y las BDR brasileñas cotizan en su moneda y siguen a su propio
mercado, asi que su baja correlacion con el S&P no es refugio sino otro
mercado (y las de cotizacion muerta, como B-Gaming, salian como "refugio"
porque su precio no se mueve).

Limites: en la ventana hay pocos episodios (en octubre de 2026: el bear
market de 2022, de inflacion y tasas, y la caida por aranceles de 2025).
Un refugio de un tipo de crisis puede no serlo en otra: la energia subio
fuerte en 2022 y cayo en 2025. Por eso el segmento exige el PEOR episodio,
no el promedio. Y lo que fue refugio en el pasado no garantiza nada si hoy
cotiza cara o en maximos -- mirar junto con gold_comparables/gold_technical.

Correr: python gold/refugio.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

UMBRAL_EPISODIO = -0.10
MIN_DIAS = 750  # ~3 años: con menos la empresa no estuvo en ningun episodio
CAPTURA_REFUGIO = 0.3
CAPTURA_DEFENSIVA = 0.6
PEOR_EPISODIO_REFUGIO = -10.0


def detectar_episodios(sp: pd.Series) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    """(pico, piso) de cada caida del S&P mayor a UMBRAL_EPISODIO desde su maximo previo."""
    dd = sp / sp.cummax() - 1
    episodios, en_caida, pico = [], False, None
    for fecha, v in dd.items():
        if not en_caida and v <= UMBRAL_EPISODIO:
            en_caida, pico = True, sp[:fecha].idxmax()
        if en_caida and v == 0:
            episodios.append((pico, sp[pico:fecha].idxmin()))
            en_caida = False
    if en_caida:
        episodios.append((pico, sp[pico:].idxmin()))
    return episodios


MERCADOS_REFUGIO = ("usa_otros", "etf")


def precios_para_refugio(con) -> pd.DataFrame:
    return con.execute(f"""
        SELECT p.ticker_usd, p.fecha, p.adj_close
        FROM fact_precios_daily p JOIN dim_empresa d USING (ticker_usd)
        WHERE p.adj_close > 0 AND d.mercado IN {MERCADOS_REFUGIO}
    """).fetchdf()


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_refugio") as log:
        sp = con.execute("SELECT fecha, valor FROM fact_macro_daily WHERE serie = 'SP500' ORDER BY fecha").fetchdf()
        sp = sp.set_index(pd.to_datetime(sp["fecha"]))["valor"]
        episodios = detectar_episodios(sp)

        px = precios_para_refugio(con)
        px["fecha"] = pd.to_datetime(px["fecha"])
        # Se alinea al calendario del S&P (ffill de hasta 3 dias para ADR con feriados de su pais).
        precios = px.pivot(index="fecha", columns="ticker_usd", values="adj_close").sort_index().reindex(sp.index).ffill(limit=3)
        retornos = precios.pct_change(fill_method=None)
        r_sp = sp.pct_change()

        filas, filas_ep = [], []
        for ticker in precios.columns:
            r = pd.concat([retornos[ticker], r_sp], axis=1, keys=["a", "m"]).dropna()
            if len(r) < MIN_DIAS:
                continue
            abajo = r[r["m"] < 0]
            serie = precios[ticker].dropna()
            rets_ep = []
            for pico, piso in episodios:
                if serie.index.min() <= pico:
                    v = (serie.asof(piso) / serie.asof(pico) - 1) * 100
                    rets_ep.append(v)
                    filas_ep.append({"ticker_usd": ticker, "episodio_pico": pico.date(), "episodio_piso": piso.date(),
                                     "retorno_sp_pct": round((sp[piso] / sp[pico] - 1) * 100, 1), "retorno_pct": round(v, 1)})
            captura = abajo["a"].mean() / abajo["m"].mean()
            peor = min(rets_ep) if rets_ep else np.nan
            if captura <= CAPTURA_REFUGIO and rets_ep and peor >= PEOR_EPISODIO_REFUGIO:
                segmento = "Refugio"
            elif captura <= CAPTURA_DEFENSIVA:
                segmento = "Defensiva"
            else:
                segmento = "No es refugio"
            filas.append({
                "ticker_usd": ticker,
                "refugio_dias": len(r),
                "refugio_beta": round(r.cov().iloc[0, 1] / r["m"].var(), 2),
                "refugio_beta_bajista": round(abajo.cov().iloc[0, 1] / abajo["m"].var(), 2),
                "refugio_captura_bajista": round(captura, 2),
                "refugio_corr_sp": round(r.corr().iloc[0, 1], 2),
                "refugio_episodios": len(rets_ep),
                "refugio_ep_mediana_pct": round(float(np.median(rets_ep)), 1) if rets_ep else None,
                "refugio_ep_peor_pct": round(peor, 1) if rets_ep else None,
                "refugio_max_dd_pct": round((serie / serie.cummax() - 1).min() * 100, 1),
                "refugio_segmento": segmento,
            })

        con.register("refugio_temp", pd.DataFrame(filas))
        con.register("refugio_ep_temp", pd.DataFrame(filas_ep))
        con.execute("CREATE OR REPLACE TABLE gold_refugio AS SELECT * FROM refugio_temp")
        con.execute("CREATE OR REPLACE TABLE gold_refugio_episodios AS SELECT * FROM refugio_ep_temp")
        con.unregister("refugio_temp")
        con.unregister("refugio_ep_temp")
        log["filas_afectadas"] = len(filas)

    print("=== Episodios de caida del S&P detectados ===")
    for pico, piso in episodios:
        print(f"  {pico.date()} -> {piso.date()}: {(sp[piso] / sp[pico] - 1) * 100:.1f}%")
    print("\n=== Empresas por segmento ===")
    print(con.execute("SELECT refugio_segmento, COUNT(*) AS empresas FROM gold_refugio GROUP BY 1 ORDER BY 2").fetchdf().to_string(index=False))
    print("\n=== Refugios ===")
    print(con.execute("""
        SELECT r.ticker_usd, d.nombre_empresa, COALESCE(d.sector_corregido, d.sector) AS sector,
               r.refugio_captura_bajista, r.refugio_ep_mediana_pct, r.refugio_ep_peor_pct, r.refugio_max_dd_pct
        FROM gold_refugio r JOIN dim_empresa d USING (ticker_usd)
        WHERE r.refugio_segmento = 'Refugio' ORDER BY r.refugio_captura_bajista
    """).fetchdf().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
