# Reglas de la cartera

Qué reglas usa el proyecto para elegir, vigilar y evaluar la cartera, y **por qué existe cada una**. El código es la fuente de verdad de los umbrales; este documento explica el motivo. Si se cambia una regla, se actualiza acá en el mismo commit, con fecha y motivo.

Casi todas las reglas nacieron de un error concreto. Están anotados para que no se repitan.

## 1. Dos preguntas, dos capas

| Pregunta | Capa | Dónde |
|---|---|---|
| ¿El dato es correcto? | Silver | `silver/calidad_datos.py` → `silver_calidad_datos` |
| ¿La empresa sirve para la cartera? | Gold / selección | `seleccionar_cartera.py` |

Silver **marca, no borra**: algunas anomalías son reales (XP subió 30% en un día después de la elección de Brasil). Un dato puede ser correcto y la empresa igual no servir: una acción poco operada sirve para analizar, no para comprar en una cartera concentrada.

## 2. Controles de datos (silver)

**Error** — la empresa no puntúa en la cartera:

| Regla | Motivo |
|---|---|
| Cotización sin movimiento (más de 5 ruedas con volumen 0 en 30 días) | B-Gaming tuvo el precio clavado durante días y un único print de +2164% (15/10/2026); con los datos previos quedaba 2.ª del ranking. |
| Precio desactualizado (más de 7 días de atraso respecto de su mercado) | AXIA y WEGE3.SA sin precios desde agosto; Schwab desde el 2/10. Nadie lo había notado. |
| Precio inválido (≤ 0, máximo < mínimo) | Básico: no se puede calcular nada sobre eso. |
| Métricas desactualizadas (más de 7 días) | Puntuar con fundamentales viejos sin saberlo. |

**Advertencia** — se usa, pero se muestra para revisar:

| Regla | Motivo |
|---|---|
| Salto de precio (+100% / −50% en un día) | Puede ser real o un error; hay que mirarlo. |
| Ganancia no recurrente (margen neto 10+ puntos sobre el operativo) | Infla la ganancia y hace parecer barato el P/E (Trip.com, SK Hynix). |
| Moneda inconsistente (valor de empresa negativo o 10× la capitalización; no aplica a bancos) | Yahoo mezcla la moneda del balance con la de la cotización (SK Hynix: wones con dólares). |
| Tenencia institucional no confiable (< 5% en listado de menos de un año) | SK Hynix sumaba "baja cobertura institucional" con 0.7% solo porque el ADR empezó a cotizar en julio. |

## 3. Filtros obligatorios de la cartera

Son **filtros, no alertas**. La liquidez y la historia corta empezaron como alertas, y aun así SK Hynix (69 días de historia) quedó primera y B-Gaming segunda (octubre 2026).

| Filtro | Motivo |
|---|---|
| Sin errores de datos (sección 2) | Primero el dato tiene que ser correcto. |
| Salud financiera distinta de "Riesgo" | No comprar empresas con el balance comprometido. |
| Ganancia que no cayó más de 20% | Evita trampas con checklist alto pero negocio roto (Magazine Luiza, Hapvida). |
| Margen neto positivo | Las no rentables cayeron −28% mediano a 12 meses (octubre 2026). |
| Volumen promedio ≥ 50.000 acciones/día | Una cartera concentrada tiene que poder entrar y salir. |
| Al menos 252 ruedas (un año) de precios | Sin historia no hay forma de medir el riesgo. |

## 4. Ranking

Promedio de tres percentiles: checklist de Lynch, P/E contra su sector y salud financiera.

**El P/E bajo no suma (queda neutral) cuando no es confiable:**
- **Cíclicas** (categoría Lynch "Cíclica"): en las cíclicas el P/E más bajo aparece en el pico de ganancias, justo antes de que caigan. SK Hynix era la "más barata" en pleno pico del ciclo de memorias y cayó 26% en un mes.
- **Ganancia no recurrente** (advertencia de silver): el P/E parece más bajo de lo que es.

El ranking **todavía no está probado** (ver sección 7). Es una hipótesis razonable, no una verdad.

## 5. Alertas para revisar a mano

No restan puntos; aparecen en la salida de `seleccionar_cartera.py` y en el monitoreo: mayoría de analistas en venta, noticias negativas, historia de menos de ~4 años (no alcanza para el análisis de riesgo a 5 años) y advertencias de datos.

