"""
analisis_fundamental.py
------------------------
Momentum fundamental de las empresas de EE.UU. detras de los CEDEARs, usando
Yahoo Finance (yfinance).

Que mide "momentum" aca:
  - crecimiento interanual (YoY) de ingresos y ganancia neta (serie anual,
    ~5 anios de historia)
  - si ese crecimiento viene acelerando o desacelerando (compara las ultimas
    2 tasas YoY)
  - sorpresas de EPS vs. estimado de los ultimos 4 informes trimestrales
    (earnings_dates trae ~12 trimestres de historia, mucho mas que el estado
    de resultados trimestral que solo trae ~5)

Ademas trae un snapshot de ratios de valuacion (P/E, PEG, P/B, EV/EBITDA,
margenes, ROE, ROA, deuda/patrimonio, liquidez, dividendo, beta) para poder
comparar empresas entre si, no solo en el tiempo.

Tambien persiste series anuales crudas para graficar tendencia real (no solo
el % de cambio): ingresos/costo de ingresos/beneficio bruto/gastos operativos/
ganancia neta (income statement), deuda total/efectivo/flujo de caja libre
(balance sheet + cashflow -- fuentes nuevas), y EPS estimado/reportado por
trimestre incluyendo el proximo informe (todavia sin reportar).

Usa "ticker_yahoo" de cedears_normalizados.csv (no "ticker_usd") porque IOL y
Yahoo Finance no siempre usan el mismo simbolo (ver TICKERS_YAHOO en
listado_cedears.py: BDRs brasileños con ".SA", tickers mal mapeados, etc.)

Yahoo banea (429) si le pegas a muchos tickers seguido sin pausa, asi que
cada ticker se cachea en disco: si el proceso corta a mitad de camino, se
puede volver a correr y retoma sin re-pedir lo que ya bajo.

Correr: python analisis_fundamental.py
"""

import json
import re
import time
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yfinance as yf
from dotenv import load_dotenv

import db

load_dotenv()

CACHE_DIR = Path("cache/fundamentals")
CACHE_DIAS = 7  # no volver a pedir un ticker si el cache tiene menos de N dias
PAUSA_ENTRE_TICKERS = 1.5  # segundos, para no gatillar el rate limit de Yahoo


def _cache_path(ticker: str) -> Path:
    """Windows no admite *, /, \\, :, ?, ", <, > ni | en nombres de archivo
    (ej. el simbolo real 'BKC*' de IOL)."""
    nombre_seguro = re.sub(r'[\\/*?:"<>|]', "_", ticker)
    return CACHE_DIR / f"{nombre_seguro}.json"


# Campos de yf.Ticker().get_info() que sirven para comparar valuacion entre
# empresas. Los que terminan en "_%" ya vienen como fraccion (0.478) en
# yfinance y se multiplican x100; dividendYield ya viene en % (no fraccion).
CAMPOS_INFO_FRACCION = {
    "grossMargins": "margen_bruto_%",
    "operatingMargins": "margen_operativo_%",
    "profitMargins": "margen_neto_%",
    "returnOnEquity": "roe_%",
    "returnOnAssets": "roa_%",
    "payoutRatio": "payout_ratio_%",
    "heldPercentInsiders": "insider_holding_%",
    "heldPercentInstitutions": "institutional_holding_%",
}
CAMPOS_INFO_DIRECTOS = {
    "marketCap": "market_cap",
    "enterpriseValue": "enterprise_value",
    "trailingPE": "pe_trailing",
    "forwardPE": "pe_forward",
    "trailingPegRatio": "peg_ratio",
    "priceToBook": "price_to_book",
    "enterpriseToEbitda": "ev_ebitda",
    "debtToEquity": "deuda_patrimonio",
    "currentRatio": "razon_corriente",
    "dividendYield": "dividend_yield_%",
    "beta": "beta",
    "sharesOutstanding": "shares_outstanding",
}
# Estos van a dim_empresa (descriptivos), no a fact_metrics_daily.
CAMPOS_INFO_DIMENSION = {"sector": "sector", "industry": "industria", "country": "pais_origen"}

