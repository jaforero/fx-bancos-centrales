# Modelo semántico

`data/semantic_model.json` (y su equivalente `data/semantic_model.yaml`) describe **qué es cada dato**
de este repositorio: de dónde sale, cómo se calcula, cómo se relaciona con los demás, qué se puede
(y qué no) hacer con él y qué tan fresco está. Sirve para reutilizar la fuente como modelo de datos
en Power BI, Tableau, un catálogo de datos, una capa semántica o un agente de IA, sin tener que
leer el código ni deducir el significado de cada campo.

Es **neutral**: no depende de dbt, Power BI ni de ningún catálogo. Es **bilingüe** (español e inglés
en cada texto) y su estructura está fija en [`schema/semantic_model.schema.json`](../schema/semantic_model.schema.json).
Se regenera en cada corrida a partir de la configuración.

```
https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data/semantic_model.json
https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data/semantic_model.yaml
```

## De dónde sale

Cada corrida combina tres fuentes, de modo que nada se escribe dos veces:

| Fuente | Aporta | Quién la edita |
|---|---|---|
| `config/sources.json` | La estructura: proveedores, series, derivadas, umbrales de validación | Quien agrega un país o una serie |
| `config/semantic.json` | La curaduría bilingüe: significado, fuentes, reglas de uso, glosario, incidencias | Quien mantiene el modelo |
| La corrida | La cobertura real: primera y última fecha, observaciones, relleno y estado de cada serie | Nadie: es automática |

Las series calculadas (cruzadas) no se describen a mano: su nombre y descripción salen de plantillas
(`templates` en `config/semantic.json`) y de la configuración. Una cruzada nueva queda descrita sola.

**Dos versiones.** `schema_version` es la del contrato de datos (1.2.0 agregó estos archivos; los
consumidores aceptan `1.x`). `semantic_model_version` es la del contenido curado y sube cuando cambia
el significado de algo. Las incidencias y los cambios del modelo están en `known_issues` y `changelog`.

## En cifras

<!-- BEGIN:generado:counts -->
| Dimensión | Cantidad | Detalle |
|---|---|---|
| Series | 31 | 10 oficiales y 21 calculadas (cruzadas); 28 visibles en el dashboard |
| Pares | 28 | una serie canónica (primary) por par; las demás son variantes ocultas |
| Monedas | 8 | una de referencia y el resto locales |
| Países | 8 | el de la moneda de referencia y los de cada banco central |
| Proveedores | 8 | bancos centrales y fuentes de verificación |
<!-- END:generado:counts -->

## Cómo está organizado

| Sección | Qué responde |
|---|---|
| `model`, `scope`, `domain`, `conventions` | Qué es esta fuente, qué cubre y qué no, con qué cadencia se publica y qué convenciones rigen (par `BASE-COTIZADA`, fechas ISO, relleno, redondeo) |
| `dimensions` | Las entidades: `currencies`, `countries`, `providers`, `pairs` y `series` |
| `domains` | Los valores válidos de `status`, `date_basis`, `method`, `rate_type` y `series_kind`, con su significado y qué series los usan |
| `measures`, `metrics` | Qué se mide, en qué unidad, cómo se agrega (una tasa **no es aditiva**) y qué métricas derivadas existen |
| `relationships`, `hierarchies` | Cómo se conectan las entidades (`series:usd_mxn_fix → providers:banxico`) y cómo se navega: país → proveedor → serie, moneda → par → serie, día → año |
| `lineage` | Las etapas del pipeline y, por serie, de qué viene, por qué etapas pasa y en qué archivos se publica |
| `quality` | Las reglas de validación, su severidad, su umbral y las series a las que aplican |
| `files`, `fields` | Cada archivo publicado y el diccionario de **todos** los campos, con la ruta exacta donde vive cada uno |
| `glossary`, `events`, `usage_guidance` | Términos, eventos que el dashboard anota y reglas de uso |
| `consumers` | Quién lee qué (el dashboard, BI, el propio pipeline) y con qué salvaguardas |
| `known_issues`, `changelog` | Incidencias con su evidencia (incluida la del CLS) y el historial del modelo |
| `curation`, `coverage` | Si falta curaduría (`gaps`) y la cobertura de la última corrida: lo único que cambia cada día |

Las referencias entre entidades usan el formato `<entidad>:<id>`. Todo texto es un objeto `{"es": …, "en": …}`.

## Cómo reutilizarlo

**Python / pandas**

```python
import pandas as pd, requests
BASE = "https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data"
model = requests.get(f"{BASE}/semantic_model.json", timeout=30).json()
assert model["schema_version"].startswith("1.")

series = {s["id"]: s for s in model["dimensions"]["series"]}
print(series["usd_cop_trm"]["name"]["en"], "·", series["usd_cop_trm"]["quote_unit"])

# Tabla de dimensión para un BI: una fila por serie, que se une por id con las columnas de rates_daily.json
dim = pd.json_normalize(model["dimensions"]["series"])[["id", "kind", "pair", "primary", "date_basis", "rate_type"]]
```

**JavaScript**

```js
const BASE = "https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data";
const model = await fetch(`${BASE}/semantic_model.json`).then(r => r.json());
const serie = Object.fromEntries(model.dimensions.series.map(s => [s.id, s]));
console.log(serie.usd_mxn_fix.name.es, serie.usd_mxn_fix.thresholds.plausible_range);
```

