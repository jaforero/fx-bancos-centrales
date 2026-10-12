# Cambios

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
