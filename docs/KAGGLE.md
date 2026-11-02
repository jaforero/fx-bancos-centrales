# Capa Kaggle

El repositorio sigue siendo la única fuente de verdad. Kaggle es una capa de distribución para análisis:
`scripts/build_kaggle_dataset.py` transforma los productos canónicos de `data/` en `dist/kaggle/`, documenta y
valida; no descarga nada de los bancos centrales, no necesita tokens de Banxico ni del BCCh y no recalcula
ninguna tasa.

```
bancos centrales → fxpipe (daily-update) → data/ en main → build_kaggle_dataset.py → validación + linaje
  + compuertas de licencias y frescura → dist/kaggle/ → dataset de Kaggle (versiones)
```

Dataset: `jaforero/latin-america-central-bank-fx-rates` (id en `kaggle/dataset-metadata.template.json`).

## Archivos

| Ruta | Qué es |
|---|---|
| `scripts/build_kaggle_dataset.py` | Construye y valida `dist/kaggle/`; escribe `dist/kaggle_readiness.md` (fuera del paquete) |
| `kaggle/licenses.json` | Revisión de condiciones de cada proveedor, con la URL oficial; alimenta la compuerta de licencias |
| `kaggle/README.template.md`, `kaggle/METHODOLOGY.template.md` | Prosa fija; las tablas y cifras se generan desde el modelo semántico y la corrida |
| `kaggle/dataset-metadata.template.json` | Id, título, subtítulo, etiquetas y frecuencia de Kaggle |
| `tests/test_kaggle_export.py` | Cobertura, claves, reconciliación de valores y `filled`, cruzadas, estado, compuertas |
| `.github/workflows/publish-kaggle.yml` | Crea versiones nuevas; solo `workflow_dispatch` por ahora |

`DATA_DICTIONARY.md`, `SOURCES_AND_LICENSES.md` y `PUBLICATION_BLOCKERS.md` no tienen plantilla: se generan por
completo desde `semantic_model.json` y `kaggle/licenses.json`.

## Compuertas

- **Validación de datos (bloquea todo):** `schema_version` 1.x y coherente, `config_version` del manifest igual a
  la configuración, mismas series en la configuración, `rates_daily.json`, `latest.json`, el manifest y el modelo
  semántico, valores positivos, `filled` coherente; tras escribir, cada CSV se relee y se compara celda a celda;
  cada cruzada se verifica con `transform.round_js`; frente a la versión anterior de Kaggle no puede desaparecer
  una serie ni perder observaciones oficiales (`--allow-history-loss` solo para un retiro explícito y revisado).
  Si falla, el paquete queda en `dist/kaggle.failed/` y el script sale con código 1.
- **Procedencia:** `SOURCE_COMMIT.txt` registra repositorio, commit, versiones, `data_hash` del manifest y
  `package_hash` del paquete. Falla si `data/` o `config/` tienen cambios sin commit.
- **Frescura (avisa, no bloquea):** una fuente degradada da `READY WITH FRESHNESS WARNINGS`; su histórico sigue
  en el paquete y el estado queda visible en el snapshot, los metadatos, el README y el mensaje de versión.
- **Licencias (bloquea solo lo público):** un proveedor que prohíbe redistribuir, un proveedor sin revisar o una
  cruzada que combina una licencia share-alike con otra que exige autorización para uso comercial bloquean la
  publicación pública y generan `PUBLICATION_BLOCKERS.md`. El paquete se puede mantener **privado**. Para
  levantar un bloqueo se registra la autorización en `resolutions` de `kaggle/licenses.json`; ningún país se
  retira en silencio.

La licencia de Kaggle es `other`: ninguna licencia única representa a todas las fuentes (ver
`SOURCES_AND_LICENSES.md` del paquete).

## Comandos

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]" "kaggle==2.2.5" six
.venv/bin/python scripts/build_kaggle_dataset.py          # construir y validar
.venv/bin/pytest -q tests/test_kaggle_export.py           # pruebas de la capa Kaggle
```

Autenticación: `kaggle auth login`, o la variable `KAGGLE_API_TOKEN` (token de la configuración de la cuenta de
Kaggle). Nunca en el repositorio.

**Primera publicación (privada):**

```bash
.venv/bin/kaggle datasets create -p dist/kaggle            # sin -u: privado
.venv/bin/kaggle datasets status jaforero/latin-america-central-bank-fx-rates
.venv/bin/kaggle datasets metadata jaforero/latin-america-central-bank-fx-rates --update -p dist/kaggle
```

`create` no envía la frecuencia de actualización, las fuentes ni la descripción de columnas; por eso el
`metadata --update`. Ese comando trata un `isPrivate` ausente como público: el constructor siempre lo escribe
explícito (`true` salvo `--visibility public`, que exige la compuerta de licencias en PASS). Después, revisar las
etiquetas aplicadas con `kaggle datasets metadata <id> -p /tmp/meta`: Kaggle ignora las que no existen.

Con las 8 etiquetas actuales, `create` (CLI 2.2.5) respondió «You have exceeded the max category limit» pero
**sí creó** el dataset, privado, con todos los archivos y sin etiquetas (2026-11-02); `metadata --update` aplicó
después las 8. Ante ese mensaje, comprueba con `datasets status` antes de reintentar: un segundo `create` falla
porque el slug ya existe.

**Versiones siguientes:** el workflow «Publicar versión en Kaggle» (secreto `KAGGLE_API_TOKEN`) descarga
`SOURCE_COMMIT.txt` y `fx_series_metadata.csv` de la versión publicada, reconstruye, compara `package_hash` y solo
crea versión si cambiaron los datos (si no, `NO KAGGLE DATA UPDATE REQUIRED`). A mano:

```bash
.venv/bin/kaggle datasets download jaforero/latin-america-central-bank-fx-rates -f SOURCE_COMMIT.txt -p prev
.venv/bin/kaggle datasets download jaforero/latin-america-central-bank-fx-rates -f fx_series_metadata.csv -p prev
.venv/bin/python scripts/build_kaggle_dataset.py --compare-with prev
.venv/bin/kaggle datasets version -p dist/kaggle -m "<mensaje que imprime el script>"
```

Nunca `--delete-old-versions`.

## Encadenar con la corrida diaria

`workflow_run` sobre «Actualización diaria de tasas» (comentado en el workflow) es la opción más simple: corre
después del commit del bot, lee `main`, no duplica `daily-update.yml` ni necesita sus secretos, el grupo de
concurrencia `kaggle-publish` serializa las corridas y `package_hash` hace que repetirla no cree versiones.
No se activa hasta validar a mano la primera publicación.