**Un agente de IA o una capa semántica** puede cargar el YAML tal cual: trae, por serie, el significado, la
unidad, la fecha a la que se atribuye el valor (`date_basis`), la fuente oficial y qué no se debe hacer con
ella. Las reglas que más conviene respetar están en `usage_guidance` y `measures`: son tasas de referencia
(no de transacción), no se suman, solo se cruzan series del mismo `date_basis` y una cruzada es siempre
«calculada», nunca «oficial».

**Unir con los datos.** El `id` de cada serie es la clave: nombra la columna en `rates_daily.json`, la entrada
de `latest.json` y el archivo `series/<id>.json`. `fields` dice, campo por campo, en qué archivo y en qué ruta
vive cada cosa.

## Series

<!-- BEGIN:generado:series -->
| Serie | Tipo | Par | Visible | Fecha (date_basis) | Tipo de tasa | Origen |
|---|---|---|---|---|---|---|
| `usd_mxn_fix` | oficial | `USD-MXN` | sí | determinacion | fixing | banxico · `SF43718` |
| `usd_mxn_liq` | oficial | `USD-MXN` | no | vigencia | fixing_settlement_view | banxico · `SF60653` |
| `usd_cop_trm` | oficial | `USD-COP` | sí | vigencia | regulatory_reference | banrep · `DF_TRM_DAILY_HIST` |
| `usd_cop_trm_mkt` | oficial | `USD-COP` | no | determinacion | regulatory_reference | banrep · `DF_TRM_DAILY_HIST` |
| `usd_ars_a3500` | oficial | `USD-ARS` | sí | determinacion | wholesale_reference | bcra · `REF` |
| `usd_brl_ptax` | oficial | `USD-BRL` | sí | determinacion | official_close_average | bcb · `CotacaoDolarPeriodo` |
| `usd_clp_obs` | oficial | `USD-CLP` | sí | vigencia | weighted_average_spot | bcch · `F073.TCO.PRE.Z.D` |
| `usd_clp_obs_mkt` | oficial | `USD-CLP` | no | determinacion | weighted_average_spot | bcch · `F073.TCO.PRE.Z.D` |
| `usd_uyu_interbank` | oficial | `USD-UYU` | sí | determinacion | interbank_average | bcu · `2225` |
| `usd_pen_interbank` | oficial | `USD-PEN` | sí | determinacion | interbank_quote | bcrp · `PD04638PD` |
| `mxn_cop_cross` | calculada | `MXN-COP` | sí | determinacion | cross_rate | `usd_cop_trm_mkt` ÷ `usd_mxn_fix` |
| `mxn_ars_cross` | calculada | `MXN-ARS` | sí | determinacion | cross_rate | `usd_ars_a3500` ÷ `usd_mxn_fix` |
| `ars_cop_cross` | calculada | `ARS-COP` | sí | determinacion | cross_rate | `usd_cop_trm_mkt` ÷ `usd_ars_a3500` |
| `brl_mxn_cross` | calculada | `BRL-MXN` | sí | determinacion | cross_rate | `usd_mxn_fix` ÷ `usd_brl_ptax` |
| `brl_cop_cross` | calculada | `BRL-COP` | sí | determinacion | cross_rate | `usd_cop_trm_mkt` ÷ `usd_brl_ptax` |
| `brl_ars_cross` | calculada | `BRL-ARS` | sí | determinacion | cross_rate | `usd_ars_a3500` ÷ `usd_brl_ptax` |
| `mxn_clp_cross` | calculada | `MXN-CLP` | sí | determinacion | cross_rate | `usd_clp_obs_mkt` ÷ `usd_mxn_fix` |
| `clp_cop_cross` | calculada | `CLP-COP` | sí | determinacion | cross_rate | `usd_cop_trm_mkt` ÷ `usd_clp_obs_mkt` |
| `clp_ars_cross` | calculada | `CLP-ARS` | sí | determinacion | cross_rate | `usd_ars_a3500` ÷ `usd_clp_obs_mkt` |
| `brl_clp_cross` | calculada | `BRL-CLP` | sí | determinacion | cross_rate | `usd_clp_obs_mkt` ÷ `usd_brl_ptax` |
| `mxn_uyu_cross` | calculada | `MXN-UYU` | sí | determinacion | cross_rate | `usd_uyu_interbank` ÷ `usd_mxn_fix` |
| `uyu_cop_cross` | calculada | `UYU-COP` | sí | determinacion | cross_rate | `usd_cop_trm_mkt` ÷ `usd_uyu_interbank` |
| `uyu_ars_cross` | calculada | `UYU-ARS` | sí | determinacion | cross_rate | `usd_ars_a3500` ÷ `usd_uyu_interbank` |
| `brl_uyu_cross` | calculada | `BRL-UYU` | sí | determinacion | cross_rate | `usd_uyu_interbank` ÷ `usd_brl_ptax` |
| `uyu_clp_cross` | calculada | `UYU-CLP` | sí | determinacion | cross_rate | `usd_clp_obs_mkt` ÷ `usd_uyu_interbank` |
| `pen_mxn_cross` | calculada | `PEN-MXN` | sí | determinacion | cross_rate | `usd_mxn_fix` ÷ `usd_pen_interbank` |
| `pen_cop_cross` | calculada | `PEN-COP` | sí | determinacion | cross_rate | `usd_cop_trm_mkt` ÷ `usd_pen_interbank` |
| `pen_ars_cross` | calculada | `PEN-ARS` | sí | determinacion | cross_rate | `usd_ars_a3500` ÷ `usd_pen_interbank` |
| `pen_brl_cross` | calculada | `PEN-BRL` | sí | determinacion | cross_rate | `usd_brl_ptax` ÷ `usd_pen_interbank` |
| `pen_clp_cross` | calculada | `PEN-CLP` | sí | determinacion | cross_rate | `usd_clp_obs_mkt` ÷ `usd_pen_interbank` |
| `pen_uyu_cross` | calculada | `PEN-UYU` | sí | determinacion | cross_rate | `usd_uyu_interbank` ÷ `usd_pen_interbank` |
<!-- END:generado:series -->

