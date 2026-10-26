"""
dolar_argentina.py
-------------------
Cotizaciones del dólar en Argentina (oficial, blue, MEP, CCL, mayorista,
cripto, tarjeta). Yahoo no tiene CCL/MEP/blue: son cotizaciones de mercado
específicas de Argentina.

Fuente: api.argentinadatos.com (gratis, sin auth), que trae la SERIE
HISTÓRICA completa de cada casa (CCL desde 2013, oficial y blue desde 2011).
Hasta el 26/10/2026 se usaba dolarapi.com, que solo da el valor del día: el
historial empezaba el 6/10/2026 y un día sin corrida (8/10) se perdía para
siempre. Con la serie completa, cada corrida reescribe todo el historial
(son ~25.000 filas en total, un upsert set-based) y un día perdido se
recupera solo en la corrida siguiente.

Para qué: comparar CCL contra oficial (brecha), pasar precios locales a
dólares con el dólar que de verdad usa el mercado, y medir elecciones o
crisis pasadas en CCL en vez de en oficial (en 2023 había cepo y el oficial
no reflejaba nada).

Se escriben en fact_macro_daily con series propias "AR_*" (USDARS de Yahoo
se mantiene aparte: es el oficial con otra fuente, sirven para cruzarlas).

Correr: python dolar_argentina.py
"""

from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv

import db

load_dotenv()

URL = "https://api.argentinadatos.com/v1/cotizaciones/dolares/{casa}"

# "casa" de la API -> codigo propio en fact_macro_daily
CASAS = {
    "oficial": "AR_OFICIAL",
    "blue": "AR_BLUE",
    "bolsa": "AR_MEP",
    "contadoconliqui": "AR_CCL",
    "mayorista": "AR_MAYORISTA",
    "cripto": "AR_CRIPTO",
    "tarjeta": "AR_TARJETA",
}


def a_filas(datos: list[dict], serie: str) -> list[dict]:
    """Respuesta de la API -> filas de fact_macro_daily. Valor = promedio
    compra/venta, el criterio con el que se suele reportar "el CCL" como un
    solo numero."""
    filas = []
    for d in datos:
        if d.get("compra") is None or d.get("venta") is None:
            continue
        filas.append({
            "serie": serie,
            "fecha": date.fromisoformat(d["fecha"]),
            "valor": round((d["compra"] + d["venta"]) / 2, 4),
        })
    return filas


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "dolar_argentina")
    con_check.close()
    if ya_corrio:
        print("dolar_argentina ya corrio hoy con exito, no hace falta repetir.")
        return

    filas = []
    for casa, serie in CASAS.items():
        r = requests.get(URL.format(casa=casa), timeout=30)
        r.raise_for_status()
        filas.extend(a_filas(r.json(), serie))

    if not filas:
        print("argentinadatos.com no devolvio datos.")
        return

    todo = pd.DataFrame(filas)
    con = db.conectar()
    with db.registrar(con, "dolar_argentina") as log:
        db.upsert_fact_macro_daily(con, todo)
        log["filas_afectadas"] = len(todo)
    con.close()

    ultimas = todo.sort_values("fecha").groupby("serie").tail(1)
    print(f"fact_macro_daily actualizada ({len(todo)} filas, {todo['fecha'].min()} a {todo['fecha'].max()}):")
    for f in ultimas.itertuples():
        print(f"  {f.serie}: {f.valor:.2f} ({f.fecha})")


if __name__ == "__main__":
    main()
