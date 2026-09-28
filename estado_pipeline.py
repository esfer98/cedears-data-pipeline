"""
estado_pipeline.py
-------------------
Tablero de salud: ultima corrida (ok o no) de cada script, para detectar de
un vistazo si algo dejo de correr silenciosamente -- exactamente lo que nos
paso con la rotacion del certificado de Norton, que rompio el pipeline sin
ningun aviso hasta que lo notamos revisando a mano.

Correr: python estado_pipeline.py
"""

import db


def main() -> None:
    con = db.conectar()
    estado = db.estado_pipeline(con)
    con.close()

    if estado.empty:
        print("Todavia no hay ninguna corrida registrada.")
        return

    print(estado.to_string(index=False))

    con_errores = db.conectar()
    ultimos_errores = con_errores.execute("""
        SELECT script, inicio, error_detalle
        FROM log_ejecuciones
        WHERE estado = 'error'
        ORDER BY inicio DESC
        LIMIT 5
    """).fetchdf()
    con_errores.close()

    if not ultimos_errores.empty:
        print("\nUltimos errores:")
        print(ultimos_errores.to_string(index=False))


if __name__ == "__main__":
    main()