Las series ocultas (`primary: false`) son variantes con una razón de ser: unas son la misma tasa reindexada por
el día de mercado para poder cruzarla (`market_day_variant_of`) y otra es el FIX para pagos (`settlement_view_of`).
Cada par tiene exactamente una serie visible.

## Pares

<!-- BEGIN:generado:pairs -->
| Par | Tipo | Serie canónica | Series del par |
|---|---|---|---|
| `USD-MXN` | oficial | `usd_mxn_fix` | `usd_mxn_fix`, `usd_mxn_liq` |
| `USD-COP` | oficial | `usd_cop_trm` | `usd_cop_trm`, `usd_cop_trm_mkt` |
| `USD-ARS` | oficial | `usd_ars_a3500` | `usd_ars_a3500` |
| `USD-BRL` | oficial | `usd_brl_ptax` | `usd_brl_ptax` |
| `USD-CLP` | oficial | `usd_clp_obs` | `usd_clp_obs`, `usd_clp_obs_mkt` |
| `USD-UYU` | oficial | `usd_uyu_interbank` | `usd_uyu_interbank` |
| `USD-PEN` | oficial | `usd_pen_interbank` | `usd_pen_interbank` |
| `MXN-COP` | cruzada | `mxn_cop_cross` | `mxn_cop_cross` |
| `MXN-ARS` | cruzada | `mxn_ars_cross` | `mxn_ars_cross` |
| `ARS-COP` | cruzada | `ars_cop_cross` | `ars_cop_cross` |
| `BRL-MXN` | cruzada | `brl_mxn_cross` | `brl_mxn_cross` |
| `BRL-COP` | cruzada | `brl_cop_cross` | `brl_cop_cross` |
| `BRL-ARS` | cruzada | `brl_ars_cross` | `brl_ars_cross` |
| `MXN-CLP` | cruzada | `mxn_clp_cross` | `mxn_clp_cross` |
| `CLP-COP` | cruzada | `clp_cop_cross` | `clp_cop_cross` |
| `CLP-ARS` | cruzada | `clp_ars_cross` | `clp_ars_cross` |
| `BRL-CLP` | cruzada | `brl_clp_cross` | `brl_clp_cross` |
| `MXN-UYU` | cruzada | `mxn_uyu_cross` | `mxn_uyu_cross` |
| `UYU-COP` | cruzada | `uyu_cop_cross` | `uyu_cop_cross` |
| `UYU-ARS` | cruzada | `uyu_ars_cross` | `uyu_ars_cross` |
| `BRL-UYU` | cruzada | `brl_uyu_cross` | `brl_uyu_cross` |
| `UYU-CLP` | cruzada | `uyu_clp_cross` | `uyu_clp_cross` |
| `PEN-MXN` | cruzada | `pen_mxn_cross` | `pen_mxn_cross` |
| `PEN-COP` | cruzada | `pen_cop_cross` | `pen_cop_cross` |
| `PEN-ARS` | cruzada | `pen_ars_cross` | `pen_ars_cross` |
| `PEN-BRL` | cruzada | `pen_brl_cross` | `pen_brl_cross` |
| `PEN-CLP` | cruzada | `pen_clp_cross` | `pen_clp_cross` |
| `PEN-UYU` | cruzada | `pen_uyu_cross` | `pen_uyu_cross` |
<!-- END:generado:pairs -->

## Monedas

Los códigos son ISO 4217. Los nombres siguen ese estándar y el vocabulario del dashboard; no salen del código
de este repositorio.

<!-- BEGIN:generado:currencies -->
| Código | Español | English | País | Rol |
|---|---|---|---|---|
| `USD` | Dólar estadounidense | US dollar | US | referencia |
| `MXN` | Peso mexicano | Mexican peso | MX | local |
| `COP` | Peso colombiano | Colombian peso | CO | local |
| `ARS` | Peso argentino | Argentine peso | AR | local |
| `BRL` | Real brasileño | Brazilian real | BR | local |
| `CLP` | Peso chileno | Chilean peso | CL | local |
| `UYU` | Peso uruguayo | Uruguayan peso | UY | local |
| `PEN` | Sol peruano | Peruvian sol | PE | local |
<!-- END:generado:currencies -->

## Proveedores

