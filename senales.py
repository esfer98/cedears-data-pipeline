"""
senales.py
-----------
Senales de compra para acciones en seguimiento que vienen de una caida fuerte
(Brasil despues de la eleccion de octubre 2026). Ninguna senal alone dice
"comprar": juntas describen si la caida esta terminando. monitoreo_cartera.py
las evalua todos los dias y avisa cuando hay MINIMO_PARA_AVISO activas.

  moneda_estable        el dolar contra la moneda local no subio en las
                        ultimas 5 ruedas. Para un CEDEAR, mientras la moneda
                        siga cayendo el precio en dolares cae aunque la
                        accion se estabilice en su bolsa.
  capitulacion          en las ultimas 10 ruedas hubo un dia con 3 veces el
                        volumen normal (90 ruedas) y cierre en la mitad alta
                        del rango del dia: vendieron en panico y alguien
                        compro todo.
  dejo_de_hacer_minimos el minimo de 20 ruedas esta al menos 15% abajo del maximo
                        de las 60 ruedas previas (hubo una caida), quedo al
                        menos 3 ruedas atras y el precio ya rebota 3% desde ahi.
  post_reporte          reporto en los ultimos 10 dias y el precio no esta mas
                        de 3% abajo del cierre previo al reporte.
  rsi_recupera          el RSI de 14 estuvo debajo de 30 en las ultimas 15
                        ruedas y hoy esta arriba de 30.

Todas se calculan "a una fecha" (hoy, por default) para poder testearlas y
reconstruirlas hacia atras.
"""

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

MINIMO_PARA_AVISO = 3


@dataclass
class Senal:
    activa: bool
    detalle: str

    def __post_init__(self):
        self.activa = bool(self.activa)  # numpy devuelve np.bool_; se normaliza para comparar y serializar


def _precios(con, ticker: str, hoy: date, ruedas: int) -> pd.DataFrame:
    df = con.execute("""
        SELECT fecha, high, low, close, adj_close, volume FROM fact_precios_daily
        WHERE ticker_usd = ? AND fecha <= ? AND adj_close IS NOT NULL
        ORDER BY fecha DESC LIMIT ?
    """, [ticker, hoy, ruedas]).fetchdf()
    return df.iloc[::-1].reset_index(drop=True)


def moneda_estable(con, serie: str, hoy: date | None = None) -> Senal:
    hoy = hoy or date.today()
    v = con.execute("""
        SELECT valor FROM fact_macro_daily WHERE serie = ? AND fecha <= ?
        ORDER BY fecha DESC LIMIT 6
    """, [serie, hoy]).fetchdf()["valor"].iloc[::-1]
    if len(v) < 6:
        return Senal(False, f"sin datos suficientes de {serie}")
    cambio = (v.iloc[-1] / v.iloc[0] - 1) * 100
    return Senal(cambio <= 0, f"{serie} {cambio:+.1f}% en 5 ruedas")


def capitulacion(con, ticker: str, hoy: date | None = None) -> Senal:
    hoy = hoy or date.today()
    p = _precios(con, ticker, hoy, 100)
    if len(p) < 40:
        return Senal(False, "historia insuficiente")
    p["vol_prom"] = p["volume"].shift(1).rolling(90, min_periods=30).mean()
    rango = (p["high"] - p["low"]).replace(0, pd.NA)
    p["pos_cierre"] = (p["close"] - p["low"]) / rango
    ult = p.tail(10)
    dias = ult[(ult["volume"] >= 3 * ult["vol_prom"]) & (ult["pos_cierre"] >= 0.5)]
    if dias.empty:
        return Senal(False, "sin dia de volumen extremo con rebote")
    d = dias.iloc[-1]
    return Senal(True, f"{pd.Timestamp(d.fecha).date()}: {d.volume / d.vol_prom:.1f}x volumen, cerró en el {d.pos_cierre:.0%} alto del día")


CAIDA_PREVIA_MINIMA_PCT = 15


