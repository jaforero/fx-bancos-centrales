# ${title}

Daily **official reference exchange rates** published by Latin American central banks (and, for Colombia, the
Banco de la República with the Superintendencia Financiera), plus **cross rates between those currencies
calculated by this project**. Every row says whether it is an official observation or a value carried forward
(`filled`), whether the series is official or calculated (`series_kind`), and which date it is attributed to
(`date_basis`).

- **Source repository (single source of truth):** ${repository}
- **Dashboard built on the same data:** ${dashboard_url}
- **This version:** repository commit `${commit}` · data contract `schema_version ${schema_version}` ·
  configuration `${config_version}` · semantic model `${semantic_model_version}` · data as of ${as_of}

> Not every number here is an "exchange rate published by a central bank". Official reference rates and the
> project's own cross rates are kept separate in every file. Please keep them separate in your work too.

## About

A GitHub Actions pipeline (`fxpipe`) downloads each rate from the institution that defines it, validates it,
fills weekends and holidays in a marked way, calculates cross rates only between series formed on the same
market day, and publishes versioned JSON files. This Kaggle dataset is an analytical distribution of those
canonical files: same values, same semantics, reshaped into CSV and documented. Nothing is recalculated for
Kaggle.

## Coverage

| | |
|---|---|
| Countries with an official source | ${n_countries}: ${countries} |
| Currencies | ${n_currencies}: ${currencies} |
| Pairs | ${n_pairs} |
| Series | ${n_series}: ${n_official} official and ${n_cross} calculated cross rates; ${n_primary} primary (one per pair) |
| Dates in `fx_rates_daily.csv` | ${first_date} → ${last_date} (${n_daily} series-day rows) |

The last dates can be later than `as_of`: some sources publish values that take effect in the future (the TRM
for the next business day, the FIX for payments). They are official values, not forecasts.

## Official rates

${official_table}

Hidden variants (`primary = false`) are not different rates but the same data indexed differently:
${hidden_variants}. Use the `primary = true` series as "the" rate of each country against the USD.

## Cross rates

${cross_table}

Cross rates are **calculations made by this project**, quotient of two official USD rates of the **same market
day**, rounded to the series' `decimals` with JavaScript `toFixed` semantics. No central bank publishes them.
Their formula and inputs are in `fx_series_metadata.csv` (`numerator_series`, `denominator_series`, `formula`)
and in `semantic_model.json`. They are published here exactly as the pipeline produced them.

## Date semantics

- **`determinacion` (determination date):** the market day on which the rate was formed (FIX, PTAX, A 3500,
  Uruguay and Peru interbank rates, and the `*_mkt` variants).
- **`vigencia` (effective date):** the day on which the rate applies. Series by effective date: ${vigencia_series}.
  The TRM and Chile's observed dollar are calculated with the previous business day's market.

Two values with the same calendar date and different `date_basis` do **not** describe the same economic moment.
Do not divide, correlate or regress them as if they did. For forecasting and any multi-series model, use series
with the same `date_basis` (all cross rates and every `primary` series except those listed above are
`determinacion`; use `usd_cop_trm_mkt` and `usd_clp_obs_mkt` to align Colombia and Chile by market day).

## Filled values

${filled_rule}

`filled = true` is **never** a new official observation. Pick what your analysis needs:

- **Official observations only** (returns, volatility, statistical tests): `filled == False`.
- **Calendar-day series** (joining with daily data, displaying a value for every day): keep all rows.

## Source hierarchy

The source of each official series is the central bank (or authority) that defines it. A second official
source enters only as declared verification or fallback (for the TRM, the Superintendencia Financiera on
datos.gov.co), never silently. Aggregators are not used.

## Dataset files

${files_table}

Plus `README.md`, `DATA_DICTIONARY.md` (every column of every CSV), `METHODOLOGY.md`, `SOURCES_AND_LICENSES.md`
and `SOURCE_COMMIT.txt` (repository, commit SHA, versions, `data_hash` and `package_hash` of this version).

**Which file?** `fx_rates_daily.csv` (LONG) is the recommended, canonical-semantics file: one row per series and
date, with `filled` per cell. `fx_rates_wide.csv` is the same data with one numeric column per series; its
`filled_series` column lists the filled ids of each row, so the information is kept but is less convenient to
filter. For forecasting, start from `fx_rates_daily.csv` filtered to one series, `filled == False`.

## Load it