<!-- BEGIN:generado:providers -->
| Proveedor | Institución | País | Rol | Secreto | Protocolo |
|---|---|---|---|---|---|
| `banxico` | Banco de México | MX | primary | `BANXICO_TOKEN` (obligatorio) | API REST del SIE (JSON). Autenticación con el encabezado Bmx-Token (token gratuito del portal SIE). |
| `sfc_trm` | Superintendencia Financiera de Colombia (portal de datos abiertos datos.gov.co) | CO | verification_and_fallback | `DATOS_GOV_CO_APP_TOKEN` (opcional) | API Socrata de datos abiertos (datos.gov.co), conjunto 32sa-8pi3: TRM certificada por la Superintendencia Financiera. |
| `bcra` | Banco Central de la República Argentina | AR | primary | — | API Estadísticas Cambiarias v1.0 (REST JSON, paginada). Sin token; control de tráfico por IP. |
| `banrep` | Banco de la República (BanRep) | CO | primary | — | Servicio web SDMX 2.1 (XML GenericData), dataflow DF_TRM_DAILY_HIST. Sin token. |
| `bcb` | Banco Central do Brasil (BCB) | BR | primary | — | API PTAX (Olinda, OData) en JSON. Sin token. |
| `bcch` | Banco Central de Chile (BCCh) | CL | primary | `BCCH_TOKEN` (obligatorio) | API REST de la Base de Datos Estadísticos (BDE). Requiere cuenta y API Key Token (vence al año), en el secreto BCCH_TOKEN. |
| `bcu` | Banco Central del Uruguay (BCU) | UY | primary | — | Web service SOAP 1.1 de cotizaciones (POST, document/literal), especificación v3. Sin token; grupo 2 = cotizaciones locales. |
| `bcrp` | Banco Central de Reserva del Perú (BCRP) | PE | primary | — | API de series estadísticas (BCRPData, REST). Sin token. La respuesta es JSON pero se sirve como text/html. |
<!-- END:generado:providers -->

El modelo incluye por proveedor su protocolo, cuándo publica, sus condiciones de reutilización (cuando la
fuente las declara) y sus particularidades (`quirks`), cada una con el archivo del repositorio que las respalda.

## Medidas y métricas

<!-- BEGIN:generado:measures -->
| Medida | Campo | Unidad | Aditividad | Agregación en el tiempo |
|---|---|---|---|---|
| Valor de la tasa | `value` | series.quote_unit | non_additive | last, mean, min, max (nunca: sum) |
| Variación absoluta | `change_abs` | series.quote_unit | non_additive | — |
| Variación porcentual | `change_pct` | percent | non_additive | — |
| Días desde la última publicación oficial | `days_since_official` | days | non_additive | — |
| Marca de relleno | `filled` | boolean | count_only | — |
<!-- END:generado:measures -->

<!-- BEGIN:generado:metrics -->
| Métrica | Definición | Se calcula en |
|---|---|---|
| Último valor oficial | Valor de la observación oficial (no rellenada) más reciente con fecha ≤ as_of, con su fecha. | `fxpipe.transform.snapshot` |
| Variación frente al registro oficial anterior | Compara el último valor oficial con el registro anterior distinto: una racha de días consecutivos con el mismo valor (p. ej. una TRM vigente de viernes a lunes) cuenta como un solo registro. | `fxpipe.transform.snapshot` |
| Próximo valor ya publicado | Primer valor oficial con fecha posterior a as_of que no sea la continuación de la racha actual (p. ej. la TRM del lunes publicada el viernes). | `fxpipe.transform.snapshot` |
| Tasa cruzada vía USD | (Cotizada por USD) ÷ (Base por USD) del mismo día de mercado, redondeada a decimals con round_js. Es un cálculo, no una tasa oficial. | `fxpipe.transform.cross_rate` |
| Estado de frescura | stale si el último valor oficial tiene más de stale_after_days de antigüedad respecto de as_of; error:empty si no hay ninguno. | `fxpipe.pipeline.run` |
| Variación en N días | Variación porcentual entre el último valor oficial ≤ as_of y el último oficial ≤ (as_of − N días); N = 30 aproxima un mes y 365 un año. | `jaforero/dashboards:tasas-cambio/fx-data.js::changeOver` (consumidor) |
| Variación en lo que va del año | Variación porcentual entre el último valor oficial del año anterior y el último valor oficial ≤ as_of. | `jaforero/dashboards:tasas-cambio/fx-data.js::ytdChange` (consumidor) |
| Índice base 100 | Cada punto dividido entre el primer valor oficial del rango y multiplicado por 100; permite comparar monedas de escalas muy distintas. | `jaforero/dashboards:tasas-cambio/fx-data.js::rebase` (consumidor) |
| Conversión de un monto | Multiplica por el valor de la serie primary cuyo par tiene la base y la cotizada pedidas, o divide si el par está invertido; indica la fecha de la tasa y si es oficial o calculada. | `jaforero/dashboards:tasas-cambio/fx-data.js::convert` (consumidor) |
<!-- END:generado:metrics -->

Las métricas marcadas «(consumidor)» las calcula el dashboard, no este repositorio; el modelo las declara con
`verifiable_in_repo: false` porque las pruebas de aquí no pueden comprobarlas.

## Calidad

