# CEDEARs Screener

Screener de activos operables desde Argentina: acciones de EE.UU./BDRs vía
CEDEARs, acciones argentinas locales (Merval y paneles relacionados), y
cripto. Fuentes de datos: API de InvertirOnline (universo CEDEARs + acciones
locales) y Yahoo Finance / yfinance (fundamentales, precios y ratios de
valuación de la empresa detrás de cada activo).

`dim_empresa.mercado` distingue las cuatro fuentes:

| `mercado` | Qué es | `ticker_yahoo` |
|---|---|---|
| `usa_otros` | CEDEAR de empresa de EE.UU. (u otra bolsa no-Brasil) | Ticker de EE.UU./exchange original |
| `brasil` | CEDEAR de BDR brasileño | `<ticker>.SA` |
| `argentina_local` | Acción local de BYMA (no es CEDEAR, es la empresa argentina en sí) | `<ticker>.BA` |
| `cripto` | BTC/ETH/SOL, fijos, no vienen de IOL | `<ticker>-USD` |

Las 13 acciones argentinas que además cotizan como ADR directo en EE.UU.
(GGAL, BMA, YPF, PAM, CRESY, SUPV, LOMA, EDN, TGS, TEO, BBAR, IRS, CEPU)
suman ese ticker en `dim_empresa.ticker_adr_usa` — no reemplaza al `.BA`, es
un dato extra para comparar precio local vs. ADR (brecha cambiaria
implícita). Cripto no tiene fundamentales (no hay balance ni ganancias que
pedir), así que `analisis_fundamental_liviano.py`/`analisis_fundamental_pesado.py`
la saltean — solo alimenta `fact_precios_daily` vía `precios_historicos.py`.

## Arquitectura por capas

```mermaid
flowchart LR
    subgraph Extract
        IOL["IOL API<br/>(iol_client.py)"]
        YF["Yahoo Finance<br/>(yfinance)"]
    end

    subgraph Bronze["Bronze (crudo)"]
        cache[("cache/*.json<br/>cache/precios/*.csv")]
    end

    subgraph Transform_Load["Transform + Load (hoy: mismo script Python)"]
        listado["listado_cedears.py<br/>dedup, mapeo ticker IOL->Yahoo"]
        liviano["analisis_fundamental_liviano.py<br/>.info (diario, 1 call/ticker)"]
        pesado["analisis_fundamental_pesado.py<br/>estados contables (semanal, 4 calls/ticker)"]
        precios["precios_historicos.py<br/>OHLCV"]
        macro["macro_diario.py<br/>tasas, VIX, FX, commodities, indices"]
        db["db.py<br/>upsert a DuckDB"]
    end

    subgraph Silver["Silver — data/warehouse.duckdb"]
        dim[("dim_empresa")]
        fm[("fact_metrics_daily")]
        fp[("fact_precios_daily")]
        fi[("fact_income_statement_annual")]
        fe[("fact_eps_trimestral")]
        fma[("fact_macro_daily")]
    end

    subgraph Gold["Gold — una metodologia por archivo, columnas prefijadas"]
        lynch["gold/lynch.py<br/>vista gold_lynch (columnas lynch_*)"]
        comp["gold/comparables.py<br/>vista gold_comparables (columnas comp_*)"]
        macrosens["gold/macro_sensitivity.py<br/>vista gold_macro_sensitivity (columnas macro_*)"]
        quant["gold/quant.py (futuro)"]
    end

    subgraph Consumo
        nb["notebooks/eda_cedears.ipynb"]
    end

    IOL --> cache
    YF --> cache
    cache --> listado --> db
    cache --> liviano --> db
    cache --> pesado --> db
    cache --> precios --> db
    cache --> macro --> db
    db --> dim & fm & fp & fi & fe & fma
    dim & fm & fp & fi & fe --> lynch & comp & quant
    dim & fp & fma --> macrosens
    dim & fm & fp & fi & fe & fma --> nb
    lynch --> nb
    comp --> nb
    macrosens --> nb
```

**Estado actual:** Transform y Load viven juntos, en Python (pandas), dentro de
cada script de Extract — es un ETL clásico, no ELT. **Roadmap:** migrar
Transform a modelos de `dbt` (dbt-duckdb) para separarlo de Extract y sumar
tests/documentación/lineage automático.

