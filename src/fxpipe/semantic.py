"""Modelo semántico de la fuente de datos: metadatos reutilizables como modelo de datos avanzado.

Se genera en cada corrida y se publica junto a los datos (`data/semantic_model.json` y `.yaml`).
Combina tres fuentes, de modo que nada se escribe dos veces ni se desincroniza:

  1. `config/sources.json`   estructura y umbrales: proveedores, series, derivadas (fuente de verdad)
  2. `config/semantic.json`  curaduría bilingüe es/en: significados, fuentes, reglas, glosario
  3. la corrida              cobertura real: fechas, observaciones y estados por serie

Es neutral frente a cualquier herramienta: no depende de dbt, Power BI ni de un catálogo concreto, y
su estructura se valida con `schema/semantic_model.schema.json`. Solo usa la biblioteca estándar.

Dos niveles de exigencia:
  - En la corrida diaria es *indulgente*: si falta curaduría de una serie, el modelo se publica igual
    y lista el hueco en `curation.gaps`. Un metadato nunca debe impedir publicar un dato.
  - En las pruebas es *estricto*: `check_curation` y `check_invariants` deben devolver listas vacías,
    de modo que agregar un país sin curarlo hace fallar la CI.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal
from pathlib import Path

from . import store, transform

CURATION_FILE = "semantic.json"
LANGS = ("es", "en")

REQUIRED_SECTIONS = (
    "semantic_version", "model", "scope", "domain", "conventions", "currencies", "countries", "domains",
    "providers", "series", "templates", "measures", "metrics", "pipeline_stages", "quality_rules",
    "relationship_types", "hierarchies", "usage_guidance", "files", "fields", "glossary", "events",
    "consumers", "known_issues", "changelog",
)
REQUIRED_TEMPLATES = ("cross_name", "cross_description", "pair_name", "pair_description")
DOMAIN_LISTS = ("status", "overall_status", "date_basis", "method", "series_kind", "rate_type")
# Etapas que el generador referencia por id al describir el linaje de cada serie
LINEAGE_OFFICIAL = ("load_history", "fetch", "validate_and_merge", "fill_forward",
                    "validate_continuity_freshness", "build_outputs", "detect_change_and_write", "publish")
LINEAGE_FALLBACK_STAGE = "verify_fallback"
LINEAGE_CROSS = ("derive_crosses", "validate_continuity_freshness", "build_outputs",
                 "detect_change_and_write", "publish")
ACCESS_KEYS = ("kind", "base_url", "docs_url", "token_env", "token_required", "max_days_per_request",
               "group", "expected_sender")


# ---------------------------------------------------------------- carga de la curaduría

def curation_path(config_path: Path) -> Path:
    return Path(config_path).with_name(CURATION_FILE)


def load_curation(config_path: Path) -> tuple[dict | None, str | None]:
    """(curaduría, problema). Nunca lanza: un metadato ilegible no debe detener la publicación de datos."""
    path = curation_path(config_path)
    if not path.exists():
        return None, f"no existe {path.name}"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, f"{path.name} ilegible: {exc}"
    if not isinstance(doc, dict):
        return None, f"{path.name} no es un objeto JSON"
    return doc, None


# ---------------------------------------------------------------- utilidades de la configuración

def _specs(cfg: dict) -> dict[str, dict]:
    return {**cfg["series"], **cfg.get("derived", {})}


def _order(cfg: dict) -> list[str]:
    return list(cfg["series"]) + list(cfg.get("derived", {}))


def _provider_role(pid: str, as_provider: set[str], as_fallback: set[str]) -> str:
    if pid in as_provider:
        return "primary"
    if pid in as_fallback:
        return "verification_and_fallback"
    return "unused"


def _roles(cfg: dict) -> dict[str, str]:
    as_provider = {s["provider"] for s in cfg["series"].values()}
    as_fallback = {s["fallback"]["provider"] for s in cfg["series"].values() if s.get("fallback")}
    return {pid: _provider_role(pid, as_provider, as_fallback) for pid in cfg["providers"]}


def reference_currency(cfg: dict) -> str | None:
    """Moneda base común de todas las series oficiales (USD), o None si no hay una sola."""
    bases = {s["pair"].split("-")[0] for s in cfg["series"].values()}
    return next(iter(bases)) if len(bases) == 1 else None


def _fmt(template: dict, **kw) -> dict:
    return {lang: template[lang].format(**kw) for lang in LANGS}


def cross_method(numerator: str, denominator: str) -> str:
    return f"cross_via_usd: {numerator} / {denominator}"


# ---------------------------------------------------------------- comprobaciones

def _walk_texts(node, path: str, gaps: list[str]) -> None:
    """Todo texto bilingüe ({"es": …, "en": …}) debe traer los dos idiomas completos."""
    if isinstance(node, dict):
        if "es" in node or "en" in node:
            es, en = node.get("es"), node.get("en")
            texts = isinstance(es, str) and isinstance(en, str) and es.strip() and en.strip()
            lists = (isinstance(es, list) and isinstance(en, list) and len(es) == len(en) and es
                     and all(isinstance(x, str) and x.strip() for x in es + en))
            if not (texts or lists):
                gaps.append(f"{path}: texto es/en incompleto o de formas distintas")
        for k, v in node.items():
            if k not in ("es", "en"):
                _walk_texts(v, f"{path}.{k}", gaps)
    elif isinstance(node, list):
        for i, v in enumerate(node):
            _walk_texts(v, f"{path}[{i}]", gaps)


def _dups(values: list, label: str, gaps: list[str]) -> None:
    seen, dup = set(), []
    for v in values:
        if v in seen and v not in dup:
            dup.append(v)
        seen.add(v)
    if dup:
        gaps.append(f"{label}: ids repetidos {sorted(map(str, dup))}")


def check_invariants(cfg: dict) -> list[str]:
    """Reglas de diseño de la configuración que el modelo da por ciertas (independientes de la curaduría)."""
    errs: list[str] = []
    specs, ref = _specs(cfg), reference_currency(cfg)
    if ref is None:
        errs.append("las series oficiales no comparten una moneda base")
    by_pair: dict[str, list[str]] = {}
    for sid, s in specs.items():
        by_pair.setdefault(s["pair"], []).append(sid)
    for pair, sids in by_pair.items():
        primaries = [s for s in sids if specs[s]["primary"]]
        if len(primaries) != 1:
            errs.append(f"el par {pair} debe tener exactamente una serie primary; tiene {primaries}")
        if len({specs[s]["quote_unit"] for s in sids}) != 1:
            errs.append(f"el par {pair}: sus series declaran unidades distintas")
        base, quote = pair.split("-")
        if specs[sids[0]]["quote_unit"] != f"{quote} por 1 {base}":
            errs.append(f"el par {pair}: quote_unit «{specs[sids[0]]['quote_unit']}» no es «{quote} por 1 {base}»")
    for did, dv in cfg.get("derived", {}).items():
        base, quote = dv["pair"].split("-")
        num, den = specs[dv["numerator"]]["pair"], specs[dv["denominator"]]["pair"]
        if num != f"{ref}-{quote}" or den != f"{ref}-{base}":
            errs.append(f"cruzada {did} ({dv['pair']}): numerador {num} y denominador {den} no corresponden "
                        f"a {ref}-{quote} y {ref}-{base}")
        if dv["numerator"] not in cfg["series"] or dv["denominator"] not in cfg["series"]:
            errs.append(f"cruzada {did}: los insumos deben ser series oficiales")
    return errs


def check_curation(cfg: dict, cur: dict | None) -> list[str]:
    """Huecos de la curaduría frente a la configuración. Lista vacía = curaduría completa."""
    if not cur:
        return ["curaduría ausente: no se encontró config/semantic.json"]
    gaps: list[str] = [f"falta la sección «{k}»" for k in REQUIRED_SECTIONS if k not in cur]
    if gaps:
        return gaps
    specs, order = _specs(cfg), _order(cfg)
    official = list(cfg["series"])
    ref = reference_currency(cfg)

    for k in REQUIRED_TEMPLATES:
        if k not in cur["templates"]:
            gaps.append(f"plantilla «{k}» ausente")

    # dominios
    domains = cur["domains"]
    values = {k: {v["value"] for v in domains.get(k, [])} for k in DOMAIN_LISTS}
    for k in DOMAIN_LISTS:
        if not values[k]:
            gaps.append(f"dominio «{k}» vacío")
    for sid, s in specs.items():
        if s["date_basis"] not in values["date_basis"]:
            gaps.append(f"serie «{sid}»: date_basis «{s['date_basis']}» sin definir en domains.date_basis")

    # proveedores
    roles = _roles(cfg)
    for pid in cfg["providers"]:
        c = cur["providers"].get(pid)
        if not c:
            gaps.append(f"proveedor «{pid}» sin curaduría")
            continue
        gaps += [f"proveedor «{pid}»: falta «{k}»" for k in ("protocol", "publication", "quirks", "source", "role") if k not in c]
        if c.get("role") != roles[pid]:
            gaps.append(f"proveedor «{pid}»: rol curado «{c.get('role')}» distinto del derivado «{roles[pid]}»")
    gaps += [f"curaduría del proveedor «{p}» sin proveedor en sources.json" for p in cur["providers"] if p not in cfg["providers"]]

    # series oficiales
    rel_types = {r["type"] for r in cur["relationship_types"]}
    for sid in official:
        c = cur["series"].get(sid)
        if not c:
            gaps.append(f"serie «{sid}» sin curaduría")
            continue
        gaps += [f"serie «{sid}»: falta «{k}»" for k in ("name", "description", "rate_type") if k not in c]
        if c.get("rate_type") and c["rate_type"] not in values["rate_type"]:
            gaps.append(f"serie «{sid}»: rate_type «{c['rate_type']}» sin definir en domains.rate_type")
        for r in c.get("relations", []):
            if r["type"] not in rel_types:
                gaps.append(f"serie «{sid}»: relación «{r['type']}» no está en relationship_types")
            if r["target"] not in specs:
                gaps.append(f"serie «{sid}»: la relación {r['type']} apunta a «{r['target']}», que no existe")
    gaps += [f"curaduría de la serie «{s}» sin serie en sources.json" for s in cur["series"] if s not in cfg["series"]]
    if "cross_rate" not in values["rate_type"] and cfg.get("derived"):
        gaps.append("domains.rate_type no define «cross_rate»")

    # monedas y países
    codes = sorted({c for s in specs.values() for c in s["pair"].split("-")})
    gaps += [f"moneda «{c}» sin curaduría" for c in codes if c not in cur["currencies"]]
    gaps += [f"curaduría de la moneda «{c}» sin pares en sources.json" for c in cur["currencies"] if c not in codes]
    countries = {p["country"] for p in cfg["providers"].values()} | {c.get("country") for c in cur["currencies"].values() if c.get("country")}
    gaps += [f"país «{c}» sin curaduría" for c in sorted(countries) if c not in cur["countries"]]
    gaps += [f"curaduría del país «{c}» sin proveedor ni moneda" for c in cur["countries"] if c not in countries]
    if ref and "country" not in cur["currencies"].get(ref, {}):
        gaps.append(f"la moneda de referencia «{ref}» necesita su país en la curaduría")

    # eventos, etapas, consumidores y archivos
    gaps += [f"evento {e['date']}: la serie «{e['series']}» no existe" for e in cur["events"]["items"] if e["series"] not in specs]
    stages = {s["id"] for s in cur["pipeline_stages"]}
    gaps += [f"etapa «{s}» ausente en pipeline_stages" for s in (*LINEAGE_OFFICIAL, LINEAGE_FALLBACK_STAGE, *LINEAGE_CROSS) if s not in stages]
    consumers = {c["id"] for c in cur["consumers"]}
    file_paths = {f["path"] for f in cur["files"]}
    for f in cur["files"]:
        gaps += [f"archivo «{f['path']}»: consumidor «{c}» desconocido" for c in f.get("consumers", []) if c not in consumers]
    for c in cur["consumers"]:
        gaps += [f"consumidor «{c['id']}»: lee «{r['file']}», que no está en files" for r in c.get("reads", []) if r["file"] not in file_paths]
    conv_ids = {c["id"] for c in cur["conventions"]}
    for fld in cur["fields"]:
        for loc in fld["paths"]:
            if "#" not in loc or loc.split("#", 1)[0] not in file_paths:
                gaps.append(f"campo «{fld['id']}»: ubicación «{loc}» fuera de files")
        if fld.get("domain_ref") and fld["domain_ref"] not in DOMAIN_LISTS:
            gaps.append(f"campo «{fld['id']}»: domain_ref «{fld['domain_ref']}» no es un dominio")
        if fld.get("convention_ref") and fld["convention_ref"] not in conv_ids:
            gaps.append(f"campo «{fld['id']}»: convention_ref «{fld['convention_ref']}» no es una convención")
    for r in cur["quality_rules"]:
        if r.get("threshold_key") and not any(r["threshold_key"] in s for s in specs.values()):
            gaps.append(f"regla «{r['id']}»: el umbral «{r['threshold_key']}» no existe en la configuración")

    # unicidad de ids y textos bilingües
    for key in ("conventions", "measures", "metrics", "pipeline_stages", "quality_rules", "hierarchies",
                "usage_guidance", "fields", "consumers", "known_issues"):
        _dups([x["id"] for x in cur[key]], key, gaps)
    _dups([x["type"] for x in cur["relationship_types"]], "relationship_types", gaps)
    _dups([x["path"] for x in cur["files"]], "files", gaps)
    _dups([x["term"] for x in cur["glossary"]], "glossary", gaps)
    _walk_texts(cur, "curation", gaps)
    return gaps


# ---------------------------------------------------------------- construcción del modelo

def build_model(cfg: dict, cur: dict | None, daily: dict, status: dict, as_of: str,
                schema_version: str, curation_problem: str | None = None) -> dict:
    """Documento completo del modelo. Determinista: mismo cfg, curaduría y datos → mismo texto."""
    gaps = ([curation_problem] if curation_problem else []) + check_curation(cfg, cur)
    cur = cur or {}
    specs, order = _specs(cfg), _order(cfg)
    official, crosses = list(cfg["series"]), list(cfg.get("derived", {}))
    ref = reference_currency(cfg)
    tpl = cur.get("templates", {})
    c_series, c_prov = cur.get("series", {}), cur.get("providers", {})
    c_cur, c_cty = cur.get("currencies", {}), cur.get("countries", {})
    roles = _roles(cfg)

    def label_text(sid):
        return {"es": specs[sid]["label"], "en": specs[sid]["label"]}

    # --- pares y series
    pair_series: dict[str, list[str]] = {}
    for sid in order:
        pair_series.setdefault(specs[sid]["pair"], []).append(sid)
    canonical = {p: next((s for s in sids if specs[s]["primary"]), sids[0]) for p, sids in pair_series.items()}

    meta_like = {s: {"primary": specs[s]["primary"], **({"inputs": [specs[s]["numerator"], specs[s]["denominator"]]} if s in crosses else {})}
                 for s in order}
    compact_cols = transform.compact_columns(order, meta_like)
    compact_visible = {s for s in order if s in compact_cols or meta_like[s]["primary"]}

    series = []
    for sid in order:
        s, is_cross = specs[sid], sid in crosses
        base, quote = s["pair"].split("-")
        c = c_series.get(sid, {})
        ent: dict = {"id": sid, "kind": "cross" if is_cross else "official"}
        if is_cross:
            ent["name"] = _fmt(tpl["cross_name"], pair=s["pair"]) if "cross_name" in tpl else label_text(sid)
            ent["description"] = (_fmt(tpl["cross_description"], numerator=s["numerator"], denominator=s["denominator"],
                                       quote=quote, base=base, decimals=s["decimals"])
                                  if "cross_description" in tpl else label_text(sid))
            ent["description_source"] = "config/sources.json; config/semantic.json · templates"
            rate_type = "cross_rate"
        else:
            ent["name"] = c.get("name") or label_text(sid)
            ent["description"] = c.get("description") or label_text(sid)
            if c.get("description_source"):
                ent["description_source"] = c["description_source"]
            rate_type = c.get("rate_type")
        ent.update({
            "label": s["label"], "pair": s["pair"], "base_currency": base, "quote_currency": quote,
            "quote_unit": s["quote_unit"], "rate_type": rate_type, "date_basis": s["date_basis"],
            "method": cross_method(s["numerator"], s["denominator"]) if is_cross else "official",
            "primary": bool(s["primary"]), "decimals": s["decimals"],
        })
        th = {"plausible_range": list(s["plausible_range"]), "max_daily_change_pct": s["max_daily_change_pct"],
              "stale_after_days": s["stale_after_days"]}
        if "max_fill_days" in s:
            th["max_fill_days"] = s["max_fill_days"]
        ent["thresholds"] = th
        if is_cross:
            ent["derivation"] = {
                "numerator": s["numerator"], "denominator": s["denominator"],
                "formula": "round_js(numerator[d] / denominator[d], decimals)  (denominator ≠ 0)",
                "implemented_in": "fxpipe.transform.cross_rate"}
        else:
            p = cfg["providers"][s["provider"]]
            src = {"provider": s["provider"], "institution": p["institution"], "country": p["country"],
                   "series_id": s["source_series_id"], "docs_url": p["docs_url"]}
            if s.get("source_options"):
                src["options"] = dict(s["source_options"])
            fb = s.get("fallback")
            if fb:
                fp = cfg["providers"][fb["provider"]]
                src["verified_against"] = {"provider": fb["provider"], "institution": fp["institution"],
                                           "series_id": fb["source_series_id"], "docs_url": fp["docs_url"]}
                if fb.get("source_options"):
                    src["verified_against"]["options"] = dict(fb["source_options"])
            ent["source"] = src
        ent["relations"] = [dict(r) for r in c.get("relations", [])]
        ent["notes"] = [dict(n) for n in c.get("notes", [])]
        series.append(ent)

    # --- monedas, países, pares
    codes: list[str] = []
    for sid in order:
        for code in specs[sid]["pair"].split("-"):
            if code not in codes:
                codes.append(code)
    codes.sort(key=lambda x: x != ref)  # estable: la moneda de referencia primero

    def country_of_currency(code):
        if c_cur.get(code, {}).get("country"):
            return c_cur[code]["country"]
        sid = canonical.get(f"{ref}-{code}") if ref else None
        return cfg["providers"][specs[sid]["provider"]]["country"] if sid in cfg["series"] else None

    currencies = []
    for code in codes:
        currencies.append({
            "id": code, "name": c_cur.get(code, {}).get("name") or {"es": code, "en": code},
            "country": country_of_currency(code), "role": "reference" if code == ref else "local",
            "reference_pair": f"{ref}-{code}" if ref and f"{ref}-{code}" in pair_series else None,
            "pairs": [p for p in pair_series if code in p.split("-")]})

    cty_codes: list[str] = []
    for cur_ent in currencies:
        if cur_ent["country"] and cur_ent["country"] not in cty_codes:
            cty_codes.append(cur_ent["country"])
    for p in cfg["providers"].values():
        if p["country"] not in cty_codes:
            cty_codes.append(p["country"])
    countries = []
    for cc in cty_codes:
        countries.append({
            "id": cc, "name": c_cty.get(cc) or {"es": cc, "en": cc},
            "currency": next((c["id"] for c in currencies if c["country"] == cc), None),
            "providers": [pid for pid, p in cfg["providers"].items() if p["country"] == cc]})

    names = {c["id"]: c["name"] for c in currencies}
    pairs = []
    for pair, sids in pair_series.items():
        base, quote = pair.split("-")
        name = ({lang: tpl["pair_name"][lang].format(base_name=names[base][lang], quote_name=names[quote][lang]) for lang in LANGS}
                if "pair_name" in tpl else {"es": pair, "en": pair})
        desc = None
        if "pair_description" in tpl:
            desc = _fmt(tpl["pair_description"], base=base, quote=quote)
        ent = {"id": pair, "base": base, "quote": quote,
               "kind": "cross" if canonical[pair] in crosses else "official",
               "name": name, "quote_unit": specs[sids[0]]["quote_unit"],
               "canonical_series": canonical[pair], "series": sids}
        if desc:
            ent["description"] = desc
        pairs.append(ent)

    providers = []
    for pid, p in cfg["providers"].items():
        c = c_prov.get(pid, {})
        ent = {"id": pid, "institution": p["institution"], "country": p["country"], "role": roles[pid],
               "protocol": c.get("protocol") or {"es": p["kind"], "en": p["kind"]},
               "publication": c.get("publication"), "terms": c.get("terms"),
               "access": {k: p[k] for k in ACCESS_KEYS if k in p}}
        for k in ("authority", "certified_by", "legal_basis"):
            if p.get(k):
                ent[k] = p[k]
        ent["quirks"] = [dict(q) for q in c.get("quirks", [])]
        ent["series"] = [s for s in official if cfg["series"][s]["provider"] == pid]
        ent["fallback_for"] = [s for s in official if (cfg["series"][s].get("fallback") or {}).get("provider") == pid]
        if c.get("source"):
            ent["source"] = c["source"]
        providers.append(ent)

    # --- dominios con su uso real
    dom: dict = {}
    for k, v in cur.get("domains", {}).items():
        dom[k] = json.loads(json.dumps(v))
    for entry in dom.get("rate_type", []):
        entry["used_by"] = [e["id"] for e in series if e["rate_type"] == entry["value"]]
    for entry in dom.get("date_basis", []):
        entry["used_by"] = [e["id"] for e in series if e["date_basis"] == entry["value"]]
    for entry in dom.get("series_kind", []):
        entry["used_by"] = [e["id"] for e in series if e["kind"] == entry["value"]]

    # --- relaciones
    inst: list[dict] = []

    def rel(t, a, b):
        inst.append({"type": t, "from": a, "to": b})

    for e in series:
        if e["kind"] == "official":
            rel("belongs_to_provider", f"series:{e['id']}", f"providers:{e['source']['provider']}")
    for e in series:
        va = (e.get("source") or {}).get("verified_against")
        if va:
            rel("verified_against", f"series:{e['id']}", f"providers:{va['provider']}")
    for e in series:
        if e["kind"] == "cross":
            rel("numerator_of", f"series:{e['derivation']['numerator']}", f"series:{e['id']}")
    for e in series:
        if e["kind"] == "cross":
            rel("denominator_of", f"series:{e['derivation']['denominator']}", f"series:{e['id']}")
    for t in ("market_day_variant_of", "settlement_view_of"):
        for e in series:
            for r in e["relations"]:
                if r["type"] == t:
                    rel(t, f"series:{e['id']}", f"series:{r['target']}")
    for p in pairs:
        rel("base_currency", f"pairs:{p['id']}", f"currencies:{p['base']}")
    for p in pairs:
        rel("quote_currency", f"pairs:{p['id']}", f"currencies:{p['quote']}")
    for p in providers:
        rel("issued_in_country", f"providers:{p['id']}", f"countries:{p['country']}")
    for p in pairs:
        rel("canonical_series_of", f"series:{p['canonical_series']}", f"pairs:{p['id']}")

    # --- linaje
    def roots(sid):
        s = specs[sid]
        return [s["provider"]] if sid in cfg["series"] else sorted(set(roots(s["numerator"]) + roots(s["denominator"])))

    lineage_series = []
    for e in series:
        sid = e["id"]
        if e["kind"] == "official":
            up = [{"type": "provider", "id": e["source"]["provider"], "series_id": e["source"]["series_id"], "role": "source"}]
            va = e["source"].get("verified_against")
            if va:
                up.append({"type": "provider", "id": va["provider"], "series_id": va["series_id"], "role": "verification_and_fallback"})
            stages = list(LINEAGE_OFFICIAL)
            if va:
                stages.insert(stages.index("fill_forward"), LINEAGE_FALLBACK_STAGE)
        else:
            up = [{"type": "series", "id": e["derivation"]["numerator"], "role": "numerator"},
                  {"type": "series", "id": e["derivation"]["denominator"], "role": "denominator"}]
            stages = list(LINEAGE_CROSS)
        compact = "column" if sid in compact_cols else ("recomputed_from_inputs" if sid in compact_visible else "absent")
        files = ["latest.json", "rates_daily.json"] + (["rates_daily_compact.json"] if compact != "absent" else []) + ["series/<id>.json"]
        lineage_series.append({"series": sid, "upstream": up, "root_providers": roots(sid), "stages": stages,
                               "published_in": files, "compact_representation": compact})

    # --- calidad: a qué series aplica cada umbral
    rules = []
    for r in cur.get("quality_rules", []):
        r = dict(r)
        if r.get("threshold_key"):
            r["applies_to"] = [e["id"] for e in series if r["threshold_key"] in e["thresholds"]]
        rules.append(r)

    # --- cobertura de la corrida (lo único volátil)
    cov = []
    for sid in order:
        d = daily.get(sid, {})
        dates = sorted(d)
        n_filled = sum(1 for v in d.values() if v["filled"])
        cov.append({"id": sid, "status": status.get(sid, "ok"),
                    "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
                    "last_official_date": transform.last_official(d), "observations": len(d),
                    "official_observations": len(d) - n_filled, "filled_observations": n_filled})
    firsts = [c["first_date"] for c in cov if c["first_date"]]
    lasts = [c["last_date"] for c in cov if c["last_date"]]
    counts: dict[str, int] = {}
    for c in cov:
        counts[c["status"]] = counts.get(c["status"], 0) + 1

    model = {
        "semantic_model_version": cur.get("semantic_version", "0.0.0"),
        "schema_version": schema_version,
        "generated_from": {"config_version": cfg.get("config_version"),
                           "files": ["config/sources.json", "config/semantic.json"], "generator": "fxpipe.semantic"},
        "model": cur.get("model", {}),
        "scope": cur.get("scope", {}),
        "domain": cur.get("domain", {}),
        "conventions": cur.get("conventions", []),
        "dimensions": {"currencies": currencies, "countries": countries, "providers": providers,
                       "pairs": pairs, "series": series},
        "domains": dom,
        "measures": cur.get("measures", []),
        "metrics": cur.get("metrics", []),
        "relationships": {
            "reference_format": "<entidad>:<id>, con entidad = una clave de dimensions (series, providers, pairs, currencies, countries)",
            "types": cur.get("relationship_types", []), "instances": inst},
        "hierarchies": cur.get("hierarchies", []),
        "lineage": {"stages": sorted(cur.get("pipeline_stages", []), key=lambda x: x.get("order", 0)), "series": lineage_series},
        "quality": {"rules": rules},
        "files": cur.get("files", []),
        "fields": [_field(f) for f in cur.get("fields", [])],
        "glossary": cur.get("glossary", []),
        "events": cur.get("events", {}),
        "consumers": cur.get("consumers", []),
        "usage_guidance": cur.get("usage_guidance", []),
        "known_issues": cur.get("known_issues", []),
        "changelog": cur.get("changelog", []),
        "curation": {"complete": not gaps, "gaps": gaps, "languages": list(LANGS),
                     "currencies_source": cur.get("currencies_source"),
                     "note": {"es": "Lista vacía = toda serie, proveedor, moneda y país de la configuración tiene su curaduría en es y en.",
                              "en": "Empty list = every series, provider, currency and country in the configuration has its curation in es and en."}},
        "coverage": {
            "as_of": as_of,
            "series_count": len(order), "official_series_count": len(official), "cross_series_count": len(crosses),
            "primary_series_count": sum(1 for e in series if e["primary"]), "pair_count": len(pairs),
            "currency_count": len(currencies), "provider_count": len(providers),
            "date_range": {"first": min(firsts) if firsts else None, "last": max(lasts) if lasts else None},
            "status_counts": dict(sorted(counts.items())), "series": cov},
    }
    return model


def _field(f: dict) -> dict:
    out = {k: v for k, v in f.items() if k != "paths"}
    out["locations"] = [{"file": loc.split("#", 1)[0], "path": loc.split("#", 1)[1]} if "#" in loc else {"file": loc, "path": ""}
                        for loc in f["paths"]]
    out.setdefault("optional", False)
    return out


# ---------------------------------------------------------------- serialización

def render_json(model: dict) -> str:
    return store.render(model)


_PLAIN_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_RESERVED = {"y", "n", "yes", "no", "on", "off", "true", "false", "null", "~"}
_RAW = re.compile("[\x7f-\x9f\u2028\u2029\ufeff\ufffe\uffff]")


def _yaml_str(s: str) -> str:
    """Cadena entre comillas dobles (los escapes de JSON son válidos en YAML) sin caracteres que YAML trate como saltos."""
    return _RAW.sub(lambda m: "\\u%04x" % ord(m.group()), json.dumps(s, ensure_ascii=False))


def _yaml_key(k: str) -> str:
    return k if _PLAIN_KEY.match(k) and k.lower() not in _RESERVED else _yaml_str(k)


def _yaml_scalar(v) -> str:
    if v is None:
        return "null"
    if v is True:
        return "true"
    if v is False:
        return "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        text = format(Decimal(repr(v)), "f")  # sin notación científica: YAML 1.1 exige el punto
        return text if "." in text else text + ".0"
    if isinstance(v, str):
        return _yaml_str(v)
    raise TypeError(f"tipo no serializable a YAML: {type(v).__name__}")


def _yaml_inline(v) -> str:
    """Escalar o contenedor vacío, en una sola línea."""
    if isinstance(v, dict):
        return "{}"
    if isinstance(v, list):
        return "[]"
    return _yaml_scalar(v)


def _yaml_lines(v, ind: int) -> list[str]:
    """Bloque YAML de un diccionario o una lista no vacíos; los hijos van sangrados 2 espacios."""
    pad = " " * ind
    lines: list[str] = []
    if isinstance(v, dict):
        for k, val in v.items():
            if isinstance(val, (dict, list)) and val:
                lines.append(f"{pad}{_yaml_key(k)}:")
                lines += _yaml_lines(val, ind + 2)
            else:
                lines.append(f"{pad}{_yaml_key(k)}: {_yaml_inline(val)}")
    else:
        for item in v:
            if isinstance(item, (dict, list)) and item:
                child = _yaml_lines(item, ind + 2)
                lines.append(f"{pad}- {child[0][ind + 2:]}")
                lines += child[1:]
            else:
                lines.append(f"{pad}- {_yaml_inline(item)}")
    return lines


def render_yaml(model: dict) -> str:
    head = ("# Modelo semántico de fx-bancos-centrales: el mismo contenido que semantic_model.json, campo por campo.\n"
            "# Lo genera fxpipe.semantic en cada corrida; no se edita a mano (edite config/semantic.json o config/sources.json).\n"
            "# Semantic model of fx-bancos-centrales: the same content as semantic_model.json, field for field.\n")
    return head + "\n".join(_yaml_lines(model, 0)) + "\n"


# ---------------------------------------------------------------- documentación generada

def _cell(x) -> str:
    """Celda de tabla Markdown: «|» escapado y «<…>» fuera de código como texto (no como etiqueta HTML); «<br>» se conserva."""
    text = ("" if x is None else str(x)).replace("|", "\\|").replace("\n", " ").replace("<br>", "\0")
    parts = text.split("`")
    for i in range(0, len(parts), 2):  # los tramos pares quedan fuera de los acentos graves
        parts[i] = parts[i].replace("<", "&lt;").replace(">", "&gt;")
    return "`".join(parts).replace("\0", "<br>")


def _table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    out += ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _code(x) -> str:
    return f"`{x}`"


def render_doc_blocks(model: dict) -> dict[str, str]:
    """Tablas en español de MODELO_SEMANTICO.md. Salen solo de la configuración y la curaduría (no de la cobertura)."""
    dim, es = model["dimensions"], lambda t: t["es"]
    blocks: dict[str, str] = {}
    n_cross = sum(1 for s in dim["series"] if s["kind"] == "cross")
    blocks["counts"] = _table(
        ["Dimensión", "Cantidad", "Detalle"],
        [["Series", len(dim["series"]), f"{len(dim['series']) - n_cross} oficiales y {n_cross} calculadas (cruzadas); "
                                       f"{sum(1 for s in dim['series'] if s['primary'])} visibles en el dashboard"],
         ["Pares", len(dim["pairs"]), "una serie canónica (primary) por par; las demás son variantes ocultas"],
         ["Monedas", len(dim["currencies"]), "una de referencia y el resto locales"],
         ["Países", len(dim["countries"]), "el de la moneda de referencia y los de cada banco central"],
         ["Proveedores", len(dim["providers"]), "bancos centrales y fuentes de verificación"]])
    blocks["series"] = _table(
        ["Serie", "Tipo", "Par", "Visible", "Fecha (date_basis)", "Tipo de tasa", "Origen"],
        [[_code(s["id"]), "oficial" if s["kind"] == "official" else "calculada", _code(s["pair"]), "sí" if s["primary"] else "no",
          s["date_basis"], s["rate_type"],
          f"{s['source']['provider']} · {_code(s['source']['series_id'])}" if s["kind"] == "official"
          else f"{_code(s['derivation']['numerator'])} ÷ {_code(s['derivation']['denominator'])}"] for s in dim["series"]])
    blocks["providers"] = _table(
        ["Proveedor", "Institución", "País", "Rol", "Secreto", "Protocolo"],
        [[_code(p["id"]), p["institution"], p["country"], p["role"],
          _code(p["access"]["token_env"]) + (" (obligatorio)" if p["access"].get("token_required") else " (opcional)") if p["access"].get("token_env") else "—",
          es(p["protocol"])] for p in dim["providers"]])
    blocks["currencies"] = _table(
        ["Código", "Español", "English", "País", "Rol"],
        [[_code(c["id"]), es(c["name"]), c["name"]["en"], c["country"], "referencia" if c["role"] == "reference" else "local"]
         for c in dim["currencies"]])
    blocks["pairs"] = _table(
        ["Par", "Tipo", "Serie canónica", "Series del par"],
        [[_code(p["id"]), "oficial" if p["kind"] == "official" else "cruzada", _code(p["canonical_series"]),
          ", ".join(_code(s) for s in p["series"])] for p in dim["pairs"]])
    blocks["measures"] = _table(
        ["Medida", "Campo", "Unidad", "Aditividad", "Agregación en el tiempo"],
        [[es(m["name"]), _code(m["field"]), m["unit"], m["additivity"],
          ", ".join(m.get("time_aggregation", [])) + (f" (nunca: {', '.join(m['forbidden_aggregations'])})" if m.get("forbidden_aggregations") else "") or "—"]
         for m in model["measures"]])
    blocks["metrics"] = _table(
        ["Métrica", "Definición", "Se calcula en"],
        [[es(m["name"]), es(m["definition"]), _code(m["implemented_in"]) + (" (consumidor)" if m.get("scope") == "consumer" else "")]
         for m in model["metrics"]])
    blocks["quality"] = _table(
        ["Regla", "Severidad", "Consecuencia", "Umbral", "Qué verifica"],
        [[_code(r["id"]), r["severity"], r["outcome"], _code(r["threshold_key"]) if r.get("threshold_key") else "—", es(r["check"])]
         for r in model["quality"]["rules"]])
    blocks["files"] = _table(
        ["Archivo", "Rol", "Esquema", "Desde", "Qué contiene"],
        [[_code(f["path"]), f["role"], _code(f["schema"]) if f.get("schema") else "—", f["since"], es(f["description"])] for f in model["files"]])
    blocks["fields"] = _table(
        ["Campo", "Ubicaciones", "Tipo", "Descripción"],
        [[_code(f["id"]), "<br>".join(_code(f"{l['file']}#{l['path']}") for l in f["locations"][:2])
          + (f"<br>(+{len(f['locations']) - 2} más)" if len(f["locations"]) > 2 else ""), f["type"], es(f["description"])]
         for f in model["fields"]])
    blocks["glossary"] = _table(["Término", "Definición"], [[g["term"], g["es"]] for g in model["glossary"]])
    blocks["known_issues"] = _table(
        ["Incidencia", "Estado", "Resuelta", "Resumen"],
        [[_code(k["id"]), k["status"], k.get("resolved_on", "—"), es(k["summary"])] for k in model["known_issues"]])
    return blocks


def _marker(name: str) -> tuple[str, str]:
    return f"<!-- BEGIN:generado:{name} -->", f"<!-- END:generado:{name} -->"


def sync_doc(text: str, blocks: dict[str, str]) -> str:
    """Reemplaza el contenido entre los marcadores de cada bloque; falla si falta alguno."""
    for name, body in blocks.items():
        begin, end = _marker(name)
        if begin not in text or end not in text:
            raise ValueError(f"MODELO_SEMANTICO.md no tiene los marcadores del bloque «{name}»")
        a, b = text.index(begin) + len(begin), text.index(end)
        text = text[:a] + "\n" + body + "\n" + text[b:]
    return text