```python
import pandas as pd

fx = pd.read_csv("fx_rates_daily.csv", parse_dates=["date"])
meta = pd.read_csv("fx_series_metadata.csv")

# Official observations of the Colombian TRM (no weekend/holiday carry-forward)
cop = fx[(fx["series_id"] == "usd_cop_trm") & (~fx["filled"])]
print(cop.tail())

# One official rate per country against the USD
usd = meta[(meta["series_kind"] == "official") & meta["primary"]]
print(usd[["series_id", "pair", "institution", "date_basis", "status"]])

# How is a cross rate calculated?
print(meta.loc[meta["series_id"] == "mxn_cop_cross", ["formula", "date_basis", "root_providers"]])
```

`filled == False` keeps only observations that were not carried forward. `filled`, `primary` are booleans
(`true`/`false` in the file).

## Semantic model

`semantic_model.json` is the repository's canonical, bilingual (es/en) semantic model, included unchanged. It
describes every series, pair, currency, country and provider; how series relate (`market_day_variant_of`,
`settlement_view_of`, cross-rate inputs); the lineage from each provider through the pipeline stages; the quality
rules and their thresholds; a dictionary of every field of every canonical file; a glossary; usage guidance; and
known issues. Load it to drive a BI model, a data catalogue or an AI agent without reading the pipeline code.

## Data quality

`status` (in `fx_latest_snapshot.csv` and `fx_series_metadata.csv`) comes from the pipeline: `ok`, `stale` (no
new official value for longer than `stale_after_days`), `error:provider` (the source did not answer),
`error:contract` (the source changed its format), `error:validation` (a value outside the plausible range was
rejected) or `error:empty`. A degraded series **keeps its history**; only its newest values are missing. The
snapshot's `value` is always the last official value, with its `date` and `days_since_official`, so a stale
value is never presented as a new one.

Structural quality (did the files pass the contract and the reconciliation?) is checked before every version.
Source freshness (did each provider update?) at the time this version was built:

```
${freshness}
```

## Updating

The repository updates on weekdays at 19:30 UTC and every day at 01:00 UTC. A new Kaggle version is created only
when the data content changes (`package_hash` in `SOURCE_COMMIT.txt`), so expect at most about one version a
day. Old versions are never deleted: they are part of the audit trail.

## Limitations

- Reference rates are **not** executable transaction prices; banks and exchange bureaus apply their own margins.
- Each rate has its own methodology (fixing, weighted average, interbank average, regulatory reference…); see
  `rate_type` and each series' description.
- Effective dates and determination dates differ; see Date semantics.
- Filled values are not new official observations.
- Cross rates are calculated by this project; they are not official.
- Methodology and coverage can change over time (new countries, corrected connectors); the `CHANGELOG.md` of the
  repository and the semantic model's `changelog` record every change.
- The TRM may come from the declared fallback (Superintendencia Financiera) on days the primary source failed;
  observations are not tagged by origin (the pipeline verifies both sources publish identical values).
- Pairs with ARS change scale strongly over time (inflation, devaluations): use a log scale or returns.

## Licence and terms

The Kaggle licence field is **`${kaggle_license}`**: this dataset does not grant a new licence over the data.
Each value keeps the terms of the institution(s) it comes from (`root_providers` in `fx_series_metadata.csv`).
${licence_summary} See `SOURCES_AND_LICENSES.md` before reusing the data, especially commercially.
Licence review status of this version: **${licence_status}**.

## Provenance

- Repository: ${repository}
- Dashboard: ${dashboard_url}
- Exact source of this version: `SOURCE_COMMIT.txt`

## Citation

Cite the original institutions for the data you use:

${sources_attribution}

And this compilation:

> Forero, J. (${year}). *${title}* [Data set]. Kaggle. https://www.kaggle.com/datasets/${dataset_id}.
> Built from ${repository} (commit `${commit}`, data as of ${as_of}). Cross rates are the author's calculations.

## En español

Tasas de cambio de referencia oficiales y diarias de bancos centrales de América Latina, más tasas cruzadas
entre esas monedas **calculadas por este proyecto** (no son tasas oficiales). Cada fila indica si el valor es una
observación oficial o un relleno (`filled`), si la serie es oficial o calculada (`series_kind`) y a qué fecha se
atribuye (`date_basis`: `determinacion` = día de mercado en que se formó; `vigencia` = día en que rige). No
cruce series con distinto `date_basis`. La fuente de verdad es el repositorio de GitHub; este dataset es una
capa de distribución. Cite siempre a la institución de origen y revise `SOURCES_AND_LICENSES.md`.
