"""
listado_cedears.py
-------------------
Universo completo de activos a trackear, una fila por empresa/activo (sin
repetir las variantes de liquidacion: pesos / "C" / "D" son el mismo papel).
Tres fuentes, tres "mercado" distintos en dim_empresa:

  1. CEDEARs (usa_otros / brasil) -- empresas extranjeras operables via IOL.
  2. Acciones argentinas locales (argentina_local) -- panel Merval y afines
     de IOL, no son CEDEARs, son las empresas argentinas en si.
  3. Cripto (cripto) -- BTC/ETH/SOL, fijos, sin pasar por IOL.

Sirve para corroborar rapido si existe CEDEAR/accion local de algo antes de
analizarla con datos de la bolsa de EE.UU.

Correr:  python listado_cedears.py
"""

import re
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv

import db
from iol_client import IOLClient

load_dotenv()

# Los ETF-CEDEAR (SPDR, iShares, Invesco, etc.) no son empresas: no tienen
# estados de resultados que analizar, asi que se descartan del listado.
PATRON_ETF = re.compile(
    r"(?i)\betf\b|select sector|spdr|ishares|invesco|proshares|direxion"
    r"|van eck|vanguard|global x|\bipath\b|\btrust\b|\bfund\b"
)

# Empresas que ya quedan cubiertas por otra fila del listado con un ticker
# base conocido (misma empresa, pero IOL escribe la descripcion tan distinto
# entre variantes que la deduplicacion automatica no las pudo unir). Se
# descarta cualquier ticker "BASE + una letra" (BMYC, BMYD, ...) porque cual
# de las variantes queda como "mas corta" puede cambiar de una corrida a otra
# segun como IOL ordene el panel en vivo -- comparar el string completo
# (p.ej. "BMYC") es fragil, comparar el stem es estable.
STEMS_CON_DUPLICADO = {
    "AZN",  # Astrazeneca: la variante trae un typo ("Aztrazeneca") y no se agrupa
    "BMY",  # Bristol-Myers Squibb
    "SHEL",  # Shell Plc
    "MDT",  # Medtronic (variante "Esc")
    "IBM",
    "IFF",  # International Flavors & Fragrances
    "UGP",  # Ultrapar
}
# Casos puntuales que no siguen el patron "stem + una letra":
DUPLICADOS_LITERALES_A_DESCARTAR = {
    "GOGLC",  # = GOOGL (nombre viejo "Google Inc", IOL nunca lo renombro)
    "BKC*",   # = BNY (Bank Of New York Mellon, variante "Cta.Ext")
    "VAL3C",  # = VALE (mismo Vale, pero via el ADR de EE.UU. ya alcanza)
}


def es_duplicado(ticker: str) -> bool:
    if ticker in DUPLICADOS_LITERALES_A_DESCARTAR:
        return True
    stem = ticker[:-1]
    return stem in STEMS_CON_DUPLICADO and ticker != stem

# Correcciones de ticker para poder pedirle el papel a Yahoo Finance.
# Confirmadas a mano contra yfinance (nombre real de la empresa) antes de
# usarlas: IOL arma el simbolo distinto al que usa Yahoo (typos, sufijos de
# liquidacion pegados al ticker, o listados en otra bolsa).
TICKERS_YAHOO = {
    # BDRs brasileños: Yahoo los pide con sufijo ".SA" (Bolsa de San Pablo)
    "BBAS3": "BBAS3.SA", "ITUB3": "ITUB3.SA", "SBSP3": "SBSP3.SA",
    "HAPV3": "HAPV3.SA", "RENT3": "RENT3.SA", "LREN3": "LREN3.SA",
    "MGLU3": "MGLU3.SA", "PRIO3": "PRIO3.SA", "SUZB3": "SUZB3.SA",
    "TIMS3": "TIMS3.SA", "WEGE3": "WEGE3.SA",
    "BPA11": "BPAC11.SA",  # IOL trunca "BPAC11" (Banco BTG Pactual) a "BPA11"
    # Simbolo de IOL no coincide con el de Yahoo (typo o formato viejo)
    "BRKB": "BRK-B", "BA.C": "BAC", "BNG": "BG", "KOFM": "KOF",
    "NOKA": "NOK", "PKS": "PKX", "TXR": "TX", "TRVV": "TRV",
    "DISN": "DIS", "WBO": "WB", "XROX": "XRX", "BBV": "BBVA",
    "ADGO": "AGRO",
    # Listados en otras bolsas: Yahoo pide el sufijo de esa plaza
    "ADS": "ADS.DE",     # Adidas, Frankfurt
    "AKO.B": "AKO-B",    # Embotelladora Andina, clase B
    "SMSN": "SMSN.IL",   # Samsung Electronics, GDR de Londres
}


