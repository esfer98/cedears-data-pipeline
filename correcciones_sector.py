"""
correcciones_sector.py
-----------------------
Aplica correcciones manuales a dim_empresa.sector_corregido, para los casos
donde la clasificacion GICS automatica de Yahoo no refleja el negocio real
de la empresa. Se detecto revisando STNE/PAGS en el EDA: Yahoo las clasifica
como "Technology" / "Software - Infrastructure", pero son procesadoras de
pago -- compiten con PYPL, MA, V, COIN, que Yahoo SI clasifica bien como
"Financial Services". Buscando el mismo patron en el resto del universo
aparecio tambien XYZ (Block Inc, ex-Square).

Por que importa: gold_comparables agrupa por sector para calcular medianas
y pares similares. Con el sector crudo de Yahoo, STNE/PAGS/XYZ terminaban
comparadas contra software de alto multiplo (mediana de P/E ~32) en vez de
contra sus competidores reales -- inflando artificialmente el "descuento"
que mostraban (-80/-89% vs. mediana de sector). gold_lynch.lynch_categoria_auto
tambien mira el sector (la regla de "Ciclica" chequea Energy/Basic
Materials/Industrials), asi que una mala clasificacion tambien puede
empujar la categoria automatica para el lado equivocado.

sector_corregido nunca se pisa con un upsert automatico -- mismo criterio
que dim_empresa.lynch_category/modelo_negocio (ver db.py): se carga a mano
una vez y se queda, aunque analisis_fundamental_liviano.py siga refrescando
el "sector" crudo de Yahoo en su propia columna. gold/lynch.py y
gold/comparables.py usan COALESCE(sector_corregido, sector), asi que la
correccion se propaga a toda la capa Gold sin perder el dato original.

Las correcciones van en la lista CORRECCIONES de aca abajo (no en un CSV:
data/ y *.csv estan en .gitignore, y esto tiene que quedar versionado).
Agregar una nueva es agregar un dict a la lista.

Correr: python correcciones_sector.py
"""

import db

CORRECCIONES = [
    {
        "ticker_usd": "STNE",
        "sector_corregido": "Financial Services",
        "motivo": "Procesadora de pagos/fintech brasileña. Yahoo la clasifica como "
                  "Technology / Software - Infrastructure, pero compite con PYPL/MA/V/COIN, "
                  "que SI estan bien clasificadas como Financial Services.",
    },
    {
        "ticker_usd": "PAGS",
        "sector_corregido": "Financial Services",
        "motivo": "Mismo caso que STNE: procesadora de pagos brasileña mal clasificada como Technology.",
    },
    {
        "ticker_usd": "XYZ",
        "sector_corregido": "Financial Services",
        "motivo": "Block Inc (ex-Square). Mismo caso: procesadora de pagos clasificada como "
                  "Technology / Software - Infrastructure en vez de Financial Services.",
    },
]


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "correcciones_sector") as log:
        db.aplicar_correcciones_sector(con, CORRECCIONES)
        log["filas_afectadas"] = len(CORRECCIONES)
    con.close()

    print(f"sector_corregido aplicado a {len(CORRECCIONES)} empresas:")
    for c in CORRECCIONES:
        print(f"  {c['ticker_usd']}: {c['sector_corregido']}")
        print(f"    motivo: {c['motivo']}")


if __name__ == "__main__":
    main()
