# Cambios

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