<!-- BEGIN:generado:quality -->
| Regla | Severidad | Consecuencia | Umbral | Qué verifica |
|---|---|---|---|---|
| `plausible_range` | error | status = error:validation | `plausible_range` | Todo valor nuevo debe estar dentro de plausible_range de su serie. |
| `daily_jump` | warning | manifest.warnings: «salto …» | `max_daily_change_pct` | Entre dos observaciones oficiales consecutivas la variación no debe superar max_daily_change_pct. |
| `fill_limit` | info | gap (no value) after max_fill_days | `max_fill_days` | Un día sin publicación se rellena como máximo max_fill_days; después queda un hueco. |
| `freshness` | warning | status = stale | `stale_after_days` | El último valor oficial no debe tener más de stale_after_days de antigüedad. |
| `non_empty` | error | status = error:empty | — | La serie debe tener al menos una observación oficial. |
| `source_contract` | error | status = error:contract | — | La respuesta de la fuente debe tener la forma esperada; si no, es un posible cambio de API (ContractError). |
| `source_availability` | error | status = error:provider (history kept) | — | La fuente debe responder (red, HTTP, credenciales); un fallo no borra el histórico. |
| `revision_watch` | warning | manifest.warnings: «revisión …» | — | Un valor ya publicado que cambia en la fuente se registra como advertencia. |
| `cross_verification` | warning | manifest.warnings: «discrepancia con …» | — | Cuando una serie declara fallback, sus valores se comparan con la segunda fuente oficial. |
| `same_date_basis` | error | ConfigError when loading the configuration | — | Numerador, denominador y cruzada deben tener el mismo date_basis. |
| `one_primary_per_pair` | error | semantic curation test | — | Cada par tiene exactamente una serie primary (la que muestra el dashboard); las demás son variantes ocultas. |
| `client_cross_guard` | error | the dashboard discards the compact file and loads rates_daily.json | — | El último valor oficial de cada cruzada recalculada en el navegador debe coincidir con latest.json. |
| `no_real_waits_in_tests` | error | test failure | — | Ninguna prueba espera ni sale a la red de verdad (tests/conftest.py). |
<!-- END:generado:quality -->

## Archivos publicados

<!-- BEGIN:generado:files -->
| Archivo | Rol | Esquema | Desde | Qué contiene |
|---|---|---|---|---|
| `latest.json` | kpi_snapshot | `schema/latest.schema.json` | 1.0.0 | Valor oficial vigente, variación frente al registro anterior, valor siguiente ya publicado y estado de cada serie. Alimenta las tarjetas del dashboard y la guarda de las cruzadas recalculadas. |
| `rates_daily.json` | full_history_wide | `schema/rates_daily.schema.json` | 1.0.0 | Todas las tasas (oficiales y cruzadas), con la lista de series rellenadas de cada día. Es el archivo para Power BI, Python y cualquier otro consumidor, y el respaldo del dashboard. |
| `rates_daily_compact.json` | browser_history_columnar | `schema/rates_daily_compact.schema.json` | 1.1.0 | Lo mismo que rates_daily.json para el navegador, sin las cruzadas: solo viajan las series oficiales visibles y los insumos de las cruzadas, en columnas; el cliente recalcula las cruzadas con series.&lt;id&gt;.inputs y decimals. Crece con el número de países, no con el de cruzadas. |
| `series/<id>.json` | per_series_history_and_persistent_store | — | 1.0.0 | Histórico completo de una serie con sus metadatos. Es además el almacén persistente del propio pipeline: cada corrida lee estos archivos (store.load_official, solo los valores oficiales) para saber desde dónde pedir datos y conservar el histórico si una fuente falla; por eso no son una copia prescindible. |
| `manifest.json` | run_state | — | 1.0.0 | Estado de la última corrida: estado de cada serie, errores, advertencias, hash de los datos y lista de archivos. Es lo primero que se lee al diagnosticar y lo que usa fxpipe check. |
| `semantic_model.json` | semantic_model | `schema/semantic_model.schema.json` | 1.2.0 | Este modelo semántico en JSON. Se regenera en cada corrida desde la configuración, la curaduría y la cobertura real. |
| `semantic_model.yaml` | semantic_model | `schema/semantic_model.schema.json` | 1.2.0 | El mismo modelo en YAML neutral, equivalente campo por campo al JSON (una prueba lo verifica). |
<!-- END:generado:files -->

## Diccionario de campos

