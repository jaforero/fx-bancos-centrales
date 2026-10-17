# Cambios

## operación — 2026-10-17
- `daily-update.yml` cierra solo las alertas `fx-alerta` abiertas cuando una corrida termina con todas las
  series en ok, con un comentario que enlaza esa corrida. El paso nuevo no puede volver roja una corrida sana
  (`continue-on-error`). Antes la alerta solo se abría o se comentaba y quedaba abierta hasta cerrarla a mano.
- Se cerró así el issue #1 «Pipeline de tasas degradado», abierto el 2026-10-12 por las corridas #19 y #20 del
  despliegue de Chile: en la #19 el BCCh respondió en una codificación que el conector no leía (`error:contract`,
  «Respuesta no es UTF-8») y en la #20, con el arreglo ya publicado, `usd_clp_obs_mkt` y sus cuatro cruzadas
  quedaron en `error:empty`. La corrida siguiente, sin cambios de código entre medias, salió en ok, y las 17
  corridas diarias posteriores también.
- Modelo semántico 1.0.1: la alerta `fx-alerta` describe el cierre automático. Sin cambios en los datos ni en
  el esquema 1.2.0.

## esquema 1.2.0 — 2026-10-17
- Nuevo modelo semántico, publicado en `data/semantic_model.json` y `data/semantic_model.yaml` (equivalentes
  campo por campo) y descrito por `schema/semantic_model.schema.json`: dimensiones (monedas, países,
  proveedores, pares y series), dominios, medidas y métricas, relaciones, jerarquías, linaje, reglas de calidad,
  archivos y diccionario de todos los campos, glosario, consumidores, incidencias conocidas y cobertura de la
  corrida. Es bilingüe (es/en) y neutral frente a cualquier herramienta. Ver `docs/MODELO_SEMANTICO.md`.
- Se genera en cada corrida (`fxpipe.semantic`) desde `config/sources.json` (estructura y umbrales),
  `config/semantic.json` (curaduría, nueva) y la corrida (cobertura). En la corrida diaria es indulgente: si
  falta curaduría o el generador falla, los datos se publican igual y el hueco queda en `curation.gaps` o en el
  registro. En las pruebas es estricto: agregar una serie, moneda o país sin curarlos hace fallar la CI.
- Incluye el registro de la incidencia del CLS de 0,022 en en-US del dashboard (resuelta el 2026-10-17 en
  `jaforero/dashboards@0a25d85`), con la causa, la corrección, las mediciones de laboratorio y en vivo y lo que
  sigue sin verificar (datos de usuarios reales, Safari y Firefox).
- Comando nuevo `python -m fxpipe semantic [--write-docs]`. `pyyaml` se agrega a las dependencias de desarrollo
  (solo las pruebas leen el YAML; la corrida diaria no tiene dependencias).
- `rates_daily.json`, `rates_daily_compact.json`, `latest.json`, `series/` y `manifest.json` conservan su forma;
  todos pasan a `schema_version` 1.2.0 (cambio compatible: los consumidores aceptan `1.x`). El manifest lista
  los dos archivos nuevos.

## esquema 1.1.0 — 2026-10-17
- Nuevo `data/rates_daily_compact.json`: series oficiales en columnas (las `primary` y los
  insumos de las cruzadas `primary`, 9 columnas hoy) más los metadatos de las 28 series
  visibles. Las cruzadas no viajan: el cliente las recalcula con `inputs` y `decimals`. Pasa de
  ~252 KB a ~56 KB comprimidos (2,5 MB a 0,26 MB sin comprimir) y crece linealmente con el número
  de países, no con el de cruzadas. Esquema en `schema/rates_daily_compact.schema.json`.
- `rates_daily.json`, `latest.json` y `series/` conservan su forma. Todos los archivos pasan a
  `schema_version` 1.1.0 (cambio compatible: los consumidores aceptan `1.x`).
- Las cruzadas se redondean con `transform.round_js` en lugar de `round`, para que el navegador
  (`Number(x.toFixed(d))`) dé exactamente el mismo número. Solo cambia el resultado en un empate
  exacto de la quinta cifra; en las 52.101 celdas publicadas hasta hoy la diferencia es 0.
- Pruebas: `tests/conftest.py` bloquea `time.sleep` y las conexiones de red reales. La corrida #12
  tardó 10 min 27 s por reintentos con esperas reales; ahora ese error falla en menos de un segundo.
- Publicar antes que el dashboard: el dashboard nuevo lee el archivo compacto y, si no existe
  o no cuadra con `latest.json`, cae a `rates_daily.json`.