# calcular_momentum() arma columnas con "%" (para que se lean bien en el CSV/
# Excel); DuckDB las guarda con sufijo "_pct" (SQL no admite "%" en nombres de
# columna). Este dict es la unica fuente de verdad de esa traduccion.
COLUMNAS_A_DB = {
    "crecimiento_ingresos_anual_%": "crecimiento_ingresos_anual_pct",
    "crecimiento_ganancia_anual_%": "crecimiento_ganancia_anual_pct",
    "aceleracion_ingresos_%": "aceleracion_ingresos_pct",
    "eps_growth_3y_%": "eps_growth_3y_pct",
    "eps_growth_5y_%": "eps_growth_5y_pct",
    "sorpresa_eps_prom_4q_%": "sorpresa_eps_prom_4q_pct",
    "trimestres_superando_estimado": "trimestres_superando_estimado",
    "market_cap": "market_cap",
    "enterprise_value": "enterprise_value",
    "pe_trailing": "pe_trailing",
    "pe_forward": "pe_forward",
    "peg_ratio": "peg_ratio",
    "price_to_book": "price_to_book",
    "ev_ebitda": "ev_ebitda",
    "margen_bruto_%": "margen_bruto_pct",
    "margen_operativo_%": "margen_operativo_pct",
    "margen_neto_%": "margen_neto_pct",
    "roe_%": "roe_pct",
    "roa_%": "roa_pct",
    "deuda_patrimonio": "deuda_patrimonio",
    "razon_corriente": "razon_corriente",
    "dividend_yield_%": "dividend_yield_pct",
    "payout_ratio_%": "payout_ratio_pct",
    "beta": "beta",
    "insider_holding_%": "insider_holding_pct",
    "institutional_holding_%": "institutional_holding_pct",
    "shares_outstanding": "shares_outstanding",
}


# Sube este numero cada vez que obtener_datos_crudos() empiece a guardar un
# campo nuevo: invalida el cache viejo (que no lo tiene) sin tener que borrar
# la carpeta a mano.
VERSION_CACHE = 5


def _cache_vigente(path: Path) -> bool:
    if not path.exists():
        return False
    datos = json.loads(path.read_text(encoding="utf-8"))
    if datos.get("_version_cache") != VERSION_CACHE:
        return False
    edad_dias = (time.time() - path.stat().st_mtime) / 86400
    return edad_dias < CACHE_DIAS


def _serie_a_dict(serie: pd.Series) -> dict:
    return {str(fecha.date()): float(valor) for fecha, valor in serie.items() if pd.notna(valor)}