<!-- BEGIN:generado:fields -->
| Campo | Ubicaciones | Tipo | Descripción |
|---|---|---|---|
| `schema_version` | `latest.json#schema_version`<br>`rates_daily.json#schema_version`<br>(+3 más) | string | Versión del contrato de datos (MAYOR.MENOR.PARCHE). Los consumidores aceptan 1.x. |
| `as_of` | `latest.json#as_of`<br>`rates_daily.json#as_of`<br>(+2 más) | string (date) | Fecha de referencia de la corrida: hoy en la zona America/Bogota (o la fecha pedida con --as-of). |
| `overall_status` | `latest.json#overall_status`<br>`manifest.json#overall_status` | string (enum) | Estado general: ok o degraded. En latest.json solo cuentan las series primary; en manifest.json, todas. |
| `series_map` | `latest.json#series`<br>`rates_daily.json#series`<br>(+1 más) | object | Mapa id de serie → metadatos (y, en latest.json, el último estado). En el archivo compacto solo trae las series visibles y las columnas. |
| `series_entry` | `latest.json#series.*`<br>`rates_daily.json#series.*`<br>(+1 más) | object | Una serie: sus metadatos (y en latest.json su estado). |
| `series_id` | `series/<id>.json#id` | string | Id estable de la serie (usd_mxn_fix). |
| `pair` | `latest.json#series.*.pair`<br>`rates_daily.json#series.*.pair`<br>(+2 más) | string | Par BASE-COTIZADA (USD-MXN). |
| `label` | `latest.json#series.*.label`<br>`rates_daily.json#series.*.label`<br>(+2 más) | string | Nombre largo de la serie en español, tal como lo declara la configuración. |
| `quote_unit` | `latest.json#series.*.quote_unit`<br>`rates_daily.json#series.*.quote_unit`<br>(+2 más) | string | Unidad de la cotización en texto («COP por 1 USD»). |
| `date_basis` | `latest.json#series.*.date_basis`<br>`rates_daily.json#series.*.date_basis`<br>(+2 más) | string (enum) | A qué fecha se atribuye el valor: determinacion o vigencia. |
| `primary` | `latest.json#series.*.primary`<br>`rates_daily.json#series.*.primary`<br>(+2 más) | boolean | true si el dashboard muestra la serie (una por par). |
| `decimals` | `latest.json#series.*.decimals`<br>`rates_daily.json#series.*.decimals`<br>(+2 más) | integer | Decimales con que se muestra la serie y con que se redondean las cruzadas. |
| `method` | `latest.json#series.*.method`<br>`rates_daily.json#series.*.method`<br>(+2 más) | string | official, o «cross_via_usd: numerador / denominador» en una cruzada. |
| `inputs` | `latest.json#series.*.inputs`<br>`rates_daily.json#series.*.inputs`<br>(+2 más) | array&lt;string&gt; | Solo en cruzadas: [id del numerador, id del denominador]. |
| `source` | `latest.json#series.*.source`<br>`rates_daily.json#series.*.source`<br>(+2 más) | object | Solo en series oficiales: de dónde sale el dato. |
| `source_provider` | `latest.json#series.*.source.provider`<br>`rates_daily.json#series.*.source.provider`<br>(+2 más) | string | Id del proveedor en config/sources.json. |
| `source_institution` | `latest.json#series.*.source.institution`<br>`rates_daily.json#series.*.source.institution`<br>(+2 más) | string | Institución que publica. |
| `source_country` | `latest.json#series.*.source.country`<br>`rates_daily.json#series.*.source.country`<br>(+2 más) | string | País de la institución (ISO 3166-1 alfa-2). |
| `source_series_id` | `latest.json#series.*.source.series_id`<br>`rates_daily.json#series.*.source.series_id`<br>(+2 más) | string | Código de la serie en la fuente. |
| `source_docs_url` | `latest.json#series.*.source.docs_url`<br>`rates_daily.json#series.*.source.docs_url`<br>(+2 más) | string (uri) | Documentación oficial de la fuente. |
| `source_authority` | `latest.json#series.*.source.authority`<br>`rates_daily.json#series.*.source.authority`<br>(+2 más) | string | Autoridad que define o calcula la tasa, cuando la cadena institucional no es obvia. |
| `source_certified_by` | `latest.json#series.*.source.certified_by`<br>`rates_daily.json#series.*.source.certified_by`<br>(+2 más) | string | Entidad que certifica la tasa (la SFC para la TRM). |
| `source_legal_basis` | `latest.json#series.*.source.legal_basis`<br>`rates_daily.json#series.*.source.legal_basis`<br>(+2 más) | string | Norma que respalda la tasa o su metodología. |
| `source_verified_against` | `latest.json#series.*.source.verified_against`<br>`rates_daily.json#series.*.source.verified_against`<br>(+2 más) | object | Segunda fuente oficial con que se verifica cada corrida y que sirve de respaldo. |
| `verified_provider` | `latest.json#series.*.source.verified_against.provider`<br>`rates_daily.json#series.*.source.verified_against.provider`<br>(+2 más) | string | Proveedor de la fuente de verificación. |
| `verified_institution` | `latest.json#series.*.source.verified_against.institution`<br>`rates_daily.json#series.*.source.verified_against.institution`<br>(+2 más) | string | Institución de la fuente de verificación. |
| `verified_series_id` | `latest.json#series.*.source.verified_against.series_id`<br>`rates_daily.json#series.*.source.verified_against.series_id`<br>(+2 más) | string | Código de la serie en la fuente de verificación. |
| `verified_docs_url` | `latest.json#series.*.source.verified_against.docs_url`<br>`rates_daily.json#series.*.source.verified_against.docs_url`<br>(+2 más) | string (uri) | Documentación de la fuente de verificación. |
| `fallback_used` | `latest.json#series.*.source.fallback_used`<br>`rates_daily.json#series.*.source.fallback_used`<br>(+2 más) | boolean | true si en la última corrida el dato vino del respaldo porque la fuente principal falló. |
| `value` | `latest.json#series.*.value` | number \| null | Último valor oficial con fecha ≤ as_of (measures.rate_value). |
| `date` | `latest.json#series.*.date` | string (date) \| null | Fecha del último valor oficial. |
| `days_since_official` | `latest.json#series.*.days_since_official` | integer | as_of menos date, en días. |
| `prev_value` | `latest.json#series.*.prev_value` | number | Valor del registro oficial anterior (no el de la misma racha de vigencia). |
| `prev_date` | `latest.json#series.*.prev_date` | string (date) | Fecha del registro oficial anterior. |
| `change_abs` | `latest.json#series.*.change_abs` | number | value − prev_value, redondeado a 6 decimales. |
| `change_pct` | `latest.json#series.*.change_pct` | number \| null | Variación porcentual frente a prev_value, redondeada a 4 decimales. |
| `next` | `latest.json#series.*.next` | object | Próximo valor oficial ya publicado (p. ej. la TRM del lunes publicada el viernes). |
| `next_date` | `latest.json#series.*.next.date` | string (date) | Fecha del próximo valor ya publicado. |
| `next_value` | `latest.json#series.*.next.value` | number | Próximo valor ya publicado. |
| `status` | `latest.json#series.*.status` | string (enum) | ok, stale o error:* (provider, contract, validation, empty). |
| `error` | `latest.json#series.*.error` | string | Mensaje del error; solo si status es error:*. |
| `rows` | `rates_daily.json#rows[]` | array&lt;object&gt; | Una fila por día calendario. |
| `row_date` | `rates_daily.json#rows[].date` | string (date) | Día calendario de la fila. |
| `row_value` | `rates_daily.json#rows[].*` | number \| null | Valor de la serie ese día (oficial o relleno); null si no hay dato. Una propiedad por id de serie. |
| `row_filled` | `rates_daily.json#rows[].filled` | array&lt;string&gt; | Ids de las series cuyo valor ese día es relleno; se omite si está vacía. |
| `layout` | `rates_daily_compact.json#layout` | string (const) | Siempre «columnar». |
| `dates` | `rates_daily_compact.json#dates` | array&lt;string (date)&gt; | Unión ordenada de las fechas de las columnas. |
| `columns` | `rates_daily_compact.json#columns` | object | Mapa id de serie oficial → columna. |
| `column` | `rates_daily_compact.json#columns.*` | object | Una columna alineada con dates. |
| `column_values` | `rates_daily_compact.json#columns.*.values` | array&lt;number \| null&gt; | values[i] es el valor de dates[i] (oficial o relleno), o null. |
| `column_filled` | `rates_daily_compact.json#columns.*.filled` | array&lt;integer&gt; | Índices de dates cuyo valor es relleno. |
| `first_date` | `series/<id>.json#first_date` | string (date) \| null | Primera fecha de la serie (oficial o relleno). |
| `last_date` | `series/<id>.json#last_date` | string (date) \| null | Última fecha de la serie (oficial o relleno). |
| `last_official_date` | `series/<id>.json#last_official_date` | string (date) \| null | Fecha del último valor oficial. |
| `observations` | `series/<id>.json#observations[]` | array&lt;object&gt; | Observaciones diarias, una por línea en el archivo. |
| `obs_date` | `series/<id>.json#observations[].date` | string (date) | Día calendario de la observación. |
| `obs_value` | `series/<id>.json#observations[].value` | number | Valor oficial o rellenado. |
| `obs_filled` | `series/<id>.json#observations[].filled` | boolean | true si el valor repite el último oficial. |
| `config_version` | `manifest.json#config_version` | string | Versión de config/sources.json que produjo la corrida. |
| `generated_at` | `manifest.json#generated_at` | string (date-time, UTC) | Momento en que se generó el manifest. |
| `data_hash` | `manifest.json#data_hash` | string (sha-256) | Hash de todos los archivos de datos generados; si no cambia, no se escribe nada. |
| `series_status` | `manifest.json#series_status`<br>`manifest.json#series_status.*` | object&lt;string&gt; | Estado de cada serie (todas, también las no primary). |
| `errors` | `manifest.json#errors`<br>`manifest.json#errors.*` | object&lt;string&gt; | Mensaje de error por serie degradada. |
| `warnings` | `manifest.json#warnings`<br>`manifest.json#warnings.*` | object&lt;array&lt;string&gt;&gt; | Hasta las últimas 20 advertencias por serie (saltos, revisiones, discrepancias). |
| `files` | `manifest.json#files` | array&lt;string&gt; | Archivos generados por la corrida, relativos a data/. |
<!-- END:generado:fields -->

