"""
sentimiento_noticias.py
-------------------------
Sentimiento de titulares de noticias por empresa, vía FinBERT (modelo
`ProsusAI/finbert`, especializado en texto financiero -- no un diccionario
genérico tipo Loughran-McDonald, entiende contexto).

Fuente de titulares: RSS de Google News (`news.google.com/rss/search`),
gratis y sin autenticación. NO usamos `yf.Ticker(...).news` -- ese endpoint
de yfinance 1.7.0 está roto (Yahoo cambió el formato de respuesta y la
librería no lo parsea, devuelve lista vacía hasta para AAPL/NVDA/TSLA,
verificado en vivo).

Por qué FinBERT y no un LLM: mismo criterio que el resto del proyecto
(`gold/lynch.py`, etc.) -- un modelo clasificador corriendo en loop sobre
cientos de titulares es la herramienta correcta, no un LLM por request.

Costo real: bajar los pesos de FinBERT (~400MB) y las dependencias
(torch + transformers, ~1-2GB) es un costo ÚNICO la primera vez. Despues,
la inferencia corre local, sin llamadas a ninguna API por titular.

Por qué cachea por titular, no por día: la parte cara es la inferencia de
FinBERT, no el fetch del RSS. `db.titulares_ya_procesados()` trae los
titulares que esa empresa YA tiene sentimiento calculado antes de pedirle
nada a FinBERT -- un titular repetido/resindicado no se reprocesa. Por eso
la primera corrida es lenta (todo es nuevo) y las siguientes son rápidas
(la mayoría de los titulares del día ya estaban, solo se procesan los
realmente nuevos).

Correr: python sentimiento_noticias.py
"""

import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

import pandas as pd
import requests
import torch
from dotenv import load_dotenv
from transformers import AutoModelForSequenceClassification, AutoTokenizer

import db

load_dotenv()

RSS_URL = "https://news.google.com/rss/search"
DIAS_VENTANA = 7  # solo noticias de los ultimos N dias -- RSS trae mezcla de fechas
PAUSA_ENTRE_TICKERS = 0.8
MAX_TITULARES_POR_TICKER = 15  # limite practico, Google News a veces trae decenas

_MODELO = None
_TOKENIZER = None


def _cargar_modelo():
    global _MODELO, _TOKENIZER
    if _MODELO is None:
        print("Cargando FinBERT (primera vez puede tardar, descarga ~400MB)...")
        _TOKENIZER = AutoTokenizer.from_pretrained("ProsusAI/finbert")
        _MODELO = AutoModelForSequenceClassification.from_pretrained("ProsusAI/finbert")
        _MODELO.eval()
    return _MODELO, _TOKENIZER


def obtener_titulares(nombre_empresa: str) -> list[dict]:
    """Titulares recientes de Google News RSS para una empresa."""
    params = {"q": f"{nombre_empresa} stock", "hl": "en-US", "gl": "US", "ceid": "US:en"}
    try:
        r = requests.get(RSS_URL, params=params, timeout=15)
        r.raise_for_status()
        root = ET.fromstring(r.content)
    except Exception as e:
        print(f"\n{nombre_empresa}: error RSS: {e}")
        return []

    limite = datetime.now(timezone.utc) - timedelta(days=DIAS_VENTANA)
    titulares = []
    for item in root.findall(".//item")[:MAX_TITULARES_POR_TICKER * 2]:  # de sobra, filtramos despues
        titulo = item.findtext("title")
        link = item.findtext("link")
        pub_date_raw = item.findtext("pubDate")
        fuente_el = item.find("source")
        fuente = fuente_el.text if fuente_el is not None else None
        if not titulo or not pub_date_raw:
            continue
        try:
            fecha_pub = parsedate_to_datetime(pub_date_raw)
        except Exception:
            continue
        if fecha_pub < limite:
            continue
        titulares.append({"titulo": titulo, "link": link, "fuente": fuente, "fecha_publicacion": fecha_pub})
        if len(titulares) >= MAX_TITULARES_POR_TICKER:
            break
    return titulares


def clasificar_sentimiento(textos: list[str]) -> list[dict]:
    """FinBERT sobre una lista de titulares, en un solo batch (mas rapido
    que uno por uno). Devuelve label + score de confianza del label elegido."""
    if not textos:
        return []
    modelo, tokenizer = _cargar_modelo()
    inputs = tokenizer(textos, return_tensors="pt", padding=True, truncation=True, max_length=64)
    with torch.no_grad():
        logits = modelo(**inputs).logits
    probs = torch.nn.functional.softmax(logits, dim=-1)
    labels = modelo.config.id2label
    resultados = []
    for p in probs:
        idx = p.argmax().item()
        resultados.append({"sentimiento": labels[idx], "score": round(p[idx].item(), 3)})
    return resultados


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "sentimiento_noticias")
    con_check.close()
    if ya_corrio:
        print("sentimiento_noticias ya corrio hoy con exito, no hace falta repetir.")
        return

    universo = pd.read_csv("data/cedears_normalizados.csv")
    universo = universo[universo["mercado"] != "cripto"].reset_index(drop=True)

    con = db.conectar()
    hoy = date.today()
    total_nuevos = 0
    total_omitidos = 0

    with db.registrar(con, "sentimiento_noticias") as log:
        for i, fila in enumerate(universo.itertuples(), 1):
            print(f"[{i}/{len(universo)}] {fila.ticker_usd}", end="\r")
            titulares = obtener_titulares(fila.nombre_empresa)
            ya_vistos = db.titulares_ya_procesados(con, fila.ticker_usd)

            nuevos = [t for t in titulares if t["titulo"] not in ya_vistos]
            total_omitidos += len(titulares) - len(nuevos)
            if not nuevos:
                time.sleep(PAUSA_ENTRE_TICKERS)
                continue

            sentimientos = clasificar_sentimiento([t["titulo"] for t in nuevos])
            filas_db = [
                {
                    "ticker_usd": fila.ticker_usd,
                    "titular": t["titulo"],
                    "fuente": t["fuente"],
                    "fecha_publicacion": t["fecha_publicacion"].replace(tzinfo=None),
                    "link": t["link"],
                    "sentimiento": s["sentimiento"],
                    "score_sentimiento": s["score"],
                    "fecha_procesado": hoy,
                }
                for t, s in zip(nuevos, sentimientos)
            ]
            db.upsert_fact_noticias(con, filas_db)
            total_nuevos += len(filas_db)
            time.sleep(PAUSA_ENTRE_TICKERS)

        print()
        log["filas_afectadas"] = total_nuevos

    con.close()
    print(f"fact_noticias: {total_nuevos} titulares nuevos procesados, {total_omitidos} ya existian (sin reprocesar)")


if __name__ == "__main__":
    main()
