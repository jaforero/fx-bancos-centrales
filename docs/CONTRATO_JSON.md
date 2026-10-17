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
  "schema_version": "1.1.0",
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

`overall_status` de `latest.json` resume solo las series `primary` (lo que muestra el
dashboard); el de `manifest.json` resume todas y es el que usa la alerta `fx-alerta`.

`primary: true` marca las series que muestra el dashboard. Una serie nueva puede
publicarse con `false` mientras el dashboard se adapta y pasar a `true` después:
es un cambio compatible (así entró Chile en config 1.5.0).

Regla de KPI para tasas cruzadas: `value` es el último día en que **ambos**
insumos son oficiales. El valor con relleno existe en `rates_daily.json`
marcado en `filled`.

## rates_daily.json

```json
{
  "schema_version": "1.1.0",
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

## rates_daily_compact.json (desde `schema_version` 1.1.0)

Lo mismo que `rates_daily.json` para el dashboard, sin las cruzadas: viajan solo las
series oficiales que hacen falta, en columnas, y el cliente recalcula las cruzadas.
Con 7 países pesa ~56 KB comprimidos contra ~252 KB de `rates_daily.json`, y crece
~6 KB por país en vez de ~6 KB por cada cruzada nueva. `rates_daily.json` no cambia:
sigue siendo el archivo para Power BI, Python y cualquier otro consumidor.

```json
{
  "schema_version": "1.1.0",
  "layout": "columnar",
  "as_of": "2026-10-17",
  "series": {"<id>": { metadatos como arriba; las cruzadas traen "inputs": [numerador, denominador] }},
  "dates": ["2020-01-01", "2020-01-02"],
  "columns": {
    "usd_mxn_fix": {"filled": [0], "values": [null, 18.8817]}
  }
}
```

- `dates`: unión ordenada de las fechas de las columnas.
- `columns[id].values[i]`: valor de `dates[i]` (oficial o relleno), o `null` si no hay.
- `columns[id].filled`: índices de `dates` cuyo valor es relleno.
- **Columnas**: las series oficiales `primary` y las oficiales que son insumo de una cruzada
  `primary`, aunque estén ocultas (la TRM y el dólar observado por día de mercado,
  `usd_cop_trm_mkt` y `usd_clp_obs_mkt`). Se deducen de los metadatos, sin listas fijas.
  Un insumo que fuera una cruzada se rechaza.
- **Cruzadas** (`series[id].inputs` y sin columna): en cada índice `i`, si el numerador y el
  denominador tienen valor y el denominador no es 0, el valor es
  `Number((num / den).toFixed(decimals))` y es relleno si cualquiera de los dos lo es; si no,
  no hay dato. Da exactamente lo que publica `rates_daily.json`: el backend redondea con
  `transform.round_js`, que replica `toFixed` (solo difiere de `round` de Python en un
  empate exacto, p. ej. 18.03125 con 4 decimales).
- El dashboard compara, al cargar, el último valor oficial de cada cruzada recalculada con el de
  `latest.json`; si alguno no coincide, descarta el compacto y carga `rates_daily.json`.

## Validación

Esquemas JSON en `schema/`. El CI valida las salidas contra ellos.
