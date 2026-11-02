# Methodology

This is a summary of the methodology of the source repository, not a new one. Every table below is generated
from `semantic_model.json` (semantic model ${semantic_model_version}), which is itself generated from the
pipeline configuration and code. The Kaggle layer adds no calculation: it reshapes the canonical files, copies
the semantic model and the manifest, and validates that nothing changed on the way.

## 1. Origin of the rates

Each official series is downloaded from the institution that defines it (a central bank, or for Colombia the
Banco de la República with the Superintendencia Financiera as certifier, verification source and declared
fallback). Aggregators are never used as a primary source. See `fx_series_metadata.csv` (`institution`,
`source_series_id`, `source_docs_url`) and `SOURCES_AND_LICENSES.md`.

## 2. Pipeline stages

${stages}

## 3. Conventions (normalization, calendar, filling, rounding)

${conventions}

## 4. Date basis

${date_basis_rule}

`determinacion` dates a value by the market day on which it was formed; `vigencia` dates it by the day on
which it applies. The TRM (Colombia) and the observed dollar (Chile) are published by validity date, so the
repository also publishes them re-indexed by market day (`usd_cop_trm_mkt`, `usd_clp_obs_mkt`); those
variants are the inputs of every cross rate. The pipeline refuses to load a configuration that crosses series
with different `date_basis`.

## 5. Cross rates

A cross rate is the quotient of two official series against the USD **on the same market day**, rounded to the
series' `decimals` with `round_js` (identical to JavaScript `Number(x.toFixed(decimals))`), and `filled` if
either input is filled. It is a calculation of this project, never an official rate. The Kaggle builder
re-derives every published cross-rate cell with the same function **only as a check**; the published values are
the canonical ones from `rates_daily.json`.

## 6. Validation, freshness and error handling

${quality_rules}

Status inheritance for cross rates: ${status_inheritance}

A source that fails never deletes good data: the series keeps its history and is marked `error:*` or `stale`.
The Kaggle layer follows the same rule: a degraded provider is reported (`fx_latest_snapshot.csv`,
`fx_series_metadata.csv`, `manifest.json`, the README freshness report) but its history stays in the dataset.

## 7. Derived metrics

The repository declares these metrics; those marked as consumer metrics are computed by the dashboard, not by
the pipeline. None is added as a column to this dataset.

${metrics}

## 8. Usage guidance

${usage_guidance}

## 9. Reconciliation performed by the Kaggle builder

- Every value and every `filled` flag in `fx_rates_daily.csv` and `fx_rates_wide.csv` is compared with
  `rates_daily.json` after writing (exact float equality; no rounding is added).
- Series sets are compared across the configuration, `rates_daily.json`, `latest.json`, `manifest.json` and the
  semantic model; per-series row counts are compared with `semantic_model.json#coverage`.
- Status in the snapshot and metadata is compared with `latest.json` and `manifest.json`.
- Every cross-rate cell is re-derived with `round_js` and compared.
- Against the previous Kaggle version, no series may disappear and no series may lose official observations.