def normalizar_empresa(descripcion: str) -> str:
    """Nombre de empresa limpio, sin el prefijo 'Cedear' ni puntuacion suelta."""
    nombre = re.sub(r"(?i)^cedear\s+", "", descripcion).strip()
    nombre = re.sub(r"[.,]+$", "", nombre).strip()
    return nombre


def clasificar_mercado(ticker: str) -> str:
    """BDRs brasileños terminan en digitos (clase de accion en B3), p.ej.
    RENT3, MGLU3, BPA11. El resto son ADRs/acciones de EE.UU. u otras bolsas."""
    return "brasil" if re.search(r"\d[A-Za-z]?$", ticker) else "usa_otros"


def clave_agrupacion(nombre: str) -> str:
    """Clave para detectar que dos descripciones son la misma empresa
    (ignora mayusculas/puntuacion: 'Ambev S.A.' y 'Ambev S.A' agrupan igual)."""
    return re.sub(r"[^a-z0-9]", "", nombre.lower())


def unir_claves_truncadas(claves: list[str]) -> dict[str, str]:
    """IOL trunca la descripcion a un largo variable segun el simbolo, asi que
    la misma empresa puede aparecer como 'Adobe Systems Incorpor' en un
    simbolo y 'Adobe Systems Incorporated' en otro. Union-Find: si una clave
    es prefijo de otra (>=6 caracteres para evitar falsos positivos con
    nombres cortos), se unen en el mismo grupo."""
    unicas = sorted(set(claves), key=len)
    padre = {c: c for c in unicas}

    def encontrar(c: str) -> str:
        while padre[c] != c:
            padre[c] = padre[padre[c]]
            c = padre[c]
        return c

    for i, corta in enumerate(unicas):
        if len(corta) < 6:
            continue
        for larga in unicas[i + 1:]:
            if larga.startswith(corta):
                padre[encontrar(corta)] = encontrar(larga)

    return {c: encontrar(c) for c in unicas}


def _deduplicar_por_empresa(titulos: list[dict]) -> pd.DataFrame:
    """A partir de titulos de IOL (con 'simbolo' y 'descripcion'), devuelve una
    fila por empresa: el simbolo mas corto del grupo como representante, el
    nombre mas largo/completo, y todas las variantes. Compartido entre
    CEDEARs y acciones argentinas -- misma logica de deduplicacion (la
    liquidacion en pesos/"C"/"D" es el mismo papel).

    Se usa la descripcion (no el simbolo) para agrupar, porque IOL arma los
    simbolos de forma inconsistente: p.ej. BA/BAC/BAD son las 3 variantes de
    Boeing, mientras que BA.C/BA.CC/BA.CD son las de Bank of America.

    Nota: quedan afuera de esta deduplicacion un puñado de casos donde IOL
    directamente escribe la descripcion distinto entre variantes (typos como
    "Aztrazeneca" vs "Astrazeneca", o los ETFs sectoriales SPDR que alternan
    entre "State Street X Select Sector Spdr" y "The X Select Sector Spdr").
    Esos son errores de tipeo en el dato fuente, no algo que se pueda arreglar
    de forma generica sin una tabla de alias a mano.
    """
    df = pd.DataFrame(titulos)
    df["empresa"] = df["descripcion"].apply(normalizar_empresa)
    df["clave"] = df["empresa"].apply(clave_agrupacion)

    mapa_grupo = unir_claves_truncadas(df["clave"].tolist())
    df["grupo"] = df["clave"].map(mapa_grupo)

    filas = []
    for _, grupo in df.groupby("grupo"):
        nombre = grupo.loc[grupo["empresa"].str.len().idxmax(), "empresa"]
        principal = grupo.assign(_len=grupo["simbolo"].str.len()).sort_values("_len").iloc[0]
        filas.append({
            "ticker": principal["simbolo"],
            "empresa": nombre,
            "variantes": ", ".join(sorted(grupo["simbolo"])),
        })
    return pd.DataFrame(filas)


def listar_empresas(iol: IOLClient) -> pd.DataFrame:
    """Panel de CEDEARs, una fila por empresa."""
    panel = iol.cedears_panel()
    titulos = [t for t in panel["titulos"] if not PATRON_ETF.search(t.get("descripcion") or "")]
    dedup = _deduplicar_por_empresa(titulos)
    dedup = dedup[~dedup["ticker"].apply(es_duplicado)]

    filas = []
    for _, row in dedup.iterrows():
        ticker = row["ticker"]
        filas.append({
            "ticker_usd": ticker,
            "ticker_cedear": ticker,  # en BYMA usan la misma base
            "ticker_yahoo": TICKERS_YAHOO.get(ticker, ticker),
            "ticker_adr_usa": None,
            "mercado": clasificar_mercado(ticker),
            "nombre_empresa": row["empresa"].title(),
            "variantes": row["variantes"],
        })

    return pd.DataFrame(filas).sort_values("nombre_empresa").reset_index(drop=True)


