"""
dolar_argentina.py
-------------------
Cotizaciones del dólar en Argentina (oficial, blue, MEP, CCL, mayorista,
cripto, tarjeta) vía dolarapi.com (gratis, sin auth). Yahoo no tiene
CCL/MEP/blue -- son cotizaciones de mercado específicas de Argentina, no
están en ningún ticker estándar de Yahoo Finance.

Por qué importa: en notebooks/eda_argentina.ipynb estimamos un "CCL
implícito" vía arbitraje ADR/local (sin necesitar el ratio de conversión,
dividiendo retornos). Tener acá el CCL *publicado* de verdad permite
validar esa estimación contra el dato real, no solo aproximarlo.

A diferencia de macro_diario.py, la API de dolarapi.com NO da histórico --
solo el valor actual. Cada corrida agrega UNA fila por casa con la fecha de
hoy; el historial se arma hacia adelante, día a día (mismo patrón que
institutional_holding_pct en fact_metrics_daily: no se puede rellenar el
pasado, pero de acá en más queda guardado).

Se escriben en fact_macro_daily (mismo esquema serie/fecha/valor que las
series de macro_diario.py), con series propias prefijadas "AR_" para no
confundir con USDARS (que ya viene de Yahoo con otra metodología/fuente --
se dejan los dos, sirven para comparar entre sí).

Correr: python dolar_argentina.py
"""

from datetime import date

import pandas as pd
import requests
from dotenv import load_dotenv

import db

load_dotenv()

URL = "https://dolarapi.com/v1/dolares"

# "casa" de dolarapi.com -> codigo propio en fact_macro_daily
CASAS = {
    "oficial": "AR_OFICIAL",
    "blue": "AR_BLUE",
    "bolsa": "AR_MEP",
    "contadoconliqui": "AR_CCL",
    "mayorista": "AR_MAYORISTA",
    "cripto": "AR_CRIPTO",
    "tarjeta": "AR_TARJETA",
}


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "dolar_argentina")
    con_check.close()
    if ya_corrio:
        print("dolar_argentina ya corrio hoy con exito, no hace falta repetir.")
        return

    r = requests.get(URL, timeout=15)
    r.raise_for_status()
    datos = r.json()

    hoy = date.today()
    filas = []
    for d in datos:
        serie = CASAS.get(d.get("casa"))
        if serie is None or d.get("compra") is None or d.get("venta") is None:
            continue
        # promedio compra/venta -- mismo criterio que usa la mayoria de los
        # sitios que reportan "el dolar blue"/"el CCL" como un solo numero.
        valor = (d["compra"] + d["venta"]) / 2
        filas.append({"serie": serie, "fecha": hoy, "valor": valor})

    if not filas:
        print("dolarapi.com no devolvio ninguna casa reconocida.")
        return

    todo = pd.DataFrame(filas)

    con = db.conectar()
    with db.registrar(con, "dolar_argentina") as log:
        db.upsert_fact_macro_daily(con, todo)
        log["filas_afectadas"] = len(todo)
    con.close()

    print(f"fact_macro_daily actualizada ({len(todo)} cotizaciones, {hoy}):")
    for f in filas:
        print(f"  {f['serie']}: {f['valor']:.2f}")


if __name__ == "__main__":
    main()
