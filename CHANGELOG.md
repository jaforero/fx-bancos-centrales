# Cambios

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
