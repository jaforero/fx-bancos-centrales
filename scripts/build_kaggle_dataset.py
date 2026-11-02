#!/usr/bin/env python3
"""Construye el paquete Kaggle (dist/kaggle/) a partir de los productos canónicos de data/.

Uso:
  python scripts/build_kaggle_dataset.py                         # construye y valida dist/kaggle/
  python scripts/build_kaggle_dataset.py --compare-with prev/    # además compara con la versión publicada
  python scripts/build_kaggle_dataset.py --visibility public     # solo si la compuerta de licencias pasa

No llama a ningún banco central, no necesita tokens ni Internet y no recalcula nada: transforma el
formato de `data/rates_daily.json`, `data/latest.json`, `data/manifest.json` y
`data/semantic_model.json`, documenta y valida. Las cruzadas se recalculan solo como verificación
independiente, con la misma `round_js` del pipeline.

Códigos de salida: 0 paquete construido y válido (puede llevar avisos de frescura o un bloqueo de
licencias para la publicación pública); 1 la validación de datos falló (el paquete no se publica);
2 error de uso.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from string import Template

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from fxpipe import transform  # noqa: E402  (round_js: solo para verificar las cruzadas)

DATA = ROOT / "data"
KAGGLE_SRC = ROOT / "kaggle"
DIST = ROOT / "dist"
DEFAULT_OUT = DIST / "kaggle"
SUPPORTED_SCHEMA_MAJOR = "1"

DAILY = "fx_rates_daily.csv"
WIDE = "fx_rates_wide.csv"
META = "fx_series_metadata.csv"
SNAP = "fx_latest_snapshot.csv"
DATA_FILES = [DAILY, WIDE, META, SNAP, "semantic_model.json", "manifest.json"]
TRUE, FALSE = "true", "false"


class ValidationFailed(Exception):
    pass


# ------------------------------------------------------------------ lectura

def load_canonical(data_dir: Path = DATA, config_path: Path = ROOT / "config" / "sources.json") -> dict:
    def j(p: Path):
        return json.loads(p.read_text(encoding="utf-8"))

    return {
        "config": j(config_path),
        "latest": j(data_dir / "latest.json"),
        "rates": j(data_dir / "rates_daily.json"),
        "manifest": j(data_dir / "manifest.json"),
        "model": j(data_dir / "semantic_model.json"),
        "raw": {name: (data_dir / name).read_bytes() for name in ("semantic_model.json", "manifest.json")},
        "licenses": j(KAGGLE_SRC / "licenses.json"),
    }


def series_order(c: dict) -> list[str]:
    return list(c["rates"]["series"])


def model_index(c: dict) -> dict:
    m = c["model"]
    return {
        "series": {s["id"]: s for s in m["dimensions"]["series"]},
        "currencies": {x["id"]: x for x in m["dimensions"]["currencies"]},
        "countries": {x["id"]: x for x in m["dimensions"]["countries"]},
        "providers": {x["id"]: x for x in m["dimensions"]["providers"]},
        "coverage": {x["id"]: x for x in m["coverage"]["series"]},
        "fields": {x["id"]: x for x in m["fields"]},
        "lineage": {x["series"]: x for x in m["lineage"]["series"]},
        "domains": m["domains"],
    }


# ------------------------------------------------------------------ contrato

def check_contract(c: dict) -> list[str]:
    """Comprobaciones estructurales antes de transformar. Devuelve errores (vacío = pasa)."""
    errs: list[str] = []
    versions = {
        "latest.json": c["latest"].get("schema_version"),
        "rates_daily.json": c["rates"].get("schema_version"),
        "manifest.json": c["manifest"].get("schema_version"),
        "semantic_model.json": c["model"].get("schema_version"),
    }
    for name, v in versions.items():
        if not isinstance(v, str) or v.split(".")[0] != SUPPORTED_SCHEMA_MAJOR:
            errs.append(f"{name}: schema_version {v!r} incompatible (se acepta {SUPPORTED_SCHEMA_MAJOR}.x)")
    if len(set(versions.values())) != 1:
        errs.append(f"schema_version distinta entre archivos: {versions}")
    if c["manifest"].get("config_version") != c["config"].get("config_version"):
        errs.append(f"config_version del manifest ({c['manifest'].get('config_version')}) "
                    f"≠ config/sources.json ({c['config'].get('config_version')})")
    if c["model"].get("generated_from", {}).get("config_version") != c["config"].get("config_version"):
        errs.append("semantic_model.json no se generó con la config_version vigente")
    as_ofs = {c["latest"]["as_of"], c["rates"]["as_of"], c["manifest"]["as_of"], c["model"]["coverage"]["as_of"]}
    if len(as_ofs) != 1:
        errs.append(f"as_of distinto entre archivos: {sorted(as_ofs)}")

    cfg_ids = list(c["config"]["series"]) + list(c["config"].get("derived", {}))
    sets = {
        "config/sources.json": cfg_ids,
        "rates_daily.json#series": list(c["rates"]["series"]),
        "latest.json#series": list(c["latest"]["series"]),
        "manifest.json#series_status": list(c["manifest"]["series_status"]),
        "semantic_model.json#dimensions.series": [s["id"] for s in c["model"]["dimensions"]["series"]],
        "semantic_model.json#coverage.series": [s["id"] for s in c["model"]["coverage"]["series"]],
    }
    ref = set(cfg_ids)
    for name, ids in sets.items():
        if len(ids) != len(set(ids)):
            errs.append(f"{name}: ids repetidos")
        if set(ids) != ref:
            errs.append(f"{name}: series distintas de la configuración "
                        f"(faltan {sorted(ref - set(ids))}, sobran {sorted(set(ids) - ref)})")
    if list(c["rates"]["series"]) != cfg_ids:
        errs.append("rates_daily.json#series no sigue el orden de la configuración")

    order = series_order(c)
    prev = None
    for i, row in enumerate(c["rates"]["rows"]):
        try:
            d = date.fromisoformat(row["date"])
        except (KeyError, ValueError):
            errs.append(f"rows[{i}]: fecha inválida {row.get('date')!r}")
            continue
        if prev and d <= prev:
            errs.append(f"rows[{i}]: fechas no estrictamente crecientes ({prev} → {d})")
        prev = d
        extra = set(row) - set(order) - {"date", "filled"}
        if extra:
            errs.append(f"rows[{i}] {row['date']}: columnas desconocidas {sorted(extra)}")
        filled = row.get("filled", [])
        for sid in order:
            v = row.get(sid)
            if v is not None and (not isinstance(v, (int, float)) or isinstance(v, bool) or not v > 0):
                errs.append(f"{row['date']} {sid}: valor no positivo o no numérico {v!r}")
        for sid in filled:
            if sid not in order or row.get(sid) is None:
                errs.append(f"{row['date']}: «{sid}» marcada filled sin valor")
        if len(filled) != len(set(filled)):
            errs.append(f"{row['date']}: filled con ids repetidos")

    idx = model_index(c)
    for sid in order:
        s = idx["series"].get(sid)
        if not s:
            continue
        pair = s["pair"]
        if pair != f"{s['base_currency']}-{s['quote_currency']}" or pair != c["rates"]["series"][sid]["pair"]:
            errs.append(f"{sid}: par incoherente entre el modelo y rates_daily.json")
        for cur in (s["base_currency"], s["quote_currency"]):
            if cur not in idx["currencies"]:
                errs.append(f"{sid}: moneda {cur} ausente de dimensions.currencies")
        if s["kind"] == "cross":
            der = s.get("derivation") or {}
            if not der.get("numerator") or not der.get("denominator"):
                errs.append(f"{sid}: cruzada sin numerador/denominador en el modelo")
            elif c["rates"]["series"][sid].get("inputs") != [der["numerator"], der["denominator"]]:
                errs.append(f"{sid}: inputs de rates_daily.json ≠ derivation del modelo")
            if s["rate_type"] != "cross_rate" or c["rates"]["series"][sid].get("method", "").split(":")[0] != "cross_via_usd":
                errs.append(f"{sid}: una cruzada debe ser rate_type=cross_rate y method=cross_via_usd")
        elif s["kind"] == "official":
            if c["rates"]["series"][sid].get("method") != "official" or not s.get("source"):
                errs.append(f"{sid}: serie oficial sin method=official o sin source")
        else:
            errs.append(f"{sid}: series_kind desconocido {s['kind']!r}")
        if s["date_basis"] not in [d["value"] for d in idx["domains"]["date_basis"]]:
            errs.append(f"{sid}: date_basis desconocido {s['date_basis']!r}")
        if c["latest"]["series"][sid]["status"] != c["manifest"]["series_status"][sid]:
            errs.append(f"{sid}: status de latest.json ≠ manifest.json")
    for sid, st in c["manifest"]["series_status"].items():
        if st not in [d["value"] for d in idx["domains"]["status"]]:
            errs.append(f"{sid}: status desconocido {st!r}")
    return errs


def check_json_schemas(c: dict) -> tuple[str, list[str]]:
    """Valida contra schema/ si jsonschema está instalado (dependencia de desarrollo)."""
    try:
        import jsonschema
    except ImportError:
        return "NOT RUN", ["jsonschema no está instalado: pip install -e '.[dev]'"]
    errs = []
    for doc, schema in (("latest", "latest.schema.json"), ("rates", "rates_daily.schema.json"),
                        ("model", "semantic_model.schema.json")):
        sch = json.loads((ROOT / "schema" / schema).read_text(encoding="utf-8"))
        try:
            jsonschema.validate(c[doc], sch)
        except jsonschema.ValidationError as exc:
            errs.append(f"{schema}: {exc.message}")
    return ("PASS" if not errs else "FAIL"), errs


# ------------------------------------------------------------------ tablas

def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return TRUE if v else FALSE
    if isinstance(v, float):
        return repr(v)  # la representación más corta que reproduce el mismo float: sin redondeo nuevo
    return str(v)


def daily_table(c: dict) -> tuple[list[str], list[list]]:
    idx = model_index(c)
    cols = ["date", "series_id", "pair", "base_currency", "quote_currency", "value", "filled",
            "series_kind", "rate_type", "date_basis", "primary"]
    rows = []
    for row in c["rates"]["rows"]:
        filled = set(row.get("filled", []))
        for sid in series_order(c):
            v = row[sid]
            if v is None:
                continue
            s = idx["series"][sid]
            rows.append([row["date"], sid, s["pair"], s["base_currency"], s["quote_currency"], v,
                         sid in filled, s["kind"], s["rate_type"], s["date_basis"], s["primary"]])
    return cols, rows


def wide_table(c: dict) -> tuple[list[str], list[list]]:
    order = series_order(c)
    cols = ["date", *order, "filled_series"]
    rows = [[r["date"], *[r[s] for s in order], ";".join(r.get("filled", []))] for r in c["rates"]["rows"]]
    return cols, rows


def metadata_table(c: dict) -> tuple[list[str], list[list]]:
    idx = model_index(c)
    cols = ["series_id", "name", "description", "pair", "base_currency", "quote_currency", "base_country",
            "quote_country", "quote_unit", "series_kind", "official_or_calculated", "rate_type", "method",
            "numerator_series", "denominator_series", "formula", "relations", "primary", "date_basis",
            "decimals", "provider", "institution", "country", "source_series_id", "source_docs_url",
            "verified_against", "root_providers", "plausible_min", "plausible_max", "max_daily_change_pct",
            "max_fill_days", "stale_after_days", "first_date", "last_date", "last_official_date",
            "observation_count", "official_count", "filled_count", "status", "status_error"]
    rows = []
    for sid in series_order(c):
        s = idx["series"][sid]
        cov = idx["coverage"][sid]
        src = s.get("source") or {}
        der = s.get("derivation") or {}
        th = s["thresholds"]
        cur = idx["currencies"]
        rows.append([
            sid, s["name"]["en"], s["description"]["en"], s["pair"], s["base_currency"], s["quote_currency"],
            cur[s["base_currency"]]["country"], cur[s["quote_currency"]]["country"], s["quote_unit"],
            s["kind"], "official" if s["kind"] == "official" else "calculated", s["rate_type"], s["method"],
            der.get("numerator"), der.get("denominator"),
            f"{der['numerator']} / {der['denominator']}" if der else None,
            ";".join(f"{r['type']}:{r['target']}" for r in s.get("relations", [])) or None,
            s["primary"], s["date_basis"], s["decimals"], src.get("provider"), src.get("institution"),
            src.get("country"), src.get("series_id"), src.get("docs_url"),
            (src.get("verified_against") or {}).get("provider"),
            ";".join(idx["lineage"][sid]["root_providers"]),
            th["plausible_range"][0], th["plausible_range"][1], th["max_daily_change_pct"],
            th.get("max_fill_days"), th["stale_after_days"], cov["first_date"], cov["last_date"],
            cov["last_official_date"], cov["observations"], cov["official_observations"],
            cov["filled_observations"], c["manifest"]["series_status"][sid], c["manifest"]["errors"].get(sid),
        ])
    return cols, rows


def snapshot_table(c: dict) -> tuple[list[str], list[list]]:
    idx = model_index(c)
    cols = ["as_of", "series_id", "pair", "series_kind", "primary", "date_basis", "status", "value", "date",
            "days_since_official", "prev_value", "prev_date", "change_abs", "change_pct", "next_date",
            "next_value", "fallback_used", "error"]
    rows = []
    for sid in series_order(c):
        e = c["latest"]["series"][sid]
        nxt = e.get("next") or {}
        rows.append([c["latest"]["as_of"], sid, e["pair"], idx["series"][sid]["kind"], e["primary"],
                     e["date_basis"], e["status"], e.get("value"), e.get("date"), e.get("days_since_official"),
                     e.get("prev_value"), e.get("prev_date"), e.get("change_abs"), e.get("change_pct"),
                     nxt.get("date"), nxt.get("value"), (e.get("source") or {}).get("fallback_used"),
                     e.get("error")])
    return cols, rows


def write_csv(path: Path, cols: list[str], rows: list[list]) -> None:
    with path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        for r in rows:
            w.writerow([fmt(v) for v in r])


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8", newline="") as fh:
        rd = csv.DictReader(fh)
        return list(rd.fieldnames or []), list(rd)


# ------------------------------------------------------------------ reconciliación

def num(s: str) -> float | None:
    return float(s) if s != "" else None


def reconcile(out: Path, c: dict) -> dict[str, list[str]]:
    """Relee los CSV escritos y los compara celda a celda con el JSON canónico."""
    res = {"export": [], "filled": [], "semantic": [], "coverage": []}
    order = series_order(c)
    idx = model_index(c)

    _, daily = read_csv(out / DAILY)
    seen = set()
    counts = {s: [0, 0] for s in order}
    canon = {(r["date"], s): (r[s], s in set(r.get("filled", []))) for r in c["rates"]["rows"] for s in order
             if r[s] is not None}
    for r in daily:
        key = (r["date"], r["series_id"])
        if key in seen:
            res["export"].append(f"{DAILY}: clave repetida {key}")
        seen.add(key)
        if key not in canon:
            res["export"].append(f"{DAILY}: fila sin origen canónico {key}")
            continue
        v, f = canon[key]
        if float(r["value"]) != v:
            res["export"].append(f"{DAILY} {key}: {r['value']} ≠ {v!r}")
        if r["filled"] not in (TRUE, FALSE) or (r["filled"] == TRUE) != f:
            res["filled"].append(f"{DAILY} {key}: filled={r['filled']} ≠ {f}")
        s = idx["series"][r["series_id"]]
        if (r["series_kind"], r["rate_type"], r["date_basis"], r["primary"]) != (
                s["kind"], s["rate_type"], s["date_basis"], fmt(s["primary"])):
            res["semantic"].append(f"{DAILY} {key}: atributos ≠ semantic_model.json")
        counts[r["series_id"]][0] += 1
        counts[r["series_id"]][1] += r["filled"] == TRUE
    missing = set(canon) - seen
    if missing:
        res["export"].append(f"{DAILY}: faltan {len(missing)} observaciones canónicas, p. ej. {sorted(missing)[:3]}")
    for sid in order:
        cov = idx["coverage"][sid]
        if counts[sid] != [cov["observations"], cov["filled_observations"]]:
            res["coverage"].append(f"{sid}: {DAILY} tiene {counts[sid][0]} filas ({counts[sid][1]} filled); "
                                   f"coverage dice {cov['observations']} ({cov['filled_observations']} filled)")
        if counts[sid][0] == 0 and c["manifest"]["series_status"][sid] != "error:empty":
            res["coverage"].append(f"{sid}: sin filas en {DAILY} y su estado no es error:empty")

    cols, wide = read_csv(out / WIDE)
    if cols != ["date", *order, "filled_series"]:
        res["export"].append(f"{WIDE}: columnas ≠ series de rates_daily.json")
    if len(wide) != len(c["rates"]["rows"]):
        res["export"].append(f"{WIDE}: {len(wide)} filas ≠ {len(c['rates']['rows'])} de rates_daily.json")
    for r, j in zip(wide, c["rates"]["rows"]):
        if r["date"] != j["date"] or any(num(r[s]) != j[s] for s in order):
            res["export"].append(f"{WIDE} {j['date']}: valores ≠ rates_daily.json")
        if (r["filled_series"].split(";") if r["filled_series"] else []) != j.get("filled", []):
            res["filled"].append(f"{WIDE} {j['date']}: filled_series ≠ rates_daily.json")

    _, meta = read_csv(out / META)
    meta_ids = [r["series_id"] for r in meta]
    if meta_ids != order:
        res["semantic"].append(f"{META}: series ≠ rates_daily.json")
    if set(r["series_id"] for r in daily) - set(meta_ids):
        res["semantic"].append(f"{DAILY}: series sin fila en {META}")
    for r in meta:
        if r["status"] != c["manifest"]["series_status"].get(r["series_id"]):
            res["semantic"].append(f"{META} {r['series_id']}: status ≠ manifest.json")
        if (r["series_kind"] == "cross") != (r["official_or_calculated"] == "calculated"):
            res["semantic"].append(f"{META} {r['series_id']}: official_or_calculated incoherente")

    _, snap = read_csv(out / SNAP)
    if [r["series_id"] for r in snap] != order:
        res["semantic"].append(f"{SNAP}: series ≠ latest.json")
    for r in snap:
        e = c["latest"]["series"][r["series_id"]]
        if r["status"] != e["status"] or r["status"] != c["manifest"]["series_status"][r["series_id"]]:
            res["semantic"].append(f"{SNAP} {r['series_id']}: status ≠ latest.json/manifest.json")
        if num(r["value"]) != e.get("value") or (r["date"] or None) != e.get("date"):
            res["export"].append(f"{SNAP} {r['series_id']}: value/date ≠ latest.json")

    for name in ("semantic_model.json", "manifest.json"):
        if (out / name).read_bytes() != c["raw"][name]:
            res["export"].append(f"{name}: la copia no es idéntica al canónico")
    return res


def check_crosses(c: dict) -> list[str]:
    """Verificación independiente: cada cruzada publicada = round_js(num/den, decimals) el mismo día,
    y filled = filled(num) or filled(den). No reemplaza ningún valor: solo lo compara."""
    errs = []
    idx = model_index(c)
    for sid in series_order(c):
        s = idx["series"][sid]
        if s["kind"] != "cross":
            continue
        n, d, dec = s["derivation"]["numerator"], s["derivation"]["denominator"], s["decimals"]
        if idx["series"][n]["date_basis"] != s["date_basis"] or idx["series"][d]["date_basis"] != s["date_basis"]:
            errs.append(f"{sid}: insumos con date_basis distinto")
        bad = 0
        for row in c["rates"]["rows"]:
            a, b, v = row[n], row[d], row[sid]
            filled = set(row.get("filled", []))
            exp = transform.round_js(a / b, dec) if a is not None and b not in (None, 0) else None
            if v != exp or (v is not None and (sid in filled) != (n in filled or d in filled)):
                bad += 1
                if bad <= 3:
                    errs.append(f"{sid} {row['date']}: publicado {v!r}, verificado {exp!r}")
        if bad > 3:
            errs.append(f"{sid}: {bad} fechas no reconcilian en total")
    return errs


def check_against_previous(prev_dir: Path, out: Path) -> list[str]:
    """Pérdida inesperada de histórico frente a la versión publicada antes (su fx_series_metadata.csv)."""
    p = prev_dir / META
    if not p.exists():
        return []
    _, prev = read_csv(p)
    _, cur = read_csv(out / META)
    cur_by = {r["series_id"]: r for r in cur}
    errs = []
    for r in prev:
        now = cur_by.get(r["series_id"])
        if now is None:
            errs.append(f"{r['series_id']}: estaba en la versión anterior y desapareció")
            continue
        if int(now["official_count"]) < int(r["official_count"]):
            errs.append(f"{r['series_id']}: observaciones oficiales {r['official_count']} → {now['official_count']}")
        if r["first_date"] and (not now["first_date"] or now["first_date"] > r["first_date"]):
            errs.append(f"{r['series_id']}: first_date {r['first_date']} → {now['first_date']}")
    return errs


# ------------------------------------------------------------------ frescura y licencias

def freshness(c: dict) -> dict:
    m = c["manifest"]
    idx = model_index(c)
    as_of = date.fromisoformat(m["as_of"])
    st = m["series_status"]
    issues: dict[str, dict] = {}
    for sid in series_order(c):
        s = idx["series"][sid]
        if s["kind"] != "official" or st[sid] == "ok":
            continue
        p = s["source"]["provider"]
        it = issues.setdefault(p, {"provider": p, "institution": s["source"]["institution"], "statuses": set(),
                                   "series": [], "affected_crosses": [], "errors": [],
                                   "last_official_date": None})
        it["statuses"].add(st[sid])
        it["series"].append(sid)
        if m["errors"].get(sid):
            it["errors"].append(m["errors"][sid])
        lo = idx["coverage"][sid]["last_official_date"]
        it["last_official_date"] = max(filter(None, [it["last_official_date"], lo]), default=None)
    for sid in series_order(c):
        s = idx["series"][sid]
        if s["kind"] == "cross" and st[sid] != "ok":
            for p in idx["lineage"][sid]["root_providers"]:
                if p in issues:
                    issues[p]["affected_crosses"].append(sid)
    recent = []
    for sid, ws in m.get("warnings", {}).items():
        for w in ws:
            dates = re.findall(r"\d{4}-\d{2}-\d{2}", w)
            if dates and max(date.fromisoformat(x) for x in dates) >= as_of - timedelta(days=30):
                recent.append(f"{sid}: {w}")
    counts = {"ok": 0, "stale": 0, "error": 0}
    for v in st.values():
        counts["ok" if v == "ok" else "stale" if v == "stale" else "error"] += 1
    return {
        "overall_status": m["overall_status"],
        "dashboard_status": c["latest"]["overall_status"],
        "as_of": m["as_of"],
        "generated_at": m["generated_at"],
        "series_ok": counts["ok"], "series_stale": counts["stale"], "series_error": counts["error"],
        "provider_issues": [{**v, "statuses": sorted(v["statuses"])} for v in issues.values()],
        "historical_warning_count": sum(len(v) for v in m.get("warnings", {}).values()),
        "recent_warnings": recent,
        "level": "OK" if m["overall_status"] == "ok" else "WARN",
    }


def license_review(c: dict) -> dict:
    lic = c["licenses"]
    idx = model_index(c)
    providers = c["config"]["providers"]
    resolved = {(r["provider"], r.get("issue")) for r in lic.get("resolutions", [])}
    blockers, incomplete = [], []
    for p in providers:
        e = lic["providers"].get(p)
        if not e:
            incomplete.append({"provider": p, "issue": "missing", "detail": "Sin revisión de condiciones en kaggle/licenses.json"})
            continue
        if e["license_status"] != "VERIFIED":
            incomplete.append({"provider": p, "issue": "unverified", "detail": e.get("notes", "")})
        if e["redistribution"] in ("prohibited_without_authorization", "unknown") and (p, "redistribution") not in resolved:
            blockers.append({"provider": p, "issue": "redistribution",
                             "series": [s for s in series_order(c) if p in idx["lineage"][s]["root_providers"]],
                             "detail": e["notes"]})
    # Combinaciones: una cruzada con un insumo share-alike (ODbL, CC BY-SA) y otro que exige autorización
    # para uso comercial no puede ofrecerse a la vez bajo la licencia share-alike y con esa restricción.
    conflicts: dict[tuple, list] = {}
    for sid in series_order(c):
        roots = idx["lineage"][sid]["root_providers"]
        sa = [p for p in roots if lic["providers"].get(p, {}).get("share_alike")]
        nc = [p for p in roots if lic["providers"].get(p, {}).get("commercial_reuse")
              in ("requires_authorization", "prohibited_without_authorization")]
        if sa and nc and len(roots) > 1:
            conflicts.setdefault((tuple(sa), tuple(nc)), []).append(sid)
    for (sa, nc), sids in conflicts.items():
        key = (",".join(sa + nc), "share_alike_vs_non_commercial")
        if key in resolved:
            continue
        blockers.append({"provider": ",".join(sa + nc), "issue": "share_alike_vs_non_commercial", "series": sids,
                         "detail": f"{', '.join(sa)} require(s) share-alike for derivative databases; "
                                   f"{', '.join(nc)} require(s) prior authorization for commercial use. "
                                   "A calculated series combining both cannot satisfy both sets of terms at once "
                                   "without an authorization or a legal assessment."})
    status = "BLOCKED" if blockers else "INCOMPLETE" if incomplete else "PASS"
    return {"status": status, "blockers": blockers, "incomplete": incomplete, "kaggle_license": lic["kaggle_license"]}


# ------------------------------------------------------------------ procedencia

def git(*args: str) -> str | None:
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def provenance(c: dict) -> dict:
    sha = os.environ.get("GITHUB_SHA") or git("rev-parse", "HEAD")
    dirty = git("status", "--porcelain", "--", "data", "config")
    return {
        "repository": c["model"]["model"]["repository"],
        "commit_sha": sha,
        "commit_date": git("show", "-s", "--format=%cI", sha) if sha else None,
        "canonical_inputs_clean": dirty == "",
        "schema_version": c["manifest"]["schema_version"],
        "config_version": c["manifest"]["config_version"],
        "semantic_model_version": c["model"]["semantic_model_version"],
        "data_as_of": c["manifest"]["as_of"],
        "pipeline_generated_at": c["manifest"]["generated_at"],
        "data_hash": c["manifest"]["data_hash"],
    }


def package_hash(out: Path) -> str:
    h = hashlib.sha256()
    for name in DATA_FILES:
        h.update(name.encode())
        h.update((out / name).read_bytes())
    return h.hexdigest()


def read_kv(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if ":" in line and not line.startswith("#"):
                k, v = line.split(":", 1)
                out[k.strip()] = v.strip()
    return out


def version_message(c: dict, prov: dict, fresh: dict) -> str:
    sha7 = (prov["commit_sha"] or "unknown")[:7]
    head = f"FX data through {fresh['as_of']}"
    if fresh["provider_issues"]:
        parts = [f"{i['provider'].upper()} refresh degraded ({'/'.join(i['statuses'])})" for i in fresh["provider_issues"]]
        return f"{head} · {' · '.join(parts)} · historical data retained · source commit {sha7}"
    return f"{head} · schema {prov['schema_version']} · config {prov['config_version']} · source commit {sha7}"


# ------------------------------------------------------------------ documentación

def md_table(cols: list[str], rows: list[list]) -> str:
    def cell(v):
        return fmt(v).replace("|", "\\|").replace("\n", " ") if v is not None else "—"
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols),
                      *["| " + " | ".join(cell(v) for v in r) + " |" for r in rows]])


def column_specs(c: dict) -> dict[str, list[dict]]:
    """Diccionario de columnas. Las definiciones salen de semantic_model.json (fields y domains) siempre
    que el modelo tenga el campo; solo las columnas propias de esta capa llevan texto aquí."""
    idx = model_index(c)
    F, D = idx["fields"], idx["domains"]

    def sem(fid: str, extra: str = "") -> str:
        return (F[fid]["description"]["en"] + (" " + extra if extra else "")).strip()

    def dom(key: str) -> str:
        return ", ".join(f"`{d['value']}`" for d in D[key])

    def meaning(key: str) -> str:
        return " ".join(f"`{d['value']}`: {d['meaning']['en']}" for d in D[key])

    def col(name, typ, nullable, definition, source, origin, unit="—", allowed="—"):
        return dict(name=name, type=typ, nullable=nullable, unit=unit, definition=definition, source=source,
                    origin=origin, allowed=allowed)

    ids = f"{len(series_order(c))} ids, see `{META}`"
    common = {
        "series_id": col("series_id", "string", "no", sem("series_id", "Joins every file to the metadata."),
                         "semantic_model.json#dimensions.series[].id", "original", allowed=ids),
        "pair": col("pair", "string", "no", sem("pair", "`USD-MXN = 18.2` means 18.2 MXN per 1 USD."),
                    "rates_daily.json#series.*.pair", "original"),
        "base_currency": col("base_currency", "string (ISO 4217)", "no", "Base currency of the pair (1 unit).",
                             "semantic_model.json#dimensions.series[].base_currency", "original",
                             allowed=", ".join(f"`{x}`" for x in idx["currencies"])),
        "quote_currency": col("quote_currency", "string (ISO 4217)", "no",
                              "Quote currency: the value is an amount of this currency per 1 unit of the base.",
                              "semantic_model.json#dimensions.series[].quote_currency", "original",
                              allowed=", ".join(f"`{x}`" for x in idx["currencies"])),
        "series_kind": col("series_kind", "string (enum)", "no", meaning("series_kind"),
                           "semantic_model.json#dimensions.series[].kind (domain: domains.series_kind)",
                           "original", allowed=dom("series_kind")),
        "rate_type": col("rate_type", "string (enum)", "no", meaning("rate_type"),
                         "semantic_model.json#dimensions.series[].rate_type", "original", allowed=dom("rate_type")),
        "date_basis": col("date_basis", "string (enum)", "no", sem("date_basis") + " " + meaning("date_basis"),
                          "rates_daily.json#series.*.date_basis", "original", allowed=dom("date_basis")),
        "primary": col("primary", "boolean", "no", sem("primary", "Exactly one primary series per pair; "
                       "`false` marks hidden variants (market-day re-indexing or settlement view)."),
                       "rates_daily.json#series.*.primary", "original", allowed="`true`, `false`"),
        "status": col("status", "string (enum)", "no", sem("status") + " " + meaning("status"),
                      "manifest.json#series_status", "original", allowed=dom("status")),
    }
    return {
        DAILY: [
            col("date", "date (YYYY-MM-DD)", "no", sem("row_date", "Its meaning depends on `date_basis`. "
                "Rows after the snapshot `as_of` are official values already published for a future date."),
                "rates_daily.json#rows[].date", "original"),
            common["series_id"], common["pair"], common["base_currency"], common["quote_currency"],
            col("value", "float", "no", "Value of the series that day (official, or filled when `filled` is "
                "true). Copied without re-rounding. Rows where the canonical value is null are omitted.",
                "rates_daily.json#rows[].<series_id>", "original", unit="quote_currency per 1 base_currency",
                allowed="> 0"),
            col("filled", "boolean", "no", "true when the pipeline repeated the last official value because the "
                "source did not publish that day; false for an official observation. "
                + c["model"]["conventions"][[x["id"] for x in c["model"]["conventions"]].index("filled_flag")]["rule"]["en"]
                + " A cross rate is filled if either input is.",
                "rates_daily.json#rows[].filled (membership)", "derived (format only)", allowed="`true`, `false`"),
            common["series_kind"], common["rate_type"], common["date_basis"], common["primary"],
        ],
        WIDE: [
            col("date", "date (YYYY-MM-DD)", "no", sem("row_date"), "rates_daily.json#rows[].date", "original"),
            col("<series_id>", "float", "yes", sem("row_value") + " One column per series, in configuration order.",
                "rates_daily.json#rows[].<series_id>", "original", unit="see quote_unit in metadata",
                allowed="> 0 or empty"),
            col("filled_series", "string", "yes", "Semicolon-separated ids of the series whose value in this row is "
                "filled (`" + sem("row_filled") + "`). Empty = every value in the row is official.",
                "rates_daily.json#rows[].filled", "derived (format only)", allowed=ids),
        ],
        META: [
            common["series_id"],
            col("name", "string", "no", "Curated English name.", "semantic_model.json#dimensions.series[].name.en", "original"),
            col("description", "string", "no", "Curated English description.",
                "semantic_model.json#dimensions.series[].description.en", "original"),
            common["pair"], common["base_currency"], common["quote_currency"],
            col("base_country", "string (ISO 3166-1 alpha-2)", "no", "Country of the base currency.",
                "semantic_model.json#dimensions.currencies[].country", "original"),
            col("quote_country", "string (ISO 3166-1 alpha-2)", "no", "Country of the quote currency.",
                "semantic_model.json#dimensions.currencies[].country", "original"),
            col("quote_unit", "string", "no", sem("quote_unit"), "rates_daily.json#series.*.quote_unit", "original"),
            common["series_kind"],
            col("official_or_calculated", "string (enum)", "no", "Plain-language form of `series_kind`: "
                "`official` (published by the source institution) or `calculated` (cross rate computed by this "
                "project; not published by any central bank).", "series_kind", "derived (relabel)",
                allowed="`official`, `calculated`"),
            common["rate_type"],
            col("method", "string", "no", sem("method"), "rates_daily.json#series.*.method", "original"),
            col("numerator_series", "string", "yes", "Cross rates only: series id of the numerator (quote currency per USD).",
                "semantic_model.json#dimensions.series[].derivation.numerator", "original"),
            col("denominator_series", "string", "yes", "Cross rates only: series id of the denominator (base currency per USD).",
                "semantic_model.json#dimensions.series[].derivation.denominator", "original"),
            col("formula", "string", "yes", "Cross rates only: `numerator / denominator` on the same date, rounded to "
                "`decimals` with round_js (JavaScript `toFixed` semantics). Canonical values are published as-is.",
                "semantic_model.json#dimensions.series[].derivation", "derived (format only)"),
            col("relations", "string", "yes", "Semicolon-separated `type:target` relations, e.g. "
                "`market_day_variant_of:usd_cop_trm`.", "semantic_model.json#dimensions.series[].relations", "original"),
            common["primary"], common["date_basis"],
            col("decimals", "integer", "no", sem("decimals"), "rates_daily.json#series.*.decimals", "original"),
            col("provider", "string", "yes", sem("source_provider") + " Empty for cross rates.",
                "rates_daily.json#series.*.source.provider", "original", allowed=", ".join(f"`{p}`" for p in idx["providers"])),
            col("institution", "string", "yes", sem("source_institution"), "rates_daily.json#series.*.source.institution", "original"),
            col("country", "string", "yes", sem("source_country"), "rates_daily.json#series.*.source.country", "original"),
            col("source_series_id", "string", "yes", sem("source_series_id"), "rates_daily.json#series.*.source.series_id", "original"),
            col("source_docs_url", "string (uri)", "yes", sem("source_docs_url"), "rates_daily.json#series.*.source.docs_url", "original"),
            col("verified_against", "string", "yes", "Provider of the second official source used to verify each run "
                "and as declared fallback (`sfc_trm` for the TRM).", "rates_daily.json#series.*.source.verified_against.provider", "original"),
            col("root_providers", "string", "no", "Semicolon-separated providers the series ultimately depends on "
                "(for a cross rate, the providers of both inputs). Use it to apply each source's terms.",
                "semantic_model.json#lineage.series[].root_providers", "original"),
            col("plausible_min", "float", "no", "Lower bound of `plausible_range` (quality rule `plausible_range`).",
                "semantic_model.json#dimensions.series[].thresholds.plausible_range[0]", "original", unit="quote_unit"),
            col("plausible_max", "float", "no", "Upper bound of `plausible_range`.",
                "semantic_model.json#dimensions.series[].thresholds.plausible_range[1]", "original", unit="quote_unit"),
            col("max_daily_change_pct", "float", "no", "Day-to-day change above this raises a `salto` warning (quality rule `daily_jump`).",
                "semantic_model.json#dimensions.series[].thresholds.max_daily_change_pct", "original", unit="percent"),
            col("max_fill_days", "integer", "yes", "Maximum consecutive days an official value is carried forward "
                "(official series only; a cross rate inherits its inputs' filling).",
                "semantic_model.json#dimensions.series[].thresholds.max_fill_days", "original", unit="days"),
            col("stale_after_days", "integer", "no", "Status becomes `stale` when the last official value is older than this.",
                "semantic_model.json#dimensions.series[].thresholds.stale_after_days", "original", unit="days"),
            col("first_date", "date", "yes", sem("first_date"), "semantic_model.json#coverage.series[].first_date", "original"),
            col("last_date", "date", "yes", sem("last_date"), "semantic_model.json#coverage.series[].last_date", "original"),
            col("last_official_date", "date", "yes", sem("last_official_date"),
                "semantic_model.json#coverage.series[].last_official_date", "original"),
            col("observation_count", "integer", "no", f"Rows of the series in `{DAILY}` (official + filled).",
                "semantic_model.json#coverage.series[].observations", "original"),
            col("official_count", "integer", "no", "Official (not filled) observations.",
                "semantic_model.json#coverage.series[].official_observations", "original"),
            col("filled_count", "integer", "no", "Filled observations.",
                "semantic_model.json#coverage.series[].filled_observations", "original"),
            common["status"],
            col("status_error", "string", "yes", sem("error"), "manifest.json#errors.<series_id>", "original"),
        ],
        SNAP: [
            col("as_of", "date", "no", sem("as_of"), "latest.json#as_of", "original"),
            common["series_id"], common["pair"], common["series_kind"], common["primary"], common["date_basis"],
            common["status"],
            col("value", "float", "yes", sem("value") + " For a cross rate: the last day on which both inputs are official.",
                "latest.json#series.*.value", "original", unit="quote_unit"),
            col("date", "date", "yes", sem("date"), "latest.json#series.*.date", "original"),
            col("days_since_official", "integer", "yes", sem("days_since_official"), "latest.json#series.*.days_since_official", "original", unit="days"),
            col("prev_value", "float", "yes", sem("prev_value"), "latest.json#series.*.prev_value", "original", unit="quote_unit"),
            col("prev_date", "date", "yes", sem("prev_date"), "latest.json#series.*.prev_date", "original"),
            col("change_abs", "float", "yes", sem("change_abs"), "latest.json#series.*.change_abs", "original", unit="quote_unit"),
            col("change_pct", "float", "yes", sem("change_pct"), "latest.json#series.*.change_pct", "original", unit="percent"),
            col("next_date", "date", "yes", sem("next_date") + " Typically the TRM or the FIX for payments, published ahead of their effective date.",
                "latest.json#series.*.next.date", "original"),
            col("next_value", "float", "yes", sem("next_value"), "latest.json#series.*.next.value", "original", unit="quote_unit"),
            col("fallback_used", "boolean", "yes", sem("fallback_used") + " Empty for series without a declared fallback.",
                "latest.json#series.*.source.fallback_used", "original", allowed="`true`, `false`, empty"),
            col("error", "string", "yes", sem("error"), "latest.json#series.*.error", "original"),
        ],
    }


def render_dictionary(specs: dict, stats: dict) -> str:
    parts = ["# Data dictionary", "",
             "Generated by `scripts/build_kaggle_dataset.py`. Definitions are taken from `semantic_model.json` "
             "(`fields` and `domains`) wherever the model defines the field; `source` gives the canonical location. "
             "`origin`: `original` = copied from the canonical file; `derived (format only)` = reshaped without "
             "changing meaning or value.", "",
             "Booleans are written `true`/`false`; missing values are empty cells. Numbers are written with the "
             "shortest representation that round-trips the canonical value (no extra rounding).", ""]
    for name, cols in specs.items():
        st = stats[name]
        parts += [f"## `{name}`", "", f"{st['rows']:,} rows × {st['columns']} columns.", "",
                  md_table(["name", "type", "nullable", "unit", "definition", "source", "origin", "allowed values"],
                           [[f"`{x['name']}`", x["type"], x["nullable"], x["unit"], x["definition"], f"`{x['source']}`",
                             x["origin"], x["allowed"]] for x in cols]), ""]
    parts += ["## `semantic_model.json` and `manifest.json`", "",
              "Byte-identical copies of the canonical files. Their own fields are documented inside "
              "`semantic_model.json` (`fields`) and in the repository's `docs/CONTRATO_JSON.md`.", ""]
    return "\n".join(parts)


def render_sources(c: dict, lr: dict) -> str:
    lic = c["licenses"]
    idx = model_index(c)
    rows = []
    for p, pc in c["config"]["providers"].items():
        e = lic["providers"].get(p, {})
        prov = idx["providers"][p]
        series = [s for s in series_order(c) if p in idx["lineage"][s]["root_providers"]]
        rows.append([f"`{p}`", pc["institution"], pc["country"], prov["role"], pc["docs_url"], e.get("terms_url"),
                     e.get("license", "UNVERIFIED"), e.get("license_status", "UNVERIFIED"), e.get("redistribution"),
                     e.get("attribution"), e.get("commercial_reuse"), e.get("derived_works"),
                     f"{len(series)} series" if series else "verification/fallback only"])
    detail = []
    for p, pc in c["config"]["providers"].items():
        e = lic["providers"].get(p, {})
        detail += [f"### {pc['institution']} (`{p}`, {pc['country']})", "",
                   f"- **Terms:** {e.get('terms_url', 'UNVERIFIED')} — {e.get('license_status', 'UNVERIFIED')}",
                   f"- **Redistribution:** `{e.get('redistribution')}` · **Commercial reuse:** `{e.get('commercial_reuse')}` · "
                   f"**Derived works:** `{e.get('derived_works')}` · **Share-alike:** `{e.get('share_alike')}`",
                   f"- **Attribution:** {e.get('attribution')}",
                   f"- **Notes:** {e.get('notes')}",
                   f"- **Series that depend on it:** " + (", ".join(
                       f"`{s}`" for s in series_order(c) if p in idx["lineage"][s]["root_providers"]) or "none (verification/fallback)"), ""]
    blockers = "\n".join(f"- **{b['provider']}** · `{b['issue']}` · series: {', '.join(f'`{s}`' for s in b['series'])}"
                         for b in lr["blockers"]) or "None."
    return "\n".join([
        "# Sources and licences", "",
        f"Review date: {lic['review_date']}. {lic['reviewed_by']}. This is a documentary review, not legal advice.", "",
        f"**Kaggle licence field: `{lic['kaggle_license']}`.** {lic['kaggle_license_rationale']}", "",
        "## Official source data", "",
        md_table(["provider", "institution", "country", "role", "source URL", "terms URL", "licence / terms", "status",
                  "redistribution", "attribution", "commercial reuse", "derived works", "used by"], rows), "",
        "## Calculated cross rates", "",
        "Cross rates (`series_kind = cross`) are calculations made by this project from two official series. They "
        "are **not** published by any central bank. Calculating them does **not** remove the rights or attribution "
        "requirements of the institutions whose data enter the calculation: each cross rate is subject to the terms of "
        f"every provider in its `root_providers` (see `{META}`).", "",
        "## Licence review result", "",
        f"**{lr['status']}**", "", blockers, "",
        "## Detail per institution", "", *detail,
    ])


def render_blockers(c: dict, lr: dict) -> str:
    out = ["# Publication blockers", "",
           "Public publication on Kaggle is **blocked** until each item below is resolved. The package can still "
           "be built and kept as a **private** Kaggle dataset. No country or series has been removed to avoid the "
           "problem; resolving it is a decision for the repository owner.", ""]
    for i, b in enumerate(lr["blockers"], 1):
        out += [f"## {i}. `{b['provider']}` — {b['issue']}", "", b["detail"], "",
                "Affected series: " + ", ".join(f"`{s}`" for s in b["series"]), "",
                "How to resolve: obtain written authorization from the institution(s) (or a legal assessment that "
                "the terms do not apply) and record it in `kaggle/licenses.json` → `resolutions` as "
                f"`{{\"provider\": \"{b['provider']}\", \"issue\": \"{b['issue']}\", \"reference\": \"…\"}}`. "
                "Alternatively, the owner may decide explicitly to exclude the affected series from the Kaggle "
                "layer; that is a documented scope change, not something the builder does on its own.", ""]
    out += ["Note: the same canonical files are already public in the GitHub repository and on the dashboard; the "
            "terms above apply to that distribution as well.", ""]
    return "\n".join(out)


def render_freshness(fr: dict) -> str:
    lines = ["FRESHNESS REPORT", "",
             f"overall_status: {fr['overall_status']} (all series; dashboard/primary scope: {fr['dashboard_status']})",
             f"as_of: {fr['as_of']}", f"generated_at: {fr['generated_at']}", "",
             f"series_ok: {fr['series_ok']}", f"series_stale: {fr['series_stale']}", f"series_error: {fr['series_error']}", "",
             "provider_errors:"]
    if not fr["provider_issues"]:
        lines.append("  none")
    for i in fr["provider_issues"]:
        lines += [f"  - {i['provider']} ({i['institution']}): {', '.join(i['statuses'])}; "
                  f"last official {i['last_official_date']}; series {', '.join(i['series'])}; "
                  f"{len(i['affected_crosses'])} dependent cross rates also degraded; history retained"]
        lines += [f"    {e}" for e in i["errors"]]
    lines += ["", f"warnings: {fr['historical_warning_count']} historical jump/revision warnings in manifest.json "
              f"(informational; they do not degrade a series)"]
    if fr["recent_warnings"]:
        lines.append("  recent (last 30 days):")
        lines += [f"  - {w}" for w in fr["recent_warnings"]]
    return "\n".join(lines)


def render_methodology(c: dict) -> str:
    m = c["model"]
    tpl = Template((KAGGLE_SRC / "METHODOLOGY.template.md").read_text(encoding="utf-8"))
    stages = md_table(["#", "stage", "what it does", "implemented in"],
                      [[s["order"], s["name"]["en"], s["description"]["en"], f"`{s['implemented_in']}`"]
                       for s in m["lineage"]["stages"]])
    rules = md_table(["rule", "severity", "consequence", "threshold", "check"],
                     [[f"`{r['id']}`", r["severity"], r["outcome"], f"`{r['threshold_key']}`" if r.get("threshold_key") else "—",
                       r["check"]["en"]] for r in m["quality"]["rules"]])
    conventions = "\n".join(f"- **`{x['id']}`** — {x['rule']['en']}" for x in m["conventions"])
    metrics = md_table(["metric", "definition", "computed in"],
                       [[x["name"]["en"], x["definition"]["en"], f"`{x['implemented_in']}`"] for x in m["metrics"]])
    guidance = "\n".join(f"- {x['rule']['en']}" for x in m["usage_guidance"])
    text = tpl.substitute(stages=stages, quality_rules=rules, conventions=conventions, metrics=metrics,
                          usage_guidance=guidance, status_inheritance=m["domains"]["status_inheritance"]["en"],
                          date_basis_rule=m["domains"]["date_basis_rule"]["en"],
                          semantic_model_version=m["semantic_model_version"])
    return text


def render_readme(c: dict, ctx: dict) -> str:
    idx = model_index(c)
    order = series_order(c)
    off = [s for s in order if idx["series"][s]["kind"] == "official"]
    crs = [s for s in order if idx["series"][s]["kind"] == "cross"]
    st = c["manifest"]["series_status"]
    countries = [x for x in c["model"]["dimensions"]["countries"] if x["providers"]]
    official_tbl = md_table(
        ["series_id", "pair", "what it is", "date_basis", "rate_type", "primary", "institution · code", "coverage", "status"],
        [[f"`{s}`", idx["series"][s]["pair"], idx["series"][s]["name"]["en"], idx["series"][s]["date_basis"],
          idx["series"][s]["rate_type"], fmt(idx["series"][s]["primary"]),
          f"{idx['series'][s]['source']['institution']} · `{idx['series'][s]['source']['series_id']}`",
          f"{idx['coverage'][s]['first_date']} → {idx['coverage'][s]['last_official_date']}", f"`{st[s]}`"] for s in off])
    cross_tbl = md_table(
        ["series_id", "pair", "formula (same market day)", "unit", "status"],
        [[f"`{s}`", idx["series"][s]["pair"],
          f"`{idx['series'][s]['derivation']['numerator']}` ÷ `{idx['series'][s]['derivation']['denominator']}`",
          idx["series"][s]["quote_unit"], f"`{st[s]}`"] for s in crs])
    files_tbl = md_table(["file", "rows", "columns", "size", "what it is"],
                         [[f"`{f}`", f"{ctx['stats'][f]['rows']:,}" if "rows" in ctx["stats"][f] else "—",
                           ctx["stats"][f].get("columns", "—"), human(ctx["stats"][f]["bytes"]), desc]
                          for f, desc in ctx["file_descriptions"]])
    sources = "\n".join(f"- **{pc['institution']}** ({pc['country']}) — {c['licenses']['providers'][p]['attribution']}"
                        for p, pc in c["config"]["providers"].items())
    vigencia = [s for s in order if idx["series"][s]["date_basis"] == "vigencia"]
    variants = "; ".join(f"`{s}` ({', '.join(r['type'] + ' `' + r['target'] + '`' for r in idx['series'][s]['relations'])})"
                         for s in order if idx["series"][s].get("relations"))
    lp = c["licenses"]["providers"]

    def who(pred):
        return ", ".join(c["config"]["providers"][p]["institution"].split(" (")[0] for p in c["config"]["providers"]
                         if pred(lp.get(p, {})))

    summary = [f"{label}: {names}." for label, names in (
        ("Share-alike licences", who(lambda e: e.get("share_alike"))),
        ("Commercial use requires the institution's authorization",
         who(lambda e: e.get("commercial_reuse") in ("requires_authorization", "prohibited_without_authorization"))),
        ("Redistribution restricted without the institution's authorization",
         who(lambda e: e.get("redistribution") in ("prohibited_without_authorization", "unknown"))),
    ) if names]
    tpl = Template((KAGGLE_SRC / "README.template.md").read_text(encoding="utf-8"))
    text = tpl.substitute(
        licence_summary=" ".join(summary),
        title=ctx["title"], dataset_id=ctx["dataset_id"], repository=ctx["prov"]["repository"],
        dashboard_url=ctx["dashboard_url"], commit=ctx["prov"]["commit_sha"][:7] if ctx["prov"]["commit_sha"] else "unknown",
        schema_version=ctx["prov"]["schema_version"], config_version=ctx["prov"]["config_version"],
        semantic_model_version=ctx["prov"]["semantic_model_version"], as_of=c["manifest"]["as_of"],
        n_countries=len(countries), countries=", ".join(f"{x['name']['en']} ({x['id']})" for x in countries),
        n_currencies=len(idx["currencies"]), currencies=", ".join(f"`{x}`" for x in idx["currencies"]),
        n_pairs=len(c["model"]["dimensions"]["pairs"]), n_series=len(order), n_official=len(off), n_cross=len(crs),
        n_primary=sum(1 for s in order if idx["series"][s]["primary"]), first_date=ctx["date_range"][0],
        last_date=ctx["date_range"][1], n_daily=f"{ctx['stats'][DAILY]['rows']:,}",
        official_table=official_tbl, cross_table=cross_tbl, files_table=files_tbl,
        vigencia_series=", ".join(f"`{s}`" for s in vigencia), hidden_variants=variants,
        freshness=render_freshness(ctx["fresh"]), licence_status=ctx["license"]["status"],
        kaggle_license=c["licenses"]["kaggle_license"], sources_attribution=sources,
        year=c["manifest"]["as_of"][:4], filled_rule=c["model"]["conventions"][
            [x["id"] for x in c["model"]["conventions"]].index("filled_flag")]["rule"]["en"],
    )
    return text


def human(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{n / 1024:.0f} KB"


def kaggle_metadata(c: dict, ctx: dict, specs: dict, readme: str, visibility: str) -> dict:
    tpl = json.loads((KAGGLE_SRC / "dataset-metadata.template.json").read_text(encoding="utf-8"))
    res_desc = dict(ctx["file_descriptions"])
    resources = []
    for name, _ in ctx["file_descriptions"]:
        r = {"path": name, "description": res_desc[name]}
        if name in specs and name != WIDE:
            r["schema"] = {"fields": [{"name": x["name"], "description": x["definition"][:500],
                                       "type": {"float": "number", "integer": "integer", "boolean": "boolean"}.get(
                                           x["type"], "datetime" if x["type"].startswith("date") else "string")}
                                      for x in specs[name]]}
        resources.append(r)
    sources = [f"Built from {ctx['prov']['repository']} (commit {ctx['prov']['commit_sha']})."]
    for p, pc in c["config"]["providers"].items():
        sources.append(f"- {pc['institution']} ({pc['country']}): {pc['docs_url']} · terms: "
                       f"{c['licenses']['providers'][p]['terms_url']}")
    return {
        **tpl,
        "id": ctx["dataset_id"],
        "licenses": [{"name": c["licenses"]["kaggle_license"]}],
        "isPrivate": visibility != "public",
        "description": readme,
        "userSpecifiedSources": "\n".join(sources),
        "resources": resources,
    }


def check_kaggle_metadata(meta: dict, out: Path) -> list[str]:
    errs = []
    owner, _, slug = meta["id"].partition("/")
    if not owner or not 6 <= len(slug) <= 50 or not re.fullmatch(r"[a-z0-9-]+", slug):
        errs.append(f"id inválido: {meta['id']}")
    if not 6 <= len(meta["title"]) <= 50:
        errs.append(f"title debe tener 6-50 caracteres ({len(meta['title'])})")
    if not 20 <= len(meta.get("subtitle", "")) <= 80:
        errs.append(f"subtitle debe tener 20-80 caracteres ({len(meta.get('subtitle', ''))})")
    if len(meta["licenses"]) != 1:
        errs.append("Kaggle exige exactamente una licencia")
    if not isinstance(meta.get("isPrivate"), bool):
        errs.append("isPrivate debe ser booleano explícito (kaggle datasets metadata --update lo trata como false si falta)")
    for r in meta["resources"]:
        if not (out / r["path"]).exists():
            errs.append(f"resources: {r['path']} no existe")
    for k in ("token", "key", "secret"):
        if re.search(rf"\b{k}\s*[=:]\s*\S{{12,}}", json.dumps(meta), re.I):
            errs.append(f"metadata parece contener un {k}")
    return errs


def auth_status() -> str:
    home = Path.home()
    if os.environ.get("KAGGLE_API_TOKEN") or (os.environ.get("KAGGLE_USERNAME") and os.environ.get("KAGGLE_KEY")):
        return "READY"
    if any((home / ".kaggle" / f).exists() for f in ("access_token", "kaggle.json")) or \
            (home / ".config" / "kaggle" / "kaggle.json").exists():
        return "READY"
    return "MISSING"


# ------------------------------------------------------------------ main

def safe_clean(path: Path) -> None:
    path = path.resolve()
    if DIST.resolve() not in path.parents:
        raise SystemExit(f"Me niego a borrar {path}: solo se limpia dentro de {DIST}")
    if path.exists():
        shutil.rmtree(path)


def build(out: Path, dataset_id: str, visibility: str, compare_with: Path | None, allow_history_loss: bool) -> dict:
    c = load_canonical()
    tmp = out.parent / f".{out.name}.build"
    safe_clean(tmp)
    tmp.mkdir(parents=True)

    checks: dict[str, list[str]] = {}
    checks["contract"] = check_contract(c)
    schema_state, checks["schema"] = check_json_schemas(c)
    if checks["contract"]:
        raise ValidationFailed("\n".join(checks["contract"]))

    tables = {DAILY: daily_table(c), WIDE: wide_table(c), META: metadata_table(c), SNAP: snapshot_table(c)}
    for name, (cols, rows) in tables.items():
        write_csv(tmp / name, cols, rows)
    for name in ("semantic_model.json", "manifest.json"):
        (tmp / name).write_bytes(c["raw"][name])

    rec = reconcile(tmp, c)
    checks.update({f"reconcile_{k}": v for k, v in rec.items()})
    checks["cross"] = check_crosses(c)
    checks["history"] = check_against_previous(compare_with, tmp) if compare_with and not allow_history_loss else []

    fresh = freshness(c)
    lr = license_review(c)
    prov = provenance(c)
    if visibility == "public" and lr["status"] != "PASS":
        raise ValidationFailed(f"--visibility public requiere la compuerta de licencias en PASS (está en {lr['status']})")

    idx = model_index(c)
    dates = [r[0] for r in tables[DAILY][1]]
    stats = {name: {"rows": len(rows), "columns": len(cols), "bytes": (tmp / name).stat().st_size}
             for name, (cols, rows) in tables.items()}
    for name in ("semantic_model.json", "manifest.json"):
        stats[name] = {"bytes": (tmp / name).stat().st_size}
    tpl = json.loads((KAGGLE_SRC / "dataset-metadata.template.json").read_text(encoding="utf-8"))
    ctx = {
        "title": tpl["title"], "dataset_id": dataset_id, "prov": prov, "fresh": fresh, "license": lr, "stats": stats,
        "date_range": (min(dates), max(dates)),
        "dashboard_url": next((x["url"] for x in c["model"]["consumers"] if x.get("kind") == "web_dashboard"), None),
        "file_descriptions": [
            (DAILY, "Main analytical table (LONG): one row per series and calendar date with a value. Carries "
                    "`filled`, `series_kind`, `rate_type`, `date_basis` and `primary`, so official observations and "
                    "calculated cross rates can be filtered without joins. Recommended file."),
            (WIDE, "Convenience WIDE view of rates_daily.json: one row per calendar date, one numeric column per "
                   "series, plus `filled_series` listing the filled ids of that row."),
            (META, "One row per series: meaning, pair, kind (official/calculated), formula and inputs of each cross "
                   "rate, source institution and code, thresholds, coverage and current status."),
            (SNAP, "Latest official value per series from latest.json: date, previous record, change, next already-"
                   "published value, status (ok/stale/error) and error message."),
            ("semantic_model.json", "Canonical bilingual semantic model: series, pairs, currencies, countries, "
                                    "providers, lineage, quality rules, field dictionary, glossary, usage guidance."),
            ("manifest.json", "Canonical state of the pipeline run: per-series status, errors, warnings, data hash."),
        ],
    }
    if ctx["dashboard_url"] is None:
        raise ValidationFailed("semantic_model.json no declara la URL del dashboard (consumers)")

    specs = column_specs(c)
    readme = render_readme(c, ctx)
    docs = {
        "README.md": readme,
        "DATA_DICTIONARY.md": render_dictionary(specs, stats),
        "METHODOLOGY.md": render_methodology(c),
        "SOURCES_AND_LICENSES.md": render_sources(c, lr),
    }
    if lr["blockers"]:
        docs["PUBLICATION_BLOCKERS.md"] = render_blockers(c, lr)
    for name, text in docs.items():
        if "${" in text:
            raise ValidationFailed(f"{name}: plantilla sin sustituir")
        (tmp / name).write_text(text.rstrip() + "\n", encoding="utf-8")

    p_hash = package_hash(tmp)
    meta = kaggle_metadata(c, ctx, specs, readme, visibility)
    checks["kaggle_metadata"] = check_kaggle_metadata(meta, tmp)
    (tmp / "dataset-metadata.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    sc = {**{k: prov[k] for k in ("repository", "commit_sha", "commit_date", "schema_version", "config_version",
                                  "semantic_model_version")},
          "generated_at": generated_at, "data_as_of": prov["data_as_of"],
          "pipeline_generated_at": prov["pipeline_generated_at"], "data_hash": prov["data_hash"],
          "package_hash": p_hash, "canonical_inputs_clean": fmt(prov["canonical_inputs_clean"]),
          "manifest_overall_status": c["manifest"]["overall_status"], "license_review": lr["status"],
          "builder": "scripts/build_kaggle_dataset.py"}
    (tmp / "SOURCE_COMMIT.txt").write_text("".join(f"{k}: {fmt(v)}\n" for k, v in sc.items()), encoding="utf-8")
    checks["provenance"] = [] if prov["commit_sha"] and prov["canonical_inputs_clean"] else [
        "commit SHA desconocido o data/ y config/ con cambios sin commit: el paquete no sería trazable"]

    data_fail = any(checks[k] for k in checks if k not in ("schema", "kaggle_metadata", "provenance")) or schema_state == "FAIL"
    prev = read_kv(compare_with / "SOURCE_COMMIT.txt") if compare_with else {}
    result = {
        "checks": checks, "schema_state": schema_state, "fresh": fresh, "license": lr, "prov": prov,
        "package_hash": p_hash, "stats": stats, "data_fail": data_fail, "dataset_id": dataset_id,
        "version_message": version_message(c, prov, fresh), "date_range": ctx["date_range"],
        "previous_package_hash": prev.get("package_hash"),
        "update_required": prev.get("package_hash") != p_hash,
        "metadata": {k: meta[k] for k in ("title", "subtitle", "keywords", "licenses", "expectedUpdateFrequency", "isPrivate")},
        "n_series": len(series_order(c)), "auth": auth_status(),
        "manifest_status": c["manifest"]["overall_status"],
    }

    if data_fail:
        failed = out.parent / f"{out.name}.failed"
        safe_clean(failed)
        tmp.rename(failed)
        result["out"] = failed
    else:
        safe_clean(out)
        tmp.rename(out)
        result["out"] = out
    return result


def readiness(r: dict) -> tuple[str, str]:
    ck = r["checks"]

    def pf(*keys):
        return "PASS" if not any(ck.get(k) for k in keys) else "FAIL"

    lic = r["license"]["status"]
    data_ok = not r["data_fail"] and r["schema_state"] == "PASS" and not ck["provenance"]
    if not data_ok:
        status = "BLOCKED BY DATA VALIDATION"
    elif lic != "PASS":
        status = "BLOCKED BY LICENSING"
    elif r["auth"] != "READY":
        status = "READY — AUTHENTICATION REQUIRED"
    elif r["fresh"]["level"] != "OK":
        status = "READY WITH FRESHNESS WARNINGS"
    else:
        status = "READY TO PUBLISH"
    safe = "NO" if (not data_ok or lic != "PASS" or ck["kaggle_metadata"]) else (
        "YES_WITH_WARNINGS" if r["fresh"]["level"] != "OK" else "YES")
    issues = "\n".join(f"  - {i['provider']}: {', '.join(i['statuses'])} — {len(i['series'])} official + "
                       f"{len(i['affected_crosses'])} cross series; last official {i['last_official_date']}"
                       for i in r["fresh"]["provider_issues"]) or "  none"
    lic_lines = "\n".join(f"  - {b['provider']}: {b['issue']} ({len(b['series'])} series)" for b in r["license"]["blockers"])
    text = f"""KAGGLE PUBLICATION READINESS