Una prueba compara este diccionario con los archivos que genera una corrida: no puede haber un campo publicado
sin documentar ni un campo documentado que no exista.

## Glosario

<!-- BEGIN:generado:glossary -->
| Término | Definición |
|---|---|
| FIX | Tipo de cambio que determina y publica Banco de México; la serie por fecha de determinación es usd_mxn_fix. |
| TRM | Tasa Representativa del Mercado de Colombia: la define el Banco de la República y la calcula y certifica la Superintendencia Financiera. |
| PTAX | Tasa de cambio del dólar de Brasil: promedio simple de cuatro consultas diarias a dealers acreditados que calcula y publica el Banco Central do Brasil. |
| A 3500 | Comunicación A 3500 del BCRA: dólar de referencia mayorista de Argentina, en pesos por 1 USD (código REF). |
| dólar observado | Tipo de cambio de Chile: promedio ponderado de las operaciones spot USD/CLP del Mercado Cambiario Formal del día hábil bancario anterior. |
| interbancario | Cotización del dólar entre bancos en el mercado local: en Uruguay, el promedio de las operaciones del día en BEVSA; en Perú, el valor diario de venta publicado por el BCRP. |
| fecha de determinación | Fecha del mercado con que se formó la tasa (date_basis = determinacion). |
| fecha de vigencia | Fecha en que la tasa rige (date_basis = vigencia); la TRM y el dólar observado vigentes el día D se calculan con el mercado del día hábil anterior. |
| día de mercado | Día en que se negociaron las operaciones con que se forma la tasa; es la base común para cruzar series. |
| tasa cruzada | Tasa calculada vía USD como cociente de dos tasas oficiales del mismo día de mercado; no es oficial. |
| relleno (filled) | Valor de un día sin publicación que repite el último oficial, hasta max_fill_days. |
| serie primary | Serie que muestra el dashboard (una por par). Las demás son insumos o variantes ocultas. |
| insumo | Serie oficial que entra como numerador o denominador de una cruzada. |
| variante por día de mercado (*_mkt) | El mismo dato oficial reindexado por el día de mercado para poder cruzarlo (usd_cop_trm_mkt, usd_clp_obs_mkt). |
| as_of | Fecha de referencia de la corrida (hoy en America/Bogota). |
| round_js | Redondeo equivalente a Number(x.toFixed(d)) de JavaScript, para que servidor y navegador den el mismo número. |
| BEVSA | Sigla que el repositorio cita como el lugar donde se promedian las operaciones interbancarias del día que publica el BCU (y como fuente de contraste del valor). El repositorio no desarrolla la sigla. |
| CLS | Cumulative Layout Shift: métrica de Core Web Vitals que mide cuánto se mueve el contenido de una página sin que el usuario lo haya provocado; es la mayor ráfaga de cambios de diseño. Bueno ≤ 0,1; deficiente &gt; 0,25 (percentil 75 de las cargas). |
<!-- END:generado:glossary -->