## config 1.10.0 — 2026-10-16
- Perú visible en el dashboard: `primary: true` en `usd_pen_interbank`, `pen_mxn_cross`,
  `pen_cop_cross`, `pen_ars_cross`, `pen_brl_cross`, `pen_clp_cross` y `pen_uyu_cross`
  (28 series primary en total).
- Publicar después del dashboard que ya trae Perú.
- Sin cambios de esquema JSON.

## config 1.9.0 — 2026-10-16
- Perú: nuevo conector `bcrp_series` (API de series estadísticas del Banco Central de Reserva
  del Perú, BCRPData, sin token) y serie `usd_pen_interbank` (código `PD04638PD`, «TC
  Interbancario (S/ por US$) - Venta»). El conector exige el nombre de la serie
  (`source_options.expected_name`): el código de Compra es `PD04637PD` y el de Venta el
  `PD04638PD`, y un código equivocado pero válido no debe producir datos falsos.
- `date_basis: determinacion`: la variación diaria correlaciona 0,26-0,43 el mismo día con
  la PTAX, el FIX, la TRM y el observado de Chile por día de mercado y el interbancario de
  Uruguay (1.693 días), y entre -0,03 y 0,07 con un día de desfase. Sin serie por día de
  mercado: se cruza directamente.
- El valor se guarda como llega (hasta 14 decimales; el BCRP declara 3) y se muestra con 3.
- Cruzadas por día de mercado, escritas «moneda por 1 PEN» porque el sol vale más que las
  otras seis: `pen_mxn_cross`, `pen_cop_cross`, `pen_ars_cross`, `pen_brl_cross`,
  `pen_clp_cross` y `pen_uyu_cross`. Malla completa entre MXN, COP, ARS, BRL, CLP, UYU y PEN.
- Las siete series nuevas salen con `primary: false` hasta adaptar el dashboard.
- El sitio del BCRP está detrás de Imperva Incapsula: una consulta mal formada y, de forma
  intermitente, una válida devuelven HTTP 200 con una página HTML de verificación. El
  conector reintenta con espera creciente y, si persiste, lanza `ProviderError` (falla
  operativa, no cambio de contrato). Si solo ocurre desde GitHub Actions, el firewall está
  bloqueando al ejecutor.
- Septiembre llega como «Set» (también con `ing`); el conector pide `esp` y acepta ambas grafías.
- Pruebas con respuestas reales: `bcrp_interbancario_venta_real.json` (1-sep a 16-oct-2026) y
  `bcrp_desafio_incapsula_real.html` (página de verificación).
- Sin cambios de esquema JSON.

## config 1.8.0 — 2026-10-15
- Uruguay visible en el dashboard: `primary: true` en `usd_uyu_interbank`,
  `mxn_uyu_cross`, `uyu_cop_cross`, `uyu_ars_cross`, `brl_uyu_cross` y `uyu_clp_cross`
  (21 series primary en total).
- Publicar después del dashboard que ya trae Uruguay.
- Sin cambios de esquema JSON.

## config 1.7.0 — 2026-10-15
- Uruguay: nuevo conector `bcu_ws` (web service SOAP de cotizaciones del Banco Central
  del Uruguay, sin token) y serie `usd_uyu_interbank` (código 2225, dólar interbancario
  billete: promedio de las operaciones del día en BEVSA).
- `date_basis: determinacion`: el BCU fecha cada cotización por el día de sus operaciones
  y la publica el mismo día. Sin serie por día de mercado: se cruza directamente.
- Cruzadas por día de mercado: `mxn_uyu_cross`, `uyu_cop_cross`, `uyu_ars_cross`,
  `brl_uyu_cross` y `uyu_clp_cross`. Malla completa entre MXN, COP, ARS, BRL, CLP y UYU.
- Las seis series nuevas salen con `primary: false` hasta adaptar el dashboard.
- `default_http_get` acepta un cuerpo (`data`) y entonces hace POST (SOAP).
- Sin cambios de esquema JSON.

## config 1.6.0 — 2026-10-12
- Chile visible en el dashboard: `primary: true` en `usd_clp_obs`, `mxn_clp_cross`,
  `clp_cop_cross`, `clp_ars_cross` y `brl_clp_cross`. `usd_clp_obs_mkt` sigue oculto
  (insumo de las cruzadas, como `usd_cop_trm_mkt`).
- Publicar después del dashboard que ya trae los nombres, la bandera y la reserva de
  alto para 15 series.
- Sin cambios de esquema JSON.

