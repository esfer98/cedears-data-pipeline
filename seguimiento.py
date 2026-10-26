"""
seguimiento.py
---------------
Maneja la lista de seguimiento: acciones que todavia no estan en la cartera
pero se quieren vigilar (por ejemplo, para entrar despues de un reporte).
monitoreo_cartera.py las incluye en el reporte diario y en la notificacion.

Uso:
  python seguimiento.py agregar UBER --motivo "entrar post-reporte 4/11" --soporte 65 --resistencia 80 --salida 62
  python seguimiento.py agregar BBD --motivo "post-eleccion Brasil" --moneda USDBRL
  python seguimiento.py quitar UBER
  python seguimiento.py listar

Volver a agregar un ticker que ya esta actualiza sus datos.
"""

import argparse
from datetime import date

import db


def main() -> None:
    parser = argparse.ArgumentParser(description="Lista de seguimiento")
    sub = parser.add_subparsers(dest="accion", required=True)
    ag = sub.add_parser("agregar")
    ag.add_argument("ticker")
    ag.add_argument("--motivo")
    ag.add_argument("--soporte", type=float)
    ag.add_argument("--resistencia", type=float)
    ag.add_argument("--salida", type=float)
    ag.add_argument("--moneda", help="serie de fact_macro_daily a vigilar (senal moneda_estable), p.ej. USDBRL")
    qu = sub.add_parser("quitar")
    qu.add_argument("ticker")
    sub.add_parser("listar")
    args = parser.parse_args()

    con = db.conectar()
    if args.accion == "agregar":
        ticker = args.ticker.upper()
        existe = con.execute("SELECT COUNT(*) FROM dim_empresa WHERE ticker_usd = ?", [ticker]).fetchone()[0]
        if not existe:
            raise SystemExit(f"{ticker} no esta en dim_empresa: no hay datos para seguirla.")
        con.execute("""
            INSERT INTO seguimiento (ticker_usd, desde, motivo, nivel_soporte, nivel_resistencia, nivel_salida, moneda, activo)
            VALUES (?, ?, ?, ?, ?, ?, ?, TRUE)
            ON CONFLICT (ticker_usd) DO UPDATE SET
                motivo = excluded.motivo, nivel_soporte = excluded.nivel_soporte,
                nivel_resistencia = excluded.nivel_resistencia, nivel_salida = excluded.nivel_salida,
                moneda = excluded.moneda, activo = TRUE
        """, [ticker, date.today(), args.motivo, args.soporte, args.resistencia, args.salida, args.moneda])
        print(f"{ticker} en seguimiento.")
    elif args.accion == "quitar":
        con.execute("UPDATE seguimiento SET activo = FALSE WHERE ticker_usd = ?", [args.ticker.upper()])
        print(f"{args.ticker.upper()} fuera de seguimiento.")
    print(con.execute("SELECT * FROM seguimiento WHERE activo ORDER BY desde").fetchdf().to_string(index=False))
    con.close()


if __name__ == "__main__":
    main()
