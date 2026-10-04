# Conectar el dashboard

El repositorio debe ser **público**: así el dashboard lee los JSON sin
credenciales. Nunca pongas un token de GitHub en código que corre en el navegador.

## URLs

```
https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data/latest.json
https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data/rates_daily.json
```

`raw.githubusercontent.com` permite CORS y aplica una caché corta (minutos).
Alternativa con CDN (caché más larga, útil con mucho tráfico):
`https://cdn.jsdelivr.net/gh/jaforero/fx-bancos-centrales@main/data/latest.json`

## JavaScript

```js
const BASE = "https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data";

async function cargarTasas() {
  const [latest, daily] = await Promise.all([
    fetch(`${BASE}/latest.json`, { cache: "no-cache" }).then(r => r.json()),
    fetch(`${BASE}/rates_daily.json`, { cache: "no-cache" }).then(r => r.json()),
  ]);
  if (!latest.schema_version.startsWith("1.")) throw new Error("Contrato incompatible");
  return { latest, daily };
}
// latest.series.usd_mxn_fix.value      -> KPI principal
// latest.series.mxn_cop_cross.status   -> mostrar aviso si != "ok"
// daily.rows.map(r => [r.date, r.usd_cop_trm])  -> serie para gráfico
```

## Power BI / Tableau

Fuente *Web* con la URL de `rates_daily.json`; expandir `rows` a tabla. Programar
la actualización después de las 02:00 UTC para tomar la corrida nocturna.

## Python

```python
import pandas as pd, requests
url = "https://raw.githubusercontent.com/jaforero/fx-bancos-centrales/main/data/rates_daily.json"
df = pd.DataFrame(requests.get(url, timeout=30).json()["rows"]).set_index("date")
```

## Buenas prácticas de visualización

- Mostrar `status` y `days_since_official` junto al KPI: el usuario debe saber si mira un dato viejo.
- Distinguir visualmente los días `filled` (línea punteada o marcador hueco).
- Rotular la cruzada como "calculada", no "oficial".