## Incidencias conocidas

<!-- BEGIN:generado:known_issues -->
| Incidencia | Estado | Resuelta | Resumen |
|---|---|---|---|
| `cls_en_us_0_022` | resolved | 2026-10-17 | El dashboard movía contenido cuando llegaba la tipografía de marca; con la corrección, el desplazamiento de diseño medido en laboratorio es 0,000 en el perfil móvil estándar y la página publicada marca 0 en una medición en vivo. |
<!-- END:generado:known_issues -->

**CLS de 0,022 en en-US (dashboard, resuelta el 2026-10-17).** La causa fue el cambio de tipografía: Igra Sans
llega después del primer pintado y es más ancha que la fuente de respaldo, así que las filas que se envuelven
al borde del ancho disponible se reacomodaban. Se corrigió en `jaforero/dashboards@0a25d85` con precarga de la
fuente, fuentes de respaldo con las métricas de Igra Sans y una disposición determinista. En laboratorio
(Chromium con Playwright, CPU ×4, 4G, fuente retrasada 1,5 s) el CLS móvil en en-US pasó de 0,022 a 0,000 y la
barrida de 194 casos pasó de una suma de 8,640 a 0,261; la página publicada marca 0 en una medición en vivo.
El detalle completo (método, cifras, riesgos residuales) está en `known_issues` del modelo. **Límites:** son
mediciones de laboratorio, no de usuarios reales; no se probó en Safari ni en Firefox; la medición en vivo
tenía la fuente en caché; y no hay todavía datos de campo (CrUX o monitoreo propio), por lo que
`field_validation.status` sigue en `pending`. Los umbrales de referencia (bueno ≤ 0,1; deficiente > 0,25, en
el percentil 75) son los de [web.dev/articles/cls](https://web.dev/articles/cls), consultados el 2026-10-17.

## Cómo se mantiene

1. Al agregar un país o una serie, edita `config/sources.json` como siempre ([AGREGAR_PAIS.md](AGREGAR_PAIS.md)).
2. Corre `python -m fxpipe semantic`: lista lo que falta curar (serie, proveedor, moneda, país) y falla si algo
   no está en español **y** en inglés. La CI hace la misma comprobación, así que no se puede publicar un país sin
   su descripción.
3. Agrega la curaduría a `config/semantic.json` (las series oficiales y el proveedor; las cruzadas salen solas).
4. Corre `python -m fxpipe semantic --write-docs` para regenerar las tablas de este documento y `pytest -q`.
5. Sube `semantic_version` y agrega una entrada a `changelog` si cambió el significado de algo.

En la corrida diaria el generador es **indulgente**: si falta curaduría publica igual los datos y declara el
hueco en `curation.gaps`; si el generador falla, los datos se publican sin él y el error queda en el registro
de la corrida. Un metadato nunca impide publicar un dato.

## Qué no cubre

- Los nombres de las monedas (ISO 4217 y vocabulario del dashboard) y los eventos que anota el dashboard no
  salen de este repositorio. El dashboard conserva su propia copia: no hay sincronización automática.
- La sigla BEVSA se cita como aparece en la documentación de la fuente; el repositorio no la desarrolla y el
  modelo tampoco.
- No es un catálogo de precios ni de tasas de transacción: son tasas de referencia oficiales.