**Gold ya es real** (no vistas de ejemplo): cada metodología de análisis
(Lynch, y a futuro quant/técnico) vive en su propio archivo dentro de `gold/`,
crea su propia vista SQL en el warehouse, y prefija todas sus columnas
(`lynch_*`) — así conviven varias metodologías sobre las mismas empresas sin
que una le pise las columnas a otra.

## Arquitectura de datos

Warehouse local en `data/warehouse.duckdb` (un solo archivo, sin servidor),
modelo dimensional:

- **`dim_empresa`** — descriptiva, cambia poco (PK `ticker_usd`): identidad
  (tickers, mercado), `sector`/`industria`/`pais_origen` (de Yahoo),
  `sector_corregido` (override manual cuando Yahoo clasifica mal — ver
  `correcciones_sector.py` — nunca se pisa con el upsert automático, mismo
  criterio que lo de abajo), y `lynch_category`/`modelo_negocio`
  (clasificación manual/asistida por LLM, pendiente — ningún proveedor de
  datos la da gratis).
- **`fact_metrics_daily`** — un snapshot por día (PK `ticker_usd` + `fecha`):
  crecimiento de ingresos/ganancia, aceleración, CAGR de EPS, sorpresas de
  EPS, y todos los ratios de valuación (P/E, PEG, P/B, EV/EBITDA, márgenes,
  ROE, ROA, deuda/patrimonio, liquidez, dividendo, beta, tenencia
  insider/institucional).
- **`fact_precios_daily`** — un OHLCV por día (PK `ticker_usd` + `fecha`).
- **`fact_income_statement_annual`** — ingresos/ganancia neta en $ por balance
  anual (PK `ticker_usd` + `fecha_balance`). Es la serie real detrás de
  `crecimiento_ingresos_anual_pct` — para graficar tendencia, no solo el %.
- **`fact_eps_trimestral`** — EPS estimado/reportado por trimestre (PK
  `ticker_usd` + `fecha_reporte`), incluye el **próximo** informe (todavía
  sin `eps_reportado`) para saber cuándo es el próximo reporte.
- **`fact_balance_cashflow_annual`** — deuda total, efectivo y flujo de caja
  libre por balance anual (PK `ticker_usd` + `fecha_balance`).
- **`fact_macro_daily`** — una fila por serie macro y día (PK `serie` +
  `fecha`): tasas (`UST3M/5Y/10Y/30Y`), riesgo (`VIX`, `HYG`, `LQD`), dólar
  (`DXY`, `USDBRL`, `USDARS`), commodities (`WTI`, `GOLD`, `COPPER`) e
  índices de referencia (`SP500`, `MERVAL`, `BOVESPA`). Grano de mercado, no
  de empresa — por eso es una tabla angosta (`serie`, `fecha`, `valor`) en
  vez de una columna por serie en `fact_metrics_daily`.
- **`log_ejecuciones`** — append-only, una fila por corrida de cada script
  (`ok`/`error`, cuántas filas afectó, y el detalle si falló). Sirve para
  tres cosas: idempotencia (no repetir un job que ya corrió hoy si el
  catch-up de Task Scheduler dispara dos veces el mismo día), tablero de
  salud (`python estado_pipeline.py`), y diagnóstico (el error queda
  guardado, no hay que rescatarlo de la terminal).

Cada tabla de datos se carga con `UPSERT` (`ON CONFLICT ... DO UPDATE`), así
que correr un script dos veces el mismo día no duplica filas, y correrlo días
distintos sí acumula historial real (no pisa el día anterior).

## Orden de ejecución

```
# Diario
python precios_historicos.py             # OHLCV (Yahoo)
python analisis_fundamental_liviano.py   # .info: P/E, P/B, market cap... (Yahoo, 1 call/ticker)
python macro_diario.py                   # tasas, VIX, FX, commodities, indices (Yahoo, 16 series)
python gold/lynch.py                     # recalcula la vista gold_lynch (no pide datos nuevos)
python gold/comparables.py               # recalcula la vista gold_comparables (idem)
python gold/macro_sensitivity.py         # recalcula la vista gold_macro_sensitivity (idem, va despues de lynch.py -- su reporte hace JOIN contra gold_lynch)

# Semanal (domingos)
python listado_cedears.py                # universo IOL -> dim_empresa + cedears_normalizados.csv
python analisis_fundamental_pesado.py    # income statement, balance, cashflow, earnings (Yahoo, 4 calls/ticker)
```