Export validation: {pf('reconcile_export', 'history')}
Schema validation: {r['schema_state'] if not ck['contract'] else 'FAIL'}
Series coverage: {pf('reconcile_coverage')}
Semantic reconciliation: {pf('reconcile_semantic')}
Filled reconciliation: {pf('reconcile_filled')}
Cross-rate reconciliation: {pf('cross')}
Provenance: {pf('provenance')}

Freshness:
{r['fresh']['level']}

Current manifest status:
{r['manifest_status']} ({r['fresh']['series_ok']} ok, {r['fresh']['series_stale']} stale, {r['fresh']['series_error']} error)

Provider issues:
{issues}

License review:
{lic}{chr(10) + lic_lines if lic_lines else ''}

Kaggle metadata:
{pf('kaggle_metadata')}

Authentication:
{r['auth']}

Recommended Dataset ID:
{r['dataset_id']}

Safe to publish publicly:
{safe}

STATUS: {status}
"""
    return status, text


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--dataset-id", default=None, help="por defecto, el id de kaggle/dataset-metadata.template.json")
    ap.add_argument("--visibility", choices=["private", "public"], default="private")
    ap.add_argument("--compare-with", type=Path, default=None,
                    help="carpeta con SOURCE_COMMIT.txt y fx_series_metadata.csv de la versión publicada")
    ap.add_argument("--allow-history-loss", action="store_true",
                    help="aceptar una reducción de histórico frente a --compare-with (retiro explícito y revisado)")
    ap.add_argument("--github-output", type=Path, default=None, help="escribe update_required, version_message, status")
    args = ap.parse_args(argv)

    dataset_id = args.dataset_id or json.loads(
        (KAGGLE_SRC / "dataset-metadata.template.json").read_text(encoding="utf-8"))["id"]
    try:
        r = build(args.out.resolve(), dataset_id, args.visibility, args.compare_with, args.allow_history_loss)
    except ValidationFailed as exc:
        print(f"BLOCKED BY DATA VALIDATION\n{exc}", file=sys.stderr)
        return 1

    print(render_freshness(r["fresh"]))
    print()
    for k, v in r["checks"].items():
        for e in v[:20]:
            print(f"[{k}] {e}", file=sys.stderr)
    status, text = readiness(r)
    print(text)
    report = DIST / "kaggle_readiness.md"
    report.write_text(render_freshness(r["fresh"]) + "\n\n" + text, encoding="utf-8")

    print(f"package: {r['out']}  ({', '.join(f'{k}: {v.get('rows', '—')} rows' for k, v in r['stats'].items() if 'rows' in v)})")
    print(f"package_hash: {r['package_hash']}")
    if args.compare_with:
        print("KAGGLE DATA UPDATE REQUIRED" if r["update_required"] else "NO KAGGLE DATA UPDATE REQUIRED")
    print(f"version message: {r['version_message']}")
    if args.github_output:
        with args.github_output.open("a", encoding="utf-8") as fh:
            fh.write(f"update_required={fmt(r['update_required'] and not r['data_fail'])}\n")
            fh.write(f"version_message={r['version_message']}\n")
            fh.write(f"status={status}\n")
            fh.write(f"license_status={r['license']['status']}\n")
    return 1 if status == "BLOCKED BY DATA VALIDATION" else 0


if __name__ == "__main__":
    raise SystemExit(main())