## config 1.5.1 — 2026-10-12
- Corrección: la API BDE del Banco Central de Chile responde en ISO-8859-1
  (`application/json; charset=ISO-8859-1`, con tildes en `descripEsp`), y la lectura
  exigía UTF-8. La primera corrida en vivo marcó Chile `error:contract` («Respuesta no
  es UTF-8»); el resto de los países se actualizó normalmente y el tablero no cambió.
- `default_http_get` decodifica con UTF-8 y, si falla, con el charset que declara el
  servidor; también descomprime gzip si la respuesta llega comprimida.
- Pruebas con respuestas reales: `bcch_obs_real.json` (datos) y `bcch_error_real.json`
  (token inválido: HTTP 200 con `Codigo: -5`).

## config 1.5.0 — 2026-10-11
- Chile: nuevo conector `bcch_bde` (API REST de la Base de Datos Estadísticos del
  Banco Central de Chile, token en el secreto `BCCH_TOKEN`) y series
  `usd_clp_obs` (dólar observado, fecha de vigencia) y `usd_clp_obs_mkt` (mismo dato
  indexado por día de mercado).
- Cruzadas por día de mercado: `mxn_clp_cross`, `clp_cop_cross`, `clp_ars_cross` y
  `brl_clp_cross`. Malla completa entre MXN, COP, ARS, BRL y CLP.
- Las seis series nuevas salen con `primary: false` hasta adaptar el dashboard.
- `latest.json` → `overall_status` se calcula solo con las series `primary`, para que
  una falla de Chile (oculto) no marque el tablero como degradado. `manifest.json`
  sigue resumiendo todas las series y la alerta `fx-alerta` no cambia.
- Red: `default_http_get` reintenta también cortes de conexión durante la lectura
  (`ConnectionError`, `http.client.HTTPException`) en lugar de abortar la corrida.
- Sin cambios de esquema JSON: solo se agregan series.

## config 1.4.0 — 2026-10-05
- Brasil: nuevo conector `bcb_ptax` (API PTAX del Banco Central do Brasil, Olinda)
  y serie `usd_brl_ptax` (PTAX de venta, cierre).
- Cruzadas por día de mercado: `brl_mxn_cross` (MXN por BRL), `brl_cop_cross`
  (COP por BRL) y `brl_ars_cross` (ARS por BRL). Malla completa entre MXN, COP, ARS y BRL.
- Sin cambios de esquema JSON: solo se agregan series.

## config 1.3.0 — 2026-10-05
- Colombia: la TRM se descarga del **Banco de la República** (servicio SDMX
  `DF_TRM_DAILY_HIST`, conector `banrep_sdmx`). Atribución completa en
  `source`: autoridad (BanRep), certificación (Superintendencia Financiera) y
  base legal (Res. Ext. 1/2018 JDBR art. 40; Circular DODM-146).
- datos.gov.co (SFC) pasa a **verificación cruzada** en cada corrida y
  **respaldo declarado** (`source.fallback_used`) si el BanRep no responde.
- Verificado: BanRep y SFC publican valores idénticos (13/13 muestras 2023-2024).
- El healthcheck también prueba las fuentes de respaldo.
- Sin cambios incompatibles: `source` solo gana campos.

## config 1.2.0 — 2026-10-04
- Argentina: nuevo conector `bcra_cambiarias` (API Estadísticas Cambiarias v1.0,
  sin token, paginada) y serie `usd_ars_a3500` (código `REF`, Com. A 3500).
- Nuevas cruzadas por día de mercado: `mxn_ars_cross` (ARS por MXN) y
  `ars_cop_cross` (COP por ARS).
- Errores HTTP 4xx ahora incluyen el mensaje de la API en el manifest.
- Sin cambios de esquema JSON: solo se agregan series.

## config 1.1.0 — 2026-10-04
- **Corrección metodológica de `mxn_cop_cross`.** La versión 1.0.0 cruzaba la TRM
  vigente (mercado del día hábil previo) con el FIX de liquidación (FIX determinado
  dos días hábiles antes, verificado contra la tabla oficial de Banxico). Esto
  mezclaba días de mercado distintos: diferencia mediana 0,62 %, p95 2,2 %,
  máximo 6,35 % sobre 2020-2026. Ahora se cruza TRM ÷ FIX del mismo día de mercado.
- Nueva serie `usd_cop_trm_mkt` (TRM indexada por día de mercado).
- `load_config` rechaza tasas cruzadas con insumos de distinto `date_basis`.
- Fixture de Banxico corregido para reflejar el desfase real de 2 días hábiles.
- Sin cambios de esquema JSON (`schema_version` 1.0.0): solo se agrega una serie.

## config 1.0.0 — 2026-10-04
- Versión inicial: USD-MXN (FIX), USD-COP (TRM), MXN-COP (cruzada).
