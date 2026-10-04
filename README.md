# fx-bancos-centrales

Tasas de cambio diarias de bancos centrales, publicadas como JSON versionado en
este repositorio para alimentar dashboards. Se actualiza solo con GitHub Actions.

| id | Par | Qué es | Fuente |
|---|---|---|---|
| `usd_mxn_fix` | USD-MXN | FIX, fecha de determinación (**principal**) | Banxico SIE `SF43718` |
| `usd_mxn_liq` | USD-MXN | FIX, fecha de liquidación (insumo de la cruzada) | Banxico SIE `SF60653` |
| `usd_cop_trm` | USD-COP | TRM vigente | SFC vía datos.gov.co `32sa-8pi3` |
| `mxn_cop_cross` | MXN-COP | Cruzada: TRM ÷ FIX liquidación | Derivada |

**Por qué MXN-COP es derivada:** ni Banxico ni el Banco de la República publican
una tasa oficial MXN-COP. El propio aviso del FIX en el DOF indica que la
equivalencia con otras monedas se calcula a partir de sus cotizaciones contra el
dólar. Se cruzan dos tasas con la misma base temporal ("vigente en la fecha D"):
TRM vigente y FIX en fecha de liquidación.

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

Datos: Banco de México (SIE) y Superintendencia Financiera de Colombia
(datos.gov.co). Las tasas cruzadas son cálculos propios, no tasas oficiales.