## 6. Diversificación

Máximo 2 por sector **y** 2 por región real. El sector solo no alcanza: las argentinas de sectores distintos correlacionaban 0.4–0.7 entre sí, y PDD/Trip.com (ambas China, pero con domicilio legal en Irlanda y Singapur) 0.59. La región usa `REGION_REAL` para corregir domicilios legales, y el panel local entero como región para Argentina y Brasil.

## 7. Registro, monitoreo y evaluación

| Pieza | Qué hace | Motivo |
|---|---|---|
| `fact_cartera_seleccionada` | Guarda cada cartera elegida, con el cierre del día como precio de selección. La corrida diaria guarda una por semana, la primera de cada semana (`origen = 'semanal'`); la que se decide usar se guarda con `--guardar=manual`. | No guardábamos qué elegíamos: no había forma de saber si el ranking funciona. |
| `monitoreo_cartera.py` | Reporte diario de la última cartera guardada (`reportes/monitoreo_<fecha>.md`) y notificación de Windows si hay alertas. | SK Hynix cayó 25% en 8 ruedas y nos enteramos porque el usuario lo vio por su cuenta. |
| `evaluar_carteras.py` | Compara cada cartera guardada contra la mediana del universo y el S&P. | El ranking nunca se había probado. |
| `seguimiento` / `seguimiento.py` | Lista de acciones a vigilar antes de comprarlas, con soporte, resistencia y nivel de salida. El monitoreo avisa al tocar un nivel, 7 días antes del reporte y durante los 7 días posteriores, con la reacción del precio. | Para entradas planificadas (por ejemplo "entrar post-reporte") sin depender de acordarse de mirar. |

Alertas del monitoreo: caída de más de 10% en 5 ruedas, más de 15% desde la selección, RSI ≤ 25 o ≥ 80, resultados en los próximos 7 días, noticias negativas, error de datos. **Avisan, no venden.**

La evaluación es solo hacia adelante: los fundamentales diarios se guardan recién desde el 14/08/2026, así que no se puede reconstruir el ranking en el pasado. Antes de 3 meses por cartera, cualquier diferencia es ruido.

## 8. Refugios

`gold/refugio.py` clasifica las acciones en dólares según cómo se comportaron en las caídas reales del S&P de más de 10% (en octubre 2026: 2022 y 2025):

- **Refugio**: cae menos de 30% de lo que cae el S&P en sus días malos, y nunca perdió más de 10% en un episodio.
- **Defensiva**: cae menos de 60% de lo que cae el S&P.

Que algo haya sido refugio no lo hace barato: mirarlo junto con valuación y técnico. Y un refugio de un tipo de crisis puede no serlo en otra (la energía subió en 2022 y cayó en 2025).

## 9. Forma de trabajo

- Lo que surge de un tweet o una idea nueva se analiza primero en una notebook aparte. Entra al ranking solo si pasa una prueba con datos.
- Revisión los domingos, después de la corrida semanal: leer `reportes/`, correr `seleccionar_cartera.py`, decidir cambios.
- Un cambio de regla se documenta acá, con fecha y motivo.

## Historial

| Fecha | Cambio | Motivo |
|---|---|---|
| 2026-10-07 | Tope de 2 por región además de por sector | Correlación oculta entre argentinas y entre chinas |
| 2026-10-16 | Liquidez de alerta a filtro | B-Gaming 2.ª del ranking con cotización muerta |
| 2026-10-16 | Capa silver; filtros de datos e historia; P/E neutral en cíclicas y ganancias no recurrentes; tenencia institucional de listados nuevos anulada | SK Hynix 1.ª del ranking con 69 días de historia, P/E de pico de ciclo y datos inconsistentes |
| 2026-10-16 | Registro de carteras, monitoreo diario con notificación, evaluación; segmento de refugios | No se guardaban las carteras ni se las vigilaba |
| 2026-10-20 | Lista de seguimiento en el monitoreo (primera: UBER, entrada post-reporte del 4/11) | Vigilar entradas planificadas, no solo la cartera |
| 2026-10-20 | Guardado semanal pasa de la corrida semanal a la diaria; métricas con forward-fill en salud financiera y silver | El 18/10 la semanal corrió entre pesado y liviano, con la fila de métricas a medio llenar, y no pasó ninguna empresa |