PANELES_ARGENTINA = ["Merval", "Panel General", "Merval 25", "Merval Argentina", "Burcap"]

# De las acciones argentinas locales, estas 13 ademas cotizan como ADR
# directo en EE.UU. (mas liquido, en dolares) -- confirmado a mano contra
# yfinance antes de usarlas. No reemplaza a ticker_yahoo (que sigue siendo
# el ".BA", el papel que realmente se opera en IOL): es un dato extra para
# comparar precio local vs. ADR (la brecha cambiaria implicita, CCL).
ADR_ARGENTINA = {
    "GGAL": "GGAL", "BMA": "BMA", "YPFD": "YPF", "PAMP": "PAM", "CRES": "CRESY",
    "SUPV": "SUPV", "LOMA": "LOMA", "EDN": "EDN", "TGSU2": "TGS", "TECO2": "TEO",
    "BBAR": "BBAR", "IRSA": "IRS", "CEPU": "CEPU",
}


def listar_acciones_argentinas(iol: IOLClient) -> pd.DataFrame:
    """Acciones locales de BYMA (Merval y paneles relacionados) -- no son
    CEDEARs, son las empresas argentinas en si. Yahoo las pide con sufijo
    ".BA" (confirmado: las 66 resuelven con ese sufijo, sin excepciones)."""
    titulos = []
    for panel in PANELES_ARGENTINA:
        r = iol._get(f"/api/v2/Cotizaciones/Acciones/{panel}/argentina")
        titulos.extend(r["titulos"])

    dedup = _deduplicar_por_empresa(titulos)

    filas = []
    for _, row in dedup.iterrows():
        ticker = row["ticker"]
        filas.append({
            "ticker_usd": ticker,
            "ticker_cedear": None,  # no es un CEDEAR, es la accion local
            "ticker_yahoo": f"{ticker}.BA",
            "ticker_adr_usa": ADR_ARGENTINA.get(ticker),
            "mercado": "argentina_local",
            "nombre_empresa": row["empresa"].title(),
            "variantes": row["variantes"],
        })

    return pd.DataFrame(filas).sort_values("nombre_empresa").reset_index(drop=True)


CRIPTO = [
    {"ticker_usd": "BTC", "ticker_yahoo": "BTC-USD", "nombre_empresa": "Bitcoin"},
    {"ticker_usd": "ETH", "ticker_yahoo": "ETH-USD", "nombre_empresa": "Ethereum"},
    {"ticker_usd": "SOL", "ticker_yahoo": "SOL-USD", "nombre_empresa": "Solana"},
]


def listar_cripto() -> pd.DataFrame:
    """Cripto no viene de IOL -- son 3 tickers fijos. Sin fundamentales (no
    tienen balance ni ganancias): analisis_fundamental_liviano.py/_pesado.py
    los saltean, solo alimentan fact_precios_daily via precios_historicos.py."""
    return pd.DataFrame([
        {
            "ticker_usd": c["ticker_usd"],
            "ticker_cedear": None,
            "ticker_yahoo": c["ticker_yahoo"],
            "ticker_adr_usa": None,
            "mercado": "cripto",
            "nombre_empresa": c["nombre_empresa"],
            "variantes": c["ticker_usd"],
        }
        for c in CRIPTO
    ])


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "listado_cedears")
    con_check.close()
    if ya_corrio:
        print("listado_cedears ya corrio hoy con exito, no hace falta repetir.")
        return

    iol = IOLClient()
    iol.login()
    print("Ingreso a IOL: OK\n")

    cedears = listar_empresas(iol)
    argentina = listar_acciones_argentinas(iol)
    cripto = listar_cripto()
    empresas = pd.concat([cedears, argentina, cripto], ignore_index=True)

    print(f"Universo total: {len(empresas)}")
    print(f"  CEDEARs: {len(cedears)} (de Brasil: {(cedears['mercado'] == 'brasil').sum()})")
    print(f"  Acciones argentinas locales: {len(argentina)} "
          f"(con ADR en EE.UU.: {argentina['ticker_adr_usa'].notna().sum()})")
    print(f"  Cripto: {len(cripto)}\n")
    print(empresas[["ticker_usd", "mercado", "nombre_empresa"]].to_string(index=False))

    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)
    destino = out_dir / "cedears_normalizados.csv"
    empresas.to_csv(destino, index=False)
    print(f"\nListado guardado en {destino}")

    con = db.conectar()
    with db.registrar(con, "listado_cedears") as log:
        db.upsert_dim_empresa(con, empresas.to_dict("records"))
        log["filas_afectadas"] = len(empresas)
    con.close()
    print("dim_empresa actualizada en data/warehouse.duckdb")


if __name__ == "__main__":
    main()
