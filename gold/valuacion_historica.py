"""
gold/valuacion_historica.py
----------------------------
Capa Gold: ¿esta barata o cara contra SU PROPIA historia? Columnas "val_".

Hasta octubre 2026 solo se podia comparar una empresa contra su sector:
fact_metrics_daily guarda el P/E de Yahoo recien desde el 14/08/2026. Con la
caida de Brasil post-eleccion la pregunta era otra: "¿Bradesco a P/E 7 es
barato para Bradesco?". Esto lo reconstruye con lo que ya hay:

  indice_t = precio de cierre del mes / ganancia neta del ultimo ejercicio
             PUBLICADO a esa fecha (cierre + LAG_PUBLICACION_DIAS, para no
             usar datos que el mercado todavia no conocia)

Es proporcional al P/E mientras la cantidad de acciones no cambie mucho
(recompras o emisiones de unos pocos % por año lo mueven un poco: es una
aproximacion, no el P/E exacto). El precio es el Close de Yahoo, ajustado por
splits pero no por dividendos (ajustar por dividendos bajaria el P/E pasado
artificialmente).

Si la empresa reporta en otra moneda que la de su cotizacion (ADR de Itau:
precio en USD, balance en BRL) la ganancia se pasa a la moneda del precio con
el tipo de cambio de cada mes (series USD<moneda> de macro_diario.py). Si no
hay serie para esa moneda, no se calcula.

Salida (se reescriben enteras, en Python como gold/ondas.py):
  gold_valuacion_historica  ticker, fecha (fin de mes), val_pe_aprox
  gold_valuacion            por ticker: P/E de hoy (Yahoo), mediana/min/max de
                            su historia escalados a ese P/E, percentil de hoy,
                            y val_lectura: "Barata vs su historia" (percentil
                            <= 20), "Cara vs su historia" (>= 80), "En su rango
                            normal", o por que no se pudo calcular.

Correr: python gold/valuacion_historica.py
"""

import sys
from datetime import timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

LAG_PUBLICACION_DIAS = 75
MIN_MESES = 12
PERCENTIL_BARATA, PERCENTIL_CARA = 20, 80


def indice_valuacion(precios_mensuales: pd.Series, ganancias: pd.DataFrame, fx: pd.Series | None = None) -> pd.Series:
    """precios_mensuales: cierre por fin de mes. ganancias: fecha_balance, ganancia_neta.
    fx: unidades de moneda del balance por unidad de moneda del precio, por mes (None si coinciden)."""
    g = ganancias.dropna().copy()
    g["publicada"] = pd.to_datetime(g["fecha_balance"]) + timedelta(days=LAG_PUBLICACION_DIAS)
    g = g.sort_values("publicada")
    resultado = {}
    for fecha, precio in precios_mensuales.items():
        conocidas = g[g["publicada"] <= fecha]
        if conocidas.empty:
            resultado[fecha] = float("nan")
            continue
        ganancia = conocidas["ganancia_neta"].iloc[-1]
        if fx is not None:
            tc = fx.asof(fecha) if hasattr(fx, "asof") else None
            ganancia = ganancia / tc if tc and not pd.isna(tc) else float("nan")
        resultado[fecha] = precio / ganancia if ganancia and ganancia > 0 else float("nan")
    return pd.Series(resultado, dtype=float)


def resumir(indice: pd.Series, pe_hoy: float | None) -> dict:
    serie = indice.dropna()
    if len(serie) < MIN_MESES:
        return {"val_lectura": "Sin historia suficiente", "val_meses": len(serie)}
    hoy = serie.iloc[-1]
    percentil = (serie <= hoy).mean() * 100
    escala = (pe_hoy / hoy) if pe_hoy and pe_hoy > 0 else None
    if percentil <= PERCENTIL_BARATA:
        lectura = "Barata vs su historia"
    elif percentil >= PERCENTIL_CARA:
        lectura = "Cara vs su historia"
    else:
        lectura = "En su rango normal"
    return {
        "val_meses": len(serie),
        "val_percentil_hoy": round(percentil, 1),
        "val_pe_hoy": pe_hoy,
        "val_pe_mediana_hist": round(serie.median() * escala, 2) if escala else None,
        "val_pe_min_hist": round(serie.min() * escala, 2) if escala else None,
        "val_pe_max_hist": round(serie.max() * escala, 2) if escala else None,
        "val_lectura": lectura,
    }


