"""
gold/ondas.py
--------------
Capa Gold: swings de precio (ZigZag), niveles de Fibonacci y conteo
CANDIDATO de ondas de Elliott. Columnas prefijadas "onda_".

A diferencia del resto de gold/, esto NO es una vista SQL: un ZigZag depende
del recorrido (cada pivot depende de cual fue el anterior), y eso no se
expresa con funciones de ventana. Se calcula en Python y se MATERIALIZA en
dos tablas que se reescriben enteras en cada corrida (es barato: ~0.5M
filas de precio, segundos):

  gold_pivots  -- una fila por pivot (ticker, fecha, precio, tipo H/L),
                  con la fecha en que el pivot quedo CONFIRMADO y la senal
                  de Elliott que se veia en ese momento. Sirve para graficar
                  y para testear si la senal predijo algo (ver seccion 14 de
                  notebooks/eda_cedears.ipynb).
  gold_ondas   -- una fila por ticker: estado actual.

Como se calcula:

  1. ZIGZAG. Un maximo pasa a ser pivot "H" cuando el precio cae un X%
     desde ahi; un minimo pasa a ser pivot "L" cuando sube un X%. Ese X
     (onda_umbral_pct) es ARBITRARIO -- no hay un valor correcto, y cambiarlo
     cambia el conteo de ondas. Se usa 5 x el desvio diario de retornos de
     cada ticker, acotado entre 8% y 30%, para que una accion volatil no
     tenga un pivot por semana y una defensiva no se quede sin ninguno.
     Un pivot se conoce con atraso: recien en fecha_confirmacion, no en su
     propia fecha. Todo lo que mire "que paso despues" tiene que partir de
     fecha_confirmacion, si no hay look-ahead.

  2. FIBONACCI sobre el ultimo swing completo (los dos ultimos pivots
     confirmados): cuanto de ese swing ya retrocedio el precio actual
     (onda_fib_retroceso_pct), los precios de los niveles 38.2 / 50 / 61.8,
     y la extension 161.8 (objetivo clasico si la tendencia del swing se
     retoma).

  3. ELLIOTT CANDIDATO. Sobre los ultimos pivots se chequean las tres reglas
     duras de un impulso de 5 ondas (largos medidos en log-precio):
       - la onda 2 no retrocede mas del 100% de la onda 1
       - la onda 3 no es la mas corta entre 1, 3 y 5
       - la onda 4 no entra en el territorio de la onda 1
     (mas que la 3 supere el fin de la 1 y la 5 el fin de la 3).
     Si los ultimos 6 pivots las cumplen: "Impulso ... completo" (la teoria
     espera una correccion A-B-C). Si los ultimos 5 cumplen las de las ondas
     1 a 4: "Ondas 1-4 ..., 5 en curso".

     Esto es UN conteo posible a UNA escala (la del umbral), no "el" conteo:
     Elliott es fractal y dos analistas cuentan distinto el mismo grafico.
     Tomarlo como una etiqueta descriptiva mas, no como prediccion.

Correr: python gold/ondas.py
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

FACTOR_UMBRAL = 5
UMBRAL_MIN_PCT = 8.0
UMBRAL_MAX_PCT = 30.0
MIN_DIAS = 250
NIVELES_FIB = [23.6, 38.2, 50.0, 61.8, 78.6, 100.0]
TOLERANCIA_FIB = 3.0  # puntos porcentuales de retroceso para decir "esta en el nivel"


def umbral_para(precios: np.ndarray) -> float:
    retornos = np.diff(np.log(precios))
    return float(np.clip(FACTOR_UMBRAL * np.std(retornos) * 100, UMBRAL_MIN_PCT, UMBRAL_MAX_PCT))


def zigzag(precios: np.ndarray, umbral_pct: float) -> list[tuple[int, str, int]]:
    """Devuelve [(indice_pivot, 'H'|'L', indice_confirmacion)], alternando H y L."""
    u = umbral_pct / 100
    pivots: list[tuple[int, str, int]] = []
    i_max = i_min = 0
    direccion = 0  # 0 = todavia sin primer pivot, 1 = pierna de suba, -1 = pierna de baja
    for i in range(1, len(precios)):
        p = precios[i]
        if direccion >= 0 and p > precios[i_max]:
            i_max = i
        if direccion <= 0 and p < precios[i_min]:
            i_min = i
        if direccion >= 0 and p <= precios[i_max] * (1 - u):
            pivots.append((i_max, "H", i))
            direccion, i_min = -1, i
        elif direccion <= 0 and p >= precios[i_min] * (1 + u):
            pivots.append((i_min, "L", i))
            direccion, i_max = 1, i
    return pivots


def _impulso(q: list[float], completo: bool) -> bool:
    """q = log-precios de pivots de un impulso ALCISTA: L,H,L,H,L[,H]."""
    w1, w3 = q[1] - q[0], q[3] - q[2]
    if not (q[2] > q[0] and q[3] > q[1] and q[4] > q[1]):
        return False
    if not completo:
        return True
    w5 = q[5] - q[4]
    return q[5] > q[3] and not (w3 < w1 and w3 < w5)


def senal_elliott(log_precios: list[float], tipos: list[str]) -> str | None:
    """Senal visible con los pivots confirmados hasta el ultimo de la lista."""
    ultimo = tipos[-1]
    if len(log_precios) >= 6:
        q = log_precios[-6:]
        if ultimo == "H" and _impulso(q, completo=True):
            return "Impulso alcista completo"
        if ultimo == "L" and _impulso([-x for x in q], completo=True):
            return "Impulso bajista completo"
    if len(log_precios) >= 5:
        q = log_precios[-5:]
        if ultimo == "L" and _impulso(q, completo=False):
            return "Ondas 1-4 alcistas, 5 en curso"
        if ultimo == "H" and _impulso([-x for x in q], completo=False):
            return "Ondas 1-4 bajistas, 5 en curso"
    return None


def analizar_ticker(ticker: str, fechas: np.ndarray, precios: np.ndarray) -> tuple[list[dict], dict | None]:
    umbral = umbral_para(precios)
    pivots = zigzag(precios, umbral)
    filas = []
    logs: list[float] = []
    tipos: list[str] = []
    for n, (i, tipo, i_conf) in enumerate(pivots, start=1):
        logs.append(float(np.log(precios[i])))
        tipos.append(tipo)
        filas.append({
            "ticker_usd": ticker, "nro_pivot": n, "fecha": fechas[i], "precio": float(precios[i]),
            "tipo": tipo, "fecha_confirmacion": fechas[i_conf], "elliott_senal": senal_elliott(logs, tipos),
        })
    if len(filas) < 2:
        return filas, None

    previo, ultimo = filas[-2], filas[-1]
    actual = float(precios[-1])
    swing = ultimo["precio"] - previo["precio"]  # >0 si el ultimo swing completo fue de suba
    retroceso = (ultimo["precio"] - actual) / swing * 100
    cercano = min(NIVELES_FIB, key=lambda n: abs(n - retroceso))
    resumen = {
        "ticker_usd": ticker,
        "fecha": fechas[-1],
        "onda_umbral_pct": round(umbral, 1),
        "onda_pivots_total": len(filas),
        "onda_ultimo_pivot_tipo": "Máximo" if ultimo["tipo"] == "H" else "Mínimo",
        "onda_ultimo_pivot_fecha": ultimo["fecha"],
        "onda_ultimo_pivot_precio": round(ultimo["precio"], 2),
        "onda_pierna_actual": "Baja" if ultimo["tipo"] == "H" else "Suba",
        "onda_pierna_actual_pct": round((actual / ultimo["precio"] - 1) * 100, 1),
        "onda_swing_previo_pct": round((ultimo["precio"] / previo["precio"] - 1) * 100, 1),
        "onda_fib_retroceso_pct": round(retroceso, 1),
        "onda_fib_nivel_cercano": cercano if abs(cercano - retroceso) <= TOLERANCIA_FIB else None,
        "onda_fib_382": round(ultimo["precio"] - 0.382 * swing, 2),
        "onda_fib_500": round(ultimo["precio"] - 0.500 * swing, 2),
        "onda_fib_618": round(ultimo["precio"] - 0.618 * swing, 2),
        "onda_fib_ext_1618": round(previo["precio"] + 1.618 * swing, 2),
        "onda_elliott_candidato": ultimo["elliott_senal"] or "Sin patrón",
    }
    return filas, resumen


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_ondas") as log:
        precios = con.execute("""
            SELECT ticker_usd, fecha, adj_close
            FROM fact_precios_daily
            WHERE adj_close IS NOT NULL AND adj_close > 0
            ORDER BY ticker_usd, fecha
        """).fetchdf()

        pivots, resumenes = [], []
        for ticker, grupo in precios.groupby("ticker_usd", sort=False):
            if len(grupo) < MIN_DIAS:
                continue
            filas, resumen = analizar_ticker(ticker, grupo["fecha"].to_numpy(), grupo["adj_close"].to_numpy())
            pivots.extend(filas)
            if resumen:
                resumenes.append(resumen)

        df_pivots = pd.DataFrame(pivots)
        df_ondas = pd.DataFrame(resumenes)
        con.register("pivots_temp", df_pivots)
        con.register("ondas_temp", df_ondas)
        con.execute("""
            CREATE OR REPLACE TABLE gold_pivots AS
            SELECT ticker_usd, nro_pivot, CAST(fecha AS DATE) AS fecha, precio, tipo,
                   CAST(fecha_confirmacion AS DATE) AS fecha_confirmacion, elliott_senal
            FROM pivots_temp
        """)
        con.execute("""
            CREATE OR REPLACE TABLE gold_ondas AS
            SELECT * REPLACE (CAST(fecha AS DATE) AS fecha,
                              CAST(onda_ultimo_pivot_fecha AS DATE) AS onda_ultimo_pivot_fecha)
            FROM ondas_temp
        """)
        con.unregister("pivots_temp")
        con.unregister("ondas_temp")
        log["filas_afectadas"] = len(df_ondas)

    print(f"Tickers: {len(df_ondas)} | pivots: {len(df_pivots)}")
    print("\n=== Conteo candidato de Elliott ===")
    print(con.execute("""
        SELECT onda_elliott_candidato, COUNT(*) AS empresas
        FROM gold_ondas GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf().to_string(index=False))

    print("\n=== Precio sobre un nivel de Fibonacci del ultimo swing ===")
    print(con.execute("""
        SELECT onda_fib_nivel_cercano, onda_pierna_actual, COUNT(*) AS empresas
        FROM gold_ondas WHERE onda_fib_nivel_cercano IS NOT NULL
        GROUP BY 1, 2 ORDER BY 1, 2
    """).fetchdf().to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