`listado_cedears.py` tiene que correr antes que cualquier otro Yahoo: los
demás leen `data/cedears_normalizados.csv` (el universo + el mapeo de ticker
de IOL al ticker real de Yahoo) para saber qué pedir. Como corre semanal, la
primera vez (o si el CSV no existe) hay que correrlo a mano una vez antes de
`analisis_fundamental_liviano.py`/`precios_historicos.py`. `gold/lynch.py` y
`gold/comparables.py` van al final de cada tanda porque solo leen lo que ya
está en el warehouse — no llaman a IOL ni a Yahoo.

Todos los scripts que le pegan a una API (menos las vistas `gold/`) chequean
`db.ya_corrio_hoy()` (diarios) o `db.ya_corrio_reciente(..., dias=6)`
(semanales) al arrancar, y no repiten el trabajo si ya corrieron con éxito
en la ventana correspondiente — pensado para que un catch-up de Task
Scheduler (PC apagada a la hora programada, corre al prenderla) no dispare
una corrida redundante.

`analisis_fundamental_liviano.py` y `analisis_fundamental_pesado.py` escriben
en la misma tabla (`fact_metrics_daily`) con cadencias distintas; el UPSERT
usa `COALESCE(excluded.col, valor_actual)` en vez de pisar directo, así que
si los dos corren el mismo día (los domingos) ninguno le borra al otro las
columnas que no le tocan (ver el comentario en `db.upsert_fact_metrics_daily`).

**Ojo, ese COALESCE no alcanza solo:** protege conflictos del *mismo día*
(misma fila, mismo PK), pero cada día nuevo es una fila nueva — si un día
corre solo `liviano`, la fila de *ese día* tiene NULL en las columnas que
solo trae `pesado` (`crecimiento_ingresos_anual_pct`, etc.), aunque el dato
siga vigente de la semana pasada. `gold_lynch.py` y `gold_comparables.py`
resuelven esto con `LAST_VALUE(col IGNORE NULLS) OVER (...)` — forward-fill
de cada columna a su último valor conocido antes de quedarse con la fila
más reciente por empresa. Bug real que apareció la primera vez que corrió
`liviano` dos días seguidos sin `pesado` en el medio (207 empresas quedaron
"Sin clasificar" en `gold_lynch` hasta que se agregó el forward-fill).

### Cadencia (Task Scheduler)

Configurado como dos tareas de Windows (`CedearsScreener-Diario` /
`CedearsScreener-Semanal`), creadas con `Register-ScheduledTask`, modo
"solo con sesión iniciada" y catch-up activado (`-StartWhenAvailable`: si la
PC estaba apagada a la hora programada, corre al prenderla). Cada una llama
a su script wrapper (`run_diario.ps1` / `run_semanal.ps1`), que encadena los
scripts de Python correspondientes.

No todo necesita correr con la misma frecuencia — depende de qué tan rápido
cambia cada fuente:

| Cadencia | Qué corre | Por qué |
|---|---|---|
| Diaria (~20:00 ART) | `precios_historicos.py` | El precio cambia todos los días hábiles |
| Diaria (~20:00 ART) | `analisis_fundamental_liviano.py` | Ratios como P/E se mueven con el precio, aunque la empresa no cambie |
| Diaria (~20:00 ART) | `macro_diario.py` | Tasas/VIX/FX/commodities cambian todos los días hábiles, igual que el precio |
| Diaria (~20:00 ART) | `gold/lynch.py`, `gold/comparables.py`, `gold/macro_sensitivity.py` | Solo recalculan sobre lo que ya se actualizó — sin costo de API |
| Semanal (domingos ~20:00 ART) | `listado_cedears.py` | El universo de CEDEARs rara vez cambia |
| Semanal (domingos ~20:00 ART) | `analisis_fundamental_pesado.py` | Los balances solo cambian ~4 veces al año |
| Mensual | Nada automatizado todavía | Reservado para revisión manual de `lynch_category`/`modelo_negocio` |