def dejo_de_hacer_minimos(con, ticker: str, hoy: date | None = None) -> Senal:
    """Solo cuenta si antes hubo una caida: sin eso, cualquier suba "dejo de hacer
    minimos" (el 26/10/2026 el rally pre-eleccion la activaba en todas)."""
    hoy = hoy or date.today()
    p = _precios(con, ticker, hoy, 80)
    if len(p) < 10:
        return Senal(False, "historia insuficiente")
    ventana = p.tail(20)
    i_min = int(ventana["adj_close"].idxmin())
    minimo = p["adj_close"].iloc[i_min]
    maximo_previo = p["adj_close"].iloc[max(0, i_min - 60):i_min + 1].max()
    caida = (minimo / maximo_previo - 1) * 100
    ruedas_desde_min = len(p) - 1 - i_min
    rebote = (p["adj_close"].iloc[-1] / minimo - 1) * 100
    activa = caida <= -CAIDA_PREVIA_MINIMA_PCT and ruedas_desde_min >= 3 and rebote >= 3
    return Senal(activa, f"caída previa {caida:.0f}%, mínimo hace {ruedas_desde_min} ruedas, rebote {rebote:+.1f}%")


def post_reporte(con, ticker: str, hoy: date | None = None) -> Senal:
    hoy = hoy or date.today()
    fila = con.execute("""
        SELECT MAX(fecha_reporte) FROM fact_eps_trimestral
        WHERE ticker_usd = ? AND fecha_reporte <= ? AND fecha_reporte >= ?
    """, [ticker, hoy, hoy - timedelta(days=10)]).fetchone()
    if fila[0] is None:
        return Senal(False, "no reportó en los últimos 10 días")
    reporte = fila[0]
    pre = con.execute("""
        SELECT adj_close FROM fact_precios_daily WHERE ticker_usd = ? AND fecha < ?
        ORDER BY fecha DESC LIMIT 1
    """, [ticker, reporte]).fetchone()
    ult = _precios(con, ticker, hoy, 1)
    if pre is None or ult.empty:
        return Senal(False, "sin precios alrededor del reporte")
    cambio = (ult["adj_close"].iloc[-1] / pre[0] - 1) * 100
    return Senal(cambio >= -3, f"reportó el {reporte}, {cambio:+.1f}% desde el cierre previo")


def _rsi(cierres: pd.Series, n: int = 14) -> pd.Series:
    delta = cierres.diff()
    ganancia = delta.clip(lower=0).rolling(n).mean()
    perdida = (-delta.clip(upper=0)).rolling(n).mean()
    rsi = 100 - 100 / (1 + ganancia / perdida.where(perdida != 0))
    # sin ninguna rueda en baja la division no existe: por definicion el RSI es 100
    return rsi.mask((perdida == 0) & (ganancia > 0), 100.0)


def rsi_recupera(con, ticker: str, hoy: date | None = None) -> Senal:
    hoy = hoy or date.today()
    p = _precios(con, ticker, hoy, 60)
    if len(p) < 20:
        return Senal(False, "historia insuficiente")
    rsi = pd.to_numeric(_rsi(p["adj_close"]), errors="coerce")
    ultimo = rsi.iloc[-1]
    estuvo_abajo = (rsi.tail(15) < 30).any()
    if pd.isna(ultimo):
        return Senal(False, "RSI sin calcular")
    return Senal(bool(estuvo_abajo and ultimo > 30), f"RSI {ultimo:.0f}" + (" (venía de < 30)" if estuvo_abajo else ""))


def evaluar(con, ticker: str, moneda: str | None = None, hoy: date | None = None) -> dict[str, Senal]:
    hoy = hoy or date.today()
    resultado = {}
    if moneda:
        resultado["moneda_estable"] = moneda_estable(con, moneda, hoy)
    resultado["capitulacion"] = capitulacion(con, ticker, hoy)
    resultado["dejo_de_hacer_minimos"] = dejo_de_hacer_minimos(con, ticker, hoy)
    resultado["post_reporte"] = post_reporte(con, ticker, hoy)
    resultado["rsi_recupera"] = rsi_recupera(con, ticker, hoy)
    return resultado


def cantidad_activas(resultado: dict[str, Senal]) -> int:
    return sum(s.activa for s in resultado.values())