def _fx_mensual(con, moneda_balance: str, moneda_precio: str, meses: pd.DatetimeIndex) -> tuple[pd.Series | None, str | None]:
    """(serie, motivo_si_no_se_puede)."""
    if moneda_balance == moneda_precio:
        return None, None
    if moneda_precio != "USD":
        return None, f"precio en {moneda_precio} y balance en {moneda_balance}: sin conversion"
    serie = con.execute("SELECT fecha, valor FROM fact_macro_daily WHERE serie = ? ORDER BY fecha",
                        [f"USD{moneda_balance}"]).fetchdf()
    if serie.empty:
        return None, f"sin serie USD{moneda_balance}"
    s = serie.set_index(pd.to_datetime(serie["fecha"]))["valor"]
    return s.reindex(s.index.union(meses)).ffill().reindex(meses), None


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_valuacion_historica") as log:
        empresas = con.execute("""
            SELECT d.ticker_usd, d.moneda_cotizacion, d.moneda_balance, c.pe_trailing
            FROM dim_empresa d LEFT JOIN gold_comparables c USING (ticker_usd)
            WHERE d.mercado NOT IN ('cripto', 'etf')
        """).fetchdf()
        precios = con.execute("""
            SELECT ticker_usd, fecha, close FROM fact_precios_daily WHERE close > 0
        """).fetchdf()
        precios["fecha"] = pd.to_datetime(precios["fecha"])
        mensuales = (precios.set_index("fecha").groupby("ticker_usd")["close"].resample("ME").last())
        ganancias = con.execute("SELECT ticker_usd, fecha_balance, ganancia_neta FROM fact_income_statement_annual").fetchdf()

        historia, resumenes = [], []
        for e in empresas.itertuples():
            base = {"ticker_usd": e.ticker_usd}
            if e.ticker_usd not in mensuales.index.get_level_values(0):
                resumenes.append({**base, "val_lectura": "Sin precios"})
                continue
            if pd.isna(e.moneda_balance) or pd.isna(e.moneda_cotizacion):
                resumenes.append({**base, "val_lectura": "Sin moneda (la completa analisis_fundamental_liviano.py)"})
                continue
            p = mensuales.loc[e.ticker_usd].dropna()
            fx, motivo = _fx_mensual(con, e.moneda_balance, e.moneda_cotizacion, p.index)
            if motivo:
                resumenes.append({**base, "val_lectura": f"No calculable: {motivo}"})
                continue
            g = ganancias[ganancias["ticker_usd"] == e.ticker_usd][["fecha_balance", "ganancia_neta"]]
            indice = indice_valuacion(p, g, fx)
            pe_hoy = e.pe_trailing if pd.notna(e.pe_trailing) else None
            r = resumir(indice, pe_hoy)
            resumenes.append({**base, **r})
            serie = indice.dropna()
            if pe_hoy and len(serie):
                escala = pe_hoy / serie.iloc[-1]
                historia += [{"ticker_usd": e.ticker_usd, "fecha": f.date(), "val_pe_aprox": round(v * escala, 2)}
                             for f, v in serie.items()]

        # columnas explicitas: sin monedas cargadas (o sin datos) la historia puede venir vacia
        con.register("vh_temp", pd.DataFrame(historia, columns=["ticker_usd", "fecha", "val_pe_aprox"]))
        con.register("v_temp", pd.DataFrame(resumenes, columns=[
            "ticker_usd", "val_meses", "val_percentil_hoy", "val_pe_hoy", "val_pe_mediana_hist",
            "val_pe_min_hist", "val_pe_max_hist", "val_lectura"]))
        con.execute("CREATE OR REPLACE TABLE gold_valuacion_historica AS SELECT * FROM vh_temp")
        con.execute("CREATE OR REPLACE TABLE gold_valuacion AS SELECT * FROM v_temp")
        con.unregister("vh_temp")
        con.unregister("v_temp")
        log["filas_afectadas"] = len(resumenes)

    print(con.execute("SELECT val_lectura, COUNT(*) FROM gold_valuacion GROUP BY 1 ORDER BY 2 DESC").fetchdf().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