20:00 ART queda después del cierre de BYMA (17:00) y de NYSE/NASDAQ (17:00–18:00
ART según horario de verano en EE.UU.), con margen para que Yahoo termine de
asentar el dato del día.

### Corregir una clasificación de sector mal hecha por Yahoo

Yahoo clasifica por GICS, y a veces se equivoca para el negocio real de una
empresa (ej. STNE/PAGS/XYZ son procesadoras de pago, pero Yahoo las mete en
"Technology" — deforma las medianas de `gold_comparables` y la regla de
"Ciclica" de `gold_lynch`, que agrupan/filtran por sector). Para corregir un
caso nuevo: agregar un dict a `CORRECCIONES` en `correcciones_sector.py`
(ticker, sector correcto, motivo) y correr `python correcciones_sector.py`
una vez. Queda en `dim_empresa.sector_corregido`, que **nunca** se pisa con
el upsert automático — `gold/lynch.py` y `gold/comparables.py` ya usan
`COALESCE(sector_corregido, sector)`, así que la corrección se propaga sola
a toda la capa Gold sin perder el dato crudo de Yahoo. No es parte de la
cadencia diaria/semanal — se corre a mano cuando aparece un caso nuevo.

### Agregar una metodología nueva en `gold/`

Cada archivo en `gold/` es independiente: crea su propia vista con
`CREATE OR REPLACE VIEW`, lee de `dim_empresa`/`fact_*` y prefija **todas**
sus columnas de salida (`lynch_*`, y a futuro `quant_*`, `technical_*`) para
que nunca choquen entre sí ni con las columnas de otra metodología. Para
importar `db.py` (vive en la raíz) desde `gold/`, cada script agrega la raíz
del proyecto a `sys.path` al principio — ver el encabezado de `gold/lynch.py`.

## Estructura
```
cedears-data-pipeline/
├── .env.example           # plantilla de credenciales (copiar a .env)
├── .gitignore
├── requirements.txt
├── db.py                  # conexion + schema + upserts de DuckDB
├── iol_client.py           # cliente de la API de IOL (auth + endpoints)
├── listado_cedears.py      # universo IOL (CEDEARs + acciones argentinas) + criptomonedas fijas -> dim_empresa + cedears_normalizados.csv
├── analisis_fundamental_liviano.py  # .info de Yahoo (diario) -> fact_metrics_daily (ratios de valuación) + sector/industria
├── analisis_fundamental_pesado.py   # income statement/balance/cashflow/earnings de Yahoo (semanal) -> momentum + series crudas
├── precios_historicos.py   # OHLCV de Yahoo -> fact_precios_daily
├── macro_diario.py         # tasas/VIX/FX/commodities/indices de Yahoo -> fact_macro_daily
├── correcciones_sector.py  # override manual de sector cuando Yahoo clasifica mal -> dim_empresa.sector_corregido
├── run_diario.ps1          # wrapper para Task Scheduler: precios + liviano + macro + gold (diario)
├── run_semanal.ps1         # wrapper para Task Scheduler: listado_cedears + pesado (semanal)
├── estado_pipeline.py      # tablero de salud: ultima corrida (ok/error) de cada script
├── gold/                   # una metodologia de analisis = un archivo, columnas prefijadas
│   ├── lynch.py            # vista gold_lynch (categoria + PEG + checklist estilo Peter Lynch)
│   ├── comparables.py      # vista gold_comparables (empresa vs. mediana de su sector + pares similares)
│   └── macro_sensitivity.py  # vista gold_macro_sensitivity (correlacion retorno vs. tasa UST10Y/30Y)
├── notebooks/
│   ├── eda_cedears.ipynb   # EDA sobre el warehouse (calidad de datos, sectores, valuación, momentum, precios, Lynch, vista por acción, sensibilidad a tasa)
│   └── eda_brasil.ipynb    # Recorte a las BDRs brasileñas: retorno limpio vs. cambiario (USDBRL) y correlación vs. USDBRL/Bovespa
├── cache/                  # cache en disco por ticker (evita re-pedirle a Yahoo)
└── data/                   # se genera solo: CSVs + warehouse.duckdb
```

