"""
gold/sentimiento.py
---------------------
Capa Gold: agrega los titulares con sentimiento de fact_noticias
(sentimiento_noticias.py, FinBERT) a un score por empresa, sobre la
ventana de los últimos 7 días.

Todas las columnas van prefijadas "sent_", misma convención que el resto
de gold/.

Que calcula:
  1. sent_titulares_total / sent_positivas / sent_negativas / sent_neutrales:
     conteo de titulares por sentimiento en la ventana.
  2. sent_score_promedio: promedio de score_sentimiento con signo (+ para
     positive, - para negative, 0 para neutral) -- un solo número entre
     -1 y 1 que resume el tono de la cobertura reciente.
  3. sent_clasificacion: etiqueta legible sobre sent_score_promedio.

Una empresa sin fila en esta vista es una empresa sin noticias recientes
en la ventana (o sin cobertura de medios en inglés vía Google News) -- no
es un error, simplemente no hay nada que agregar.

Correr: python gold/sentimiento.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db

DIAS_VENTANA = 7

SQL_VISTA = f"""
CREATE OR REPLACE VIEW gold_sentimiento AS
WITH recientes AS (
    SELECT *
    FROM fact_noticias
    WHERE fecha_publicacion >= CURRENT_DATE - INTERVAL {DIAS_VENTANA} DAY
),
agregado AS (
    SELECT
        ticker_usd,
        COUNT(*) AS total_titulares,
        SUM((sentimiento = 'positive')::INT) AS positivas,
        SUM((sentimiento = 'negative')::INT) AS negativas,
        SUM((sentimiento = 'neutral')::INT) AS neutrales,
        AVG(CASE sentimiento
                WHEN 'positive' THEN score_sentimiento
                WHEN 'negative' THEN -score_sentimiento
                ELSE 0
            END) AS score_promedio
    FROM recientes
    GROUP BY ticker_usd
)
SELECT
    ticker_usd,
    total_titulares AS sent_titulares_total,
    positivas AS sent_positivas,
    negativas AS sent_negativas,
    neutrales AS sent_neutrales,
    ROUND(score_promedio, 3) AS sent_score_promedio,
    CASE
        WHEN score_promedio >= 0.15 THEN 'Positivo'
        WHEN score_promedio <= -0.15 THEN 'Negativo'
        ELSE 'Neutral/Mixto'
    END AS sent_clasificacion
FROM agregado
"""


def main() -> None:
    con = db.conectar()
    with db.registrar(con, "gold_sentimiento") as log:
        con.execute(SQL_VISTA)
        log["filas_afectadas"] = con.execute("SELECT COUNT(*) FROM gold_sentimiento").fetchone()[0]

    print(f"=== Distribución de sentimiento (ventana {DIAS_VENTANA} días) ===")
    print(con.execute("""
        SELECT sent_clasificacion, COUNT(*) AS empresas
        FROM gold_sentimiento GROUP BY 1 ORDER BY 2 DESC
    """).fetchdf().to_string(index=False))

    print("\n=== Top 10 cobertura más POSITIVA ===")
    print(con.execute("""
        SELECT s.ticker_usd, d.nombre_empresa, s.sent_score_promedio, s.sent_titulares_total
        FROM gold_sentimiento s JOIN dim_empresa d USING (ticker_usd)
        ORDER BY s.sent_score_promedio DESC LIMIT 10
    """).fetchdf().to_string(index=False))

    print("\n=== Top 10 cobertura más NEGATIVA ===")
    print(con.execute("""
        SELECT s.ticker_usd, d.nombre_empresa, s.sent_score_promedio, s.sent_titulares_total
        FROM gold_sentimiento s JOIN dim_empresa d USING (ticker_usd)
        ORDER BY s.sent_score_promedio ASC LIMIT 10
    """).fetchdf().to_string(index=False))

    con.close()


if __name__ == "__main__":
    main()
