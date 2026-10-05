# fx-bancos-centrales

Tasas de cambio diarias de bancos centrales, publicadas como JSON versionado en
este repositorio para alimentar dashboards. Se actualiza solo con GitHub Actions.

| id | Par | Qué es | Fuente |
|---|---|---|---|
| `usd_mxn_fix` | USD-MXN | FIX, fecha de determinación (**principal**) | Banxico SIE `SF43718` |
| `usd_mxn_liq` | USD-MXN | FIX "para pagos" (FIX de 2 días hábiles antes) | Banxico SIE `SF60653` |
| `usd_cop_trm` | USD-COP | TRM vigente (**principal**) | Banco de la República, SDMX `DF_TRM_DAILY_HIST` · verificada contra SFC (datos.gov.co) |
| `usd_cop_trm_mkt` | USD-COP | TRM indexada por día de mercado (insumo de la cruzada) | Misma fuente |
| `usd_ars_a3500` | USD-ARS | Dólar de referencia mayorista Com. A 3500 (**principal**) | BCRA Estadísticas Cambiarias `REF` |
| `usd_brl_ptax` | USD-BRL | PTAX de venta, cierre (**principal**) | Banco Central do Brasil, API PTAX (Olinda) |
| `mxn_cop_cross` | MXN-COP | Cruzada: TRM ÷ FIX del **mismo día de mercado** | Derivada |
| `mxn_ars_cross` | MXN-ARS | Cruzada: A 3500 ÷ FIX (ARS por 1 MXN) | Derivada |
| `ars_cop_cross` | ARS-COP | Cruzada: TRM ÷ A 3500 (COP por 1 ARS) | Derivada |
| `brl_mxn_cross` | BRL-MXN | Cruzada: FIX ÷ PTAX (MXN por 1 BRL) | Derivada |
| `brl_cop_cross` | BRL-COP | Cruzada: TRM ÷ PTAX (COP por 1 BRL) | Derivada |
| `brl_ars_cross` | BRL-ARS | Cruzada: A 3500 ÷ PTAX (ARS por 1 BRL) | Derivada |

**Por qué MXN-COP es derivada:** ni Banxico ni el Banco de la República publican
una tasa oficial MXN-COP. El propio aviso del FIX en el DOF indica que la
equivalencia con otras monedas se calcula a partir de sus cotizaciones contra el
dólar. Se cruzan dos tasas formadas el **mismo día de mercado**: el FIX
determinado el día D y la TRM calculada con el mercado del día D (la que entra en
vigencia el día siguiente). El pipeline rechaza cruzar series con distinta
base temporal.

## Principio: fuentes oficiales de los bancos centrales

Cada tasa se descarga de la institución que la define oficialmente:

| País | Tasa | Autoridad | Cómo se obtiene |
|---|---|---|---|
| México | FIX | Banco de México (la determina y publica) | API SIE de Banxico |
| Colombia | TRM | Banco de la República (define la TRM y su metodología; la Superintendencia Financiera la calcula y certifica a diario) | Servicio SDMX del BanRep, verificado en cada corrida contra la SFC en datos.gov.co |
| Argentina | Com. A 3500 | Banco Central de la República Argentina | API Estadísticas Cambiarias del BCRA |
| Brasil | PTAX | Banco Central do Brasil (la calcula y publica; Resolução BCB nº 45/2020) | API PTAX del BCB (Olinda, OData) |

Por qué importa: la fuente oficial define la metodología, el horario y las
correcciones; un agregador puede redondear, retrasarse o mezclar tasas
distintas bajo el mismo nombre. Cuando existe una segunda publicación oficial
(como la de la SFC en datos.gov.co), el pipeline la usa para **verificar** cada
dato y como **respaldo** declarado (`source.fallback_used: true`), nunca en
silencio.

## Archivos que consume el dashboard

| Archivo | Uso |
|---|---|
| `data/latest.json` | Tarjetas KPI: valor vigente, variación, estado, próximo valor ya publicado |
| `data/rates_daily.json` | **Todas las tasas**, una fila por día calendario (gráficos, tablas) |
| `data/series/<id>.json` | Histórico por serie con metadatos y fuente |
| `data/manifest.json` | Estado de la última corrida, errores, advertencias |

Contrato completo en [docs/CONTRATO_JSON.md](docs/CONTRATO_JSON.md). Conexión del
dashboard en [docs/DASHBOARD.md](docs/DASHBOARD.md).

## Puesta en marcha (una sola vez)

1. Solicita tu token gratuito del SIE en https://www.banxico.org.mx/SieAPIRest/service/v1/token
2. Crea el repo **público** `jaforero/fx-bancos-centrales` y sube este contenido.
3. En *Settings → Secrets and variables → Actions* crea el secreto `BANXICO_TOKEN`.
   Opcional: `DATOS_GOV_CO_APP_TOKEN` (app token de Socrata, mejora límites de uso).
4. En *Settings → Actions → General → Workflow permissions* elige **Read and write**.
5. Ejecuta manualmente *Actualización diaria de tasas* con `backfill_start = 2020-01-01`.
6. Verifica `data/manifest.json` → `overall_status: "ok"`.

## Operación

- `daily-update.yml`: corre lunes a viernes 19:30 UTC y todos los días 01:00 UTC.
  Solo hace commit si cambian los datos. Si una serie queda degradada, abre o
  actualiza un issue con etiqueta `fx-alerta`.
- `healthcheck.yml`: cada lunes prueba el contrato de cada API en vivo. Si detecta
  un cambio de formato, abre un issue `api-drift`.
- `ci.yml`: pruebas en cada push o PR.

Regla de diseño: **una fuente caída nunca borra datos buenos**. La serie conserva
su histórico, se marca `error:*` o `stale` y el dashboard puede mostrarlo.

## Local

```bash
pip install -e ".[dev]"
export BANXICO_TOKEN=...            # nunca lo subas al repo
PYTHONPATH=src python -m fxpipe update --dry-run
PYTHONPATH=src python -m fxpipe healthcheck
pytest -q
```

## Escalar a otros países

Ver [docs/AGREGAR_PAIS.md](docs/AGREGAR_PAIS.md).

## Atribución

Datos: Banco de México (SIE), Banco de la República de Colombia (SDMX; TRM
calculada y certificada por la Superintendencia Financiera) y Banco Central de
la República Argentina (API Estadísticas Cambiarias) y Banco Central do Brasil
(API PTAX, licencia ODbL). Las tasas cruzadas son cálculos propios, no tasas oficiales.