## Notebook de EDA

`notebooks/eda_cedears.ipynb` corre contra `data/warehouse.duckdb` (hay que
haber corrido los 3 scripts de arriba al menos una vez antes). Kernel
registrado como "Python 3 (.venv)" — si VS Code no lo detecta solo, `Ctrl+Shift+P`
→ "Notebook: Select Kernel" → elegir el del `.venv` del proyecto.

## Puesta en marcha (VS Code)

1. Abrir la carpeta en VS Code: `File > Open Folder...`

2. Crear el entorno virtual (terminal integrada, Ctrl+Ñ):
   ```bash
   python -m venv .venv
   ```

3. Activarlo:
   - Windows (PowerShell): `.venv\Scripts\Activate.ps1`
   - Mac/Linux: `source .venv/bin/activate`

4. Seleccionar el intérprete: `Ctrl+Shift+P` > "Python: Select Interpreter" > el de `.venv`.

5. Instalar dependencias:
   ```bash
   pip install -r requirements.txt
   ```

6. Configurar credenciales: copiar `.env.example` a `.env` y completar
   con tu usuario y clave de IOL.
   ```bash
   cp .env.example .env   # en Windows: copy .env.example .env
   ```

7. Correr, en este orden (ver "Orden de ejecución" más abajo):
   ```bash
   python listado_cedears.py
   python analisis_fundamental_liviano.py
   python analisis_fundamental_pesado.py
   python precios_historicos.py
   python macro_diario.py
   python gold/lynch.py
   python gold/comparables.py
   python gold/macro_sensitivity.py
   ```

## Notas
- La API de IOL debe estar habilitada en tu cuenta:
  Mi Cuenta > Personalización > APIs (aceptar términos).
- El `.env` con tus credenciales NUNCA se sube al repo.
- Consultar cotizaciones es solo lectura; las órdenes impactan en el entorno real.

## Troubleshooting: `SSLCertVerificationError` al conectar con IOL
Si tenés Norton (u otro antivirus con "SSL/TLS scanning") instalado, intercepta el
HTTPS de `api.invertironline.com` con un certificado propio que no está en el
bundle de certificados de Python (`certifi`), aunque sí está instalado en el
almacén de Windows (por eso el navegador funciona bien pero los scripts de Python no).

Solución (sin desactivar la protección del antivirus):
1. Exportar el certificado raíz de Norton desde el almacén de Windows:
   ```powershell
   $cert = Get-ChildItem Cert:\CurrentUser\Root | Where-Object { $_.Subject -like "*Norton*" } | Select-Object -First 1
   $pem = "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($cert.RawData, 'InsertLineBreaks') + "`n-----END CERTIFICATE-----"
   Set-Content -Path norton_root.pem -Value $pem -Encoding ascii
   ```
2. Combinarlo con el bundle de `certifi` en un archivo `combined_ca.pem`.
3. Agregar al `.env`:
   ```
   REQUESTS_CA_BUNDLE=C:\ruta\completa\a\combined_ca.pem
   ```
   Cada script ya hace `load_dotenv()` antes de crear el `IOLClient` o de
   llamar a yfinance, así que `requests` toma la variable automáticamente.

Si no usás Norton pero te da el mismo error, seguramente es otro antivirus/proxy
corporativo haciendo lo mismo — el diagnóstico es idéntico (`openssl s_client
-connect api.invertironline.com:443` te muestra quién firmó el certificado que
realmente estás recibiendo).

**El certificado de Norton rota de tanto en tanto.** Si un día `pip install`
o algún script que veía funcionar de repente tira el mismo
`SSLCertVerificationError` (incluso para hosts que antes andaban bien, como
`pypi.org`), no es un problema nuevo: hay que repetir el paso 1 (reexportar
`norton_root.pem`) y el paso 2 (reconstruir `combined_ca.pem`) con el
certificado actual del almacén de Windows — el archivo viejo quedó
desactualizado, no roto.