def obtener_datos_crudos(ticker: str) -> dict:
    """Estados de resultados (anual + trimestral) y sorpresas de EPS, con cache en disco."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(ticker)

    if _cache_vigente(path):
        return json.loads(path.read_text(encoding="utf-8"))

    datos = {"ticker": ticker, "_version_cache": VERSION_CACHE}
    try:
        t = yf.Ticker(ticker)

        anual = t.income_stmt
        if anual is not None and "Total Revenue" in anual.index:
            datos["ingresos_anuales"] = _serie_a_dict(anual.loc["Total Revenue"])
        if anual is not None and "Cost Of Revenue" in anual.index:
            datos["costo_ingresos_anual"] = _serie_a_dict(anual.loc["Cost Of Revenue"])
        if anual is not None and "Gross Profit" in anual.index:
            datos["beneficio_bruto_anual"] = _serie_a_dict(anual.loc["Gross Profit"])
        if anual is not None and "Operating Expense" in anual.index:
            datos["gastos_operativos_anual"] = _serie_a_dict(anual.loc["Operating Expense"])
        if anual is not None and "Net Income" in anual.index:
            datos["ganancia_anual"] = _serie_a_dict(anual.loc["Net Income"])
        if anual is not None and "Diluted EPS" in anual.index:
            datos["eps_anual"] = _serie_a_dict(anual.loc["Diluted EPS"])

        # Fase B: balance y cashflow -- fuentes nuevas (antes solo pediamos
        # income_stmt). Deuda/efectivo/FCF son lo que le falta al warehouse
        # para el grafico "Nivel de deuda y cobertura".
        balance = t.balance_sheet
        if balance is not None and "Total Debt" in balance.index:
            datos["deuda_anual"] = _serie_a_dict(balance.loc["Total Debt"])
        if balance is not None and "Cash And Cash Equivalents" in balance.index:
            datos["efectivo_anual"] = _serie_a_dict(balance.loc["Cash And Cash Equivalents"])

        flujo = t.cashflow
        if flujo is not None and "Free Cash Flow" in flujo.index:
            datos["fcf_anual"] = _serie_a_dict(flujo.loc["Free Cash Flow"])

        earnings = t.earnings_dates
        if earnings is not None and not earnings.empty:
            # head(5), sin filtrar NaN: yfinance devuelve mas reciente primero,
            # asi que esto trae el proximo trimestre (todavia sin "Reported EPS")
            # + los ~4 ya reportados. Filtrar antes perdia la fecha del proximo
            # informe, que es justo lo que hace falta para "Siguiente: <fecha>".
            ultimos = earnings.head(5)
            datos["eps_trimestral"] = [
                {
                    "fecha": str(fecha.date()),
                    "eps_estimado": float(fila["EPS Estimate"]) if pd.notna(fila["EPS Estimate"]) else None,
                    "eps_reportado": float(fila["Reported EPS"]) if pd.notna(fila["Reported EPS"]) else None,
                    "sorpresa_pct": float(fila["Surprise(%)"]) if pd.notna(fila["Surprise(%)"]) else None,
                }
                for fecha, fila in ultimos.iterrows()
            ]

        info = t.get_info() or {}
        campos_a_guardar = {**CAMPOS_INFO_FRACCION, **CAMPOS_INFO_DIRECTOS, **CAMPOS_INFO_DIMENSION}
        datos["info"] = {campo: info.get(campo) for campo in campos_a_guardar}
    except Exception as e:
        datos["error"] = str(e)

    path.write_text(json.dumps(datos, ensure_ascii=False), encoding="utf-8")
    time.sleep(PAUSA_ENTRE_TICKERS)
    return datos


def _crecimiento_yoy(serie: dict) -> list[float]:
    """% de crecimiento interanual entre periodos consecutivos, del mas viejo al mas nuevo."""
    fechas = sorted(serie.keys())
    valores = [serie[f] for f in fechas]
    crecimientos = []
    for anterior, actual in zip(valores, valores[1:]):
        if anterior:
            crecimientos.append((actual - anterior) / abs(anterior) * 100)
    return crecimientos


def _cagr_eps(serie: dict, anios: int) -> float | None:
    """Crecimiento anualizado (CAGR) del EPS diluido entre el balance mas
    reciente y el de "anios" balances atras. Yahoo gratis solo da ~4-5
    balances anuales, asi que eps_growth_5y va a dar None para la mayoria de
    las empresas -- no hay suficiente historia para calcularlo, y es mejor
    dejarlo vacio que inventar un numero."""
    fechas = sorted(serie.keys())
    valores = [serie[f] for f in fechas]
    if len(valores) <= anios:
        return None
    inicial, final = valores[-1 - anios], valores[-1]
    if inicial is None or final is None or inicial <= 0 or final <= 0:
        return None  # CAGR no tiene sentido bien definido con base negativa/cero
    return (((final / inicial) ** (1 / anios)) - 1) * 100


def calcular_momentum(datos: dict) -> dict:
    fila = {"ticker": datos.get("ticker")}

    crec_ingresos = _crecimiento_yoy(datos.get("ingresos_anuales", {}))
    crec_ganancia = _crecimiento_yoy(datos.get("ganancia_anual", {}))

    fila["crecimiento_ingresos_anual_%"] = round(crec_ingresos[-1], 1) if crec_ingresos else None
    fila["crecimiento_ganancia_anual_%"] = round(crec_ganancia[-1], 1) if crec_ganancia else None
    fila["aceleracion_ingresos_%"] = (
        round(crec_ingresos[-1] - crec_ingresos[-2], 1) if len(crec_ingresos) >= 2 else None
    )

    eps_anual = datos.get("eps_anual", {})
    for anios in (3, 5):
        cagr = _cagr_eps(eps_anual, anios)
        fila[f"eps_growth_{anios}y_%"] = round(cagr, 1) if cagr is not None else None

    reportados = [e for e in datos.get("eps_trimestral", []) if e["eps_reportado"] is not None][:4]
    sorpresas = [e["sorpresa_pct"] for e in reportados if e["sorpresa_pct"] is not None]
    fila["sorpresa_eps_prom_4q_%"] = round(sum(sorpresas) / len(sorpresas), 1) if sorpresas else None
    fila["trimestres_superando_estimado"] = sum(1 for s in sorpresas if s > 0) if sorpresas else None

    info = datos.get("info", {})
    for campo, nombre_salida in CAMPOS_INFO_FRACCION.items():
        valor = info.get(campo)
        fila[nombre_salida] = round(valor * 100, 1) if valor is not None else None
    for campo, nombre_salida in CAMPOS_INFO_DIRECTOS.items():
        valor = info.get(campo)
        fila[nombre_salida] = round(valor, 2) if isinstance(valor, (int, float)) else valor
    for campo, nombre_salida in CAMPOS_INFO_DIMENSION.items():
        fila[nombre_salida] = info.get(campo)

    if "error" in datos:
        fila["error"] = datos["error"]

    return fila


def main() -> None:
    con_check = db.conectar()
    ya_corrio = db.ya_corrio_hoy(con_check, "analisis_fundamental")
    con_check.close()
    if ya_corrio:
        print("analisis_fundamental ya corrio hoy con exito, no hace falta repetir.")
        return

    universo = pd.read_csv("data/cedears_normalizados.csv")
    # Cripto no tiene balance, ganancias ni EPS que pedir -- solo alimenta
    # fact_precios_daily via precios_historicos.py, no este script.
    universo = universo[universo["mercado"] != "cripto"].reset_index(drop=True)

    filas = []
    filas_income_statement = []
    filas_eps = []
    filas_balance_cashflow = []
    for i, fila_universo in enumerate(universo.itertuples(), 1):
        print(f"[{i}/{len(universo)}] {fila_universo.ticker_yahoo}", end="\r")
        datos = obtener_datos_crudos(fila_universo.ticker_yahoo)
        momentum = calcular_momentum(datos)
        momentum["ticker"] = fila_universo.ticker_usd  # el simbolo que se ve en IOL
        filas.append(momentum)

        ticker_usd = fila_universo.ticker_usd
        ingresos = datos.get("ingresos_anuales", {})
        costos = datos.get("costo_ingresos_anual", {})
        brutos = datos.get("beneficio_bruto_anual", {})
        gastos = datos.get("gastos_operativos_anual", {})
        ganancias = datos.get("ganancia_anual", {})
        fechas_balance = set(ingresos) | set(costos) | set(brutos) | set(gastos) | set(ganancias)
        for fecha_str in fechas_balance:
            ing, gan = ingresos.get(fecha_str), ganancias.get(fecha_str)
            margen = (gan / ing * 100) if ing not in (None, 0) and gan is not None else None
            filas_income_statement.append({
                "ticker_usd": ticker_usd,
                "fecha_balance": datetime.fromisoformat(fecha_str).date(),
                "ingresos": ing,
                "costo_ingresos": costos.get(fecha_str),
                "beneficio_bruto": brutos.get(fecha_str),
                "gastos_operativos": gastos.get(fecha_str),
                "ganancia_neta": gan,
                "margen_neto_pct": round(margen, 1) if margen is not None else None,
            })

        deudas = datos.get("deuda_anual", {})
        efectivos = datos.get("efectivo_anual", {})
        fcfs = datos.get("fcf_anual", {})
        for fecha_str in set(deudas) | set(efectivos) | set(fcfs):
            filas_balance_cashflow.append({
                "ticker_usd": ticker_usd,
                "fecha_balance": datetime.fromisoformat(fecha_str).date(),
                "deuda_total": deudas.get(fecha_str),
                "efectivo": efectivos.get(fecha_str),
                "flujo_caja_libre": fcfs.get(fecha_str),
            })

        for e in datos.get("eps_trimestral", []):
            filas_eps.append({
                "ticker_usd": ticker_usd,
                "fecha_reporte": datetime.fromisoformat(e["fecha"]).date(),
                "eps_estimado": e["eps_estimado"],
                "eps_reportado": e["eps_reportado"],
                "sorpresa_pct": e["sorpresa_pct"],
            })
    print()

    resultado = pd.DataFrame(filas).merge(
        universo[["ticker_usd", "mercado", "nombre_empresa"]],
        left_on="ticker", right_on="ticker_usd",
    ).drop(columns="ticker_usd")
    resultado = resultado.sort_values("crecimiento_ingresos_anual_%", ascending=False)

    out_dir = Path("data")
    out_dir.mkdir(exist_ok=True)

    destino_csv = out_dir / "momentum_fundamental.csv"
    resultado.to_csv(destino_csv, index=False)

    destino_excel = out_dir / "momentum_fundamental.xlsx"
    resultado.to_excel(destino_excel, index=False, sheet_name="Momentum")

    print(f"Guardado en {destino_csv} y {destino_excel}")

    hoy = date.today()
    filas_metrics = [
        {
            "ticker_usd": f["ticker"],
            "fecha": hoy,
            **{COLUMNAS_A_DB[col]: f[col] for col in COLUMNAS_A_DB if col in f},
        }
        for f in filas
    ]
    filas_dim = universo.merge(
        pd.DataFrame(filas)[["ticker", "sector", "industria", "pais_origen"]],
        left_on="ticker_usd", right_on="ticker",
    ).to_dict("records")

    con = db.conectar()
    with db.registrar(con, "analisis_fundamental") as log:
        db.upsert_fact_metrics_daily(con, filas_metrics)
        db.upsert_dim_empresa(con, filas_dim)
        db.upsert_fact_income_statement_annual(con, filas_income_statement)
        db.upsert_fact_eps_trimestral(con, filas_eps)
        db.upsert_fact_balance_cashflow_annual(con, filas_balance_cashflow)
        log["filas_afectadas"] = len(filas_metrics)
    con.close()
    print(f"fact_metrics_daily y dim_empresa actualizadas ({len(filas_metrics)} filas, fecha {hoy})")
    print(f"fact_income_statement_annual: {len(filas_income_statement)} filas | "
          f"fact_eps_trimestral: {len(filas_eps)} filas | "
          f"fact_balance_cashflow_annual: {len(filas_balance_cashflow)} filas")


if __name__ == "__main__":
    main()
