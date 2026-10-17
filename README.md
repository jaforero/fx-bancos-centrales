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
| `usd_clp_obs` | USD-CLP | Dólar observado, fecha de vigencia | Banco Central de Chile, API BDE `F073.TCO.PRE.Z.D` |
| `usd_clp_obs_mkt` | USD-CLP | Dólar observado indexado por día de mercado (insumo de las cruzadas) | Misma fuente |
| `usd_uyu_interbank` | USD-UYU | Dólar interbancario billete, promedio del día | Banco Central del Uruguay, web service de cotizaciones (código `2225`) |
| `usd_pen_interbank` | USD-PEN | Dólar interbancario venta, valor del día | Banco Central de Reserva del Perú, BCRPData `PD04638PD` |
| `mxn_cop_cross` | MXN-COP | Cruzada: TRM ÷ FIX del **mismo día de mercado** | Derivada |
| `mxn_ars_cross` | MXN-ARS | Cruzada: A 3500 ÷ FIX (ARS por 1 MXN) | Derivada |
| `ars_cop_cross` | ARS-COP | Cruzada: TRM ÷ A 3500 (COP por 1 ARS) | Derivada |
| `brl_mxn_cross` | BRL-MXN | Cruzada: FIX ÷ PTAX (MXN por 1 BRL) | Derivada |
| `brl_cop_cross` | BRL-COP | Cruzada: TRM ÷ PTAX (COP por 1 BRL) | Derivada |
| `brl_ars_cross` | BRL-ARS | Cruzada: A 3500 ÷ PTAX (ARS por 1 BRL) | Derivada |
| `mxn_clp_cross` | MXN-CLP | Cruzada: dólar observado ÷ FIX (CLP por 1 MXN) | Derivada |
| `clp_cop_cross` | CLP-COP | Cruzada: TRM ÷ dólar observado (COP por 1 CLP) | Derivada |
| `clp_ars_cross` | CLP-ARS | Cruzada: A 3500 ÷ dólar observado (ARS por 1 CLP) | Derivada |
| `brl_clp_cross` | BRL-CLP | Cruzada: dólar observado ÷ PTAX (CLP por 1 BRL) | Derivada |
| `mxn_uyu_cross` | MXN-UYU | Cruzada: interbancario ÷ FIX (UYU por 1 MXN) | Derivada |
| `uyu_cop_cross` | UYU-COP | Cruzada: TRM ÷ interbancario (COP por 1 UYU) | Derivada |
| `uyu_ars_cross` | UYU-ARS | Cruzada: A 3500 ÷ interbancario (ARS por 1 UYU) | Derivada |
| `brl_uyu_cross` | BRL-UYU | Cruzada: interbancario ÷ PTAX (UYU por 1 BRL) | Derivada |
| `uyu_clp_cross` | UYU-CLP | Cruzada: dólar observado ÷ interbancario (CLP por 1 UYU) | Derivada |
| `pen_mxn_cross` | PEN-MXN | Cruzada: FIX ÷ interbancario de Perú (MXN por 1 PEN) | Derivada |
| `pen_cop_cross` | PEN-COP | Cruzada: TRM ÷ interbancario de Perú (COP por 1 PEN) | Derivada |
| `pen_ars_cross` | PEN-ARS | Cruzada: A 3500 ÷ interbancario de Perú (ARS por 1 PEN) | Derivada |
| `pen_brl_cross` | PEN-BRL | Cruzada: PTAX ÷ interbancario de Perú (BRL por 1 PEN) | Derivada |
| `pen_clp_cross` | PEN-CLP | Cruzada: dólar observado ÷ interbancario de Perú (CLP por 1 PEN) | Derivada |
| `pen_uyu_cross` | PEN-UYU | Cruzada: interbancario de Uruguay ÷ interbancario de Perú (UYU por 1 PEN) | Derivada |

Perú entró igual que Uruguay: primero los datos con `primary: false` (config 1.9.0) y,
con el dashboard adaptado, `primary: true` en el interbancario y sus seis cruzadas.
Su fuente (BCRPData) está detrás de un firewall anti-bots que a veces responde con una
página HTML en lugar de JSON; el conector la reintenta y, si persiste, la reporta como
error operativo (ver `src/fxpipe/providers/bcrp_series.py`).

Uruguay entró igual que Chile: primero los datos con `primary: false` (config 1.7.0) y,
con el dashboard adaptado, `primary: true` en el interbancario y sus cinco cruzadas
(config 1.8.0).

Chile entró en dos fases: primero los datos con `primary: false` (config 1.5.x) y,
con el dashboard ya adaptado, `primary: true` en el dólar observado y sus cuatro
cruzadas (config 1.6.0). `usd_clp_obs_mkt`, como `usd_cop_trm_mkt`, es un insumo y
sigue oculto.

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
| Chile | Dólar observado | Banco Central de Chile (lo calcula y publica; Compendio de Normas de Cambios Internacionales, Cap. I, sección VII) | API REST de la Base de Datos Estadísticos (BDE), con token |
| Uruguay | Interbancario | Banco Central del Uruguay (publica el promedio de las operaciones interbancarias del día en BEVSA) | Web service SOAP de cotizaciones del BCU, sin token |
| Perú | Interbancario venta | Banco Central de Reserva del Perú (publica el tipo de cambio interbancario en BCRPData) | API de series estadísticas del BCRP, sin token |

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
   Crea también `BCCH_TOKEN` (API Key Token de la BDE del Banco Central de Chile; ver abajo).
   Opcional: `DATOS_GOV_CO_APP_TOKEN` (app token de Socrata, mejora límites de uso).
4. En *Settings → Actions → General → Workflow permissions* elige **Read and write**.
5. Ejecuta manualmente *Actualización diaria de tasas* con `backfill_start = 2020-01-01`.
6. Verifica `data/manifest.json` → `overall_status: "ok"`.

### Token del Banco Central de Chile (`BCCH_TOKEN`)

1. Sigue la guía oficial de acceso: https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/acceso_api.html
   (si no tienes cuenta en la BDE, regístrate desde la página de inicio de la API).
2. Inicia sesión, acepta los términos de uso y pulsa **Activar el uso de la API** (una sola vez).
3. En **Mi Cuenta → Apikey Token** copia el token y guárdalo como secreto `BCCH_TOKEN`.
4. El token **vence al año**. Se renueva o se extiende en la misma sección; si vence, la
   corrida marca las series de Chile `error:provider` con un mensaje que lo indica
   (la API responde `Codigo=-5`, «Invalid username or password»).

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
export BCCH_TOKEN=...
PYTHONPATH=src python -m fxpipe update --dry-run
PYTHONPATH=src python -m fxpipe healthcheck
pytest -q
```

## Escalar a otros países

Ver [docs/AGREGAR_PAIS.md](docs/AGREGAR_PAIS.md).

## Atribución

Datos: Banco de México (SIE), Banco de la República de Colombia (SDMX; TRM
calculada y certificada por la Superintendencia Financiera) y Banco Central de
la República Argentina (API Estadísticas Cambiarias), Banco Central do Brasil
(API PTAX, licencia ODbL), Banco Central de Chile (Base de Datos Estadísticos) y
Banco Central del Uruguay (web service de cotizaciones) y Banco Central de Reserva del Perú
(BCRPData; la información puede reproducirse citando la fuente).
Las tasas cruzadas son cálculos propios, no tasas oficiales.
