# Contrato de datos (schema_version 1.x)

El dashboard depende de este contrato. Cambios compatibles (agregar series,
agregar campos opcionales) suben la versión menor (1.0 → 1.1). Renombrar o
eliminar campos, o cambiar su significado, exige versión mayor (2.0) y
publicarla en paralelo bajo otra ruta durante una transición.

## Convenciones

- **Par `BASE-COTIZADA`**: `USD-MXN = 18.2` significa 18.2 MXN por 1 USD.
- **id de serie** (`usd_mxn_fix`): clave estable que usa el dashboard. Un par puede
  tener varias series (FIX determinación vs. liquidación).
- **Fechas**: ISO `AAAA-MM-DD`, sin hora.
- **`date_basis`**:
  - `determinacion`: fecha del mercado con que se formó la tasa (FIX: día de
    determinación; `usd_cop_trm_mkt`: vigenciadesde − 1 día).
  - `vigencia`: fecha en la que la tasa rige. TRM: mercado del día hábil previo.
    FIX "para pagos" (`usd_mxn_liq`): FIX determinado 2 días hábiles antes.
  Solo se cruzan series con el mismo `date_basis`; `load_config` lo exige.
- **`filled: true`**: el pipeline repitió el último valor oficial porque la fuente no
  publica ese día (fin de semana o festivo del país). Máximo `max_fill_days`; luego hay hueco.
  En la TRM los fines de semana no son relleno: la SFC publica la vigencia explícita.
- **Tasa cruzada** `filled` si cualquiera de sus insumos lo está.

## latest.json

```json
{
  "schema_version": "1.0.0",
  "as_of": "2026-10-04",
  "overall_status": "ok | degraded",
  "series": {
    "<id>": {
      "pair": "USD-COP", "label": "...", "quote_unit": "COP por 1 USD",
      "date_basis": "vigencia", "primary": true, "decimals": 2,
      "method": "official | cross_via_usd: <num> / <den>",
      "source": {"provider", "institution", "country", "series_id", "docs_url"},
      "value": 3273.49,            // último valor OFICIAL con fecha <= as_of
      "date": "2026-10-04",
      "days_since_official": 0,
      "prev_value": 3307.73,       // registro oficial anterior (no la misma vigencia)
      "prev_date": "2026-10-02",
      "change_abs": -34.24, "change_pct": -1.0351,
      "next": {"date": "...", "value": 0},   // opcional: valor oficial futuro ya publicado
      "status": "ok | stale | error:provider | error:contract | error:validation | error:empty",
      "error": "solo si status es error:*"
    }
  }
}
```

Campos de `source` (series oficiales): `provider`, `institution`, `country`,
`series_id`, `docs_url` y, cuando aplica, `authority`, `certified_by`,
`legal_basis`, `verified_against` (fuente de verificación) y `fallback_used`
(`true` si el dato vino del respaldo en la última corrida).

Regla de KPI para tasas cruzadas: `value` es el último día en que **ambos**
insumos son oficiales. El valor con relleno existe en `rates_daily.json`
marcado en `filled`.

## rates_daily.json

```json
{
  "schema_version": "1.0.0",
  "as_of": "2026-10-04",
  "series": {"<id>": { metadatos como arriba }},
  "rows": [
    {"date": "2026-10-04", "usd_mxn_fix": 18.2, "usd_mxn_liq": 18.25,
     "usd_cop_trm": 3273.49, "mxn_cop_cross": 179.3693,
     "filled": ["usd_mxn_fix", "usd_mxn_liq", "mxn_cop_cross"]}
  ]
}
```

Una fila por día calendario; `null` si la serie no tiene dato ese día. `filled`
se omite cuando está vacío.

## Validación

Esquemas JSON en `schema/`. El CI valida las salidas contra ellos.
