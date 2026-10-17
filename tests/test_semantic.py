"""Pruebas del modelo semántico (data/semantic_model.json y .yaml).

Su propósito es que el modelo no pueda desincronizarse de lo que realmente se publica:
cada afirmación verificable del modelo se contrasta con la configuración, con el código o con
los archivos que genera una corrida simulada.
"""
import copy
import importlib
import json
import re
from datetime import date
from pathlib import Path

import pytest

from fxpipe import pipeline, semantic, store, transform
from test_pipeline import AS_OF, ROOT, env, fake_http, load  # noqa: F401  (env es una fixture)

SCHEMA = ROOT / "schema" / "semantic_model.schema.json"
EXTERNAL = "jaforero/dashboards:"


# ------------------------------------------------------------------ fixtures y ayudas

@pytest.fixture
def run(env):
    """Una corrida simulada completa: (cfg, curaduría, carpeta de datos, modelo, manifest)."""
    cfg, data = env
    pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    model = json.loads((data / "semantic_model.json").read_text(encoding="utf-8"))
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    return cfg, cfg["_curation"], data, model, manifest


def by_id(items, key="id"):
    return {x[key]: x for x in items}


def flatten(doc, ids) -> set[str]:
    """Rutas de un documento: claves de series → «*»; «[]» para el elemento de una lista de objetos.

    Una lista de valores simples queda con la ruta de la lista; una lista de objetos, con la del
    elemento («rows[]») y sus claves («rows[].date»), como dice la convención field_path_notation.
    """
    out: set[str] = set()

    def rec(node, p):
        if isinstance(node, dict):
            for k, v in node.items():
                q = f"{p}.{'*' if k in ids else k}" if p else ("*" if k in ids else k)
                out.add(q)
                rec(v, q)
        elif isinstance(node, list):
            structured = [v for v in node if isinstance(v, (dict, list))]
            if structured:
                out.discard(p)
                out.add(p + "[]")
                for v in structured:
                    rec(v, p + "[]")

    rec(doc, "")
    return out


def published_paths(data: Path, ids) -> dict[str, set[str]]:
    files = {"latest.json": [data / "latest.json"], "rates_daily.json": [data / "rates_daily.json"],
             "rates_daily_compact.json": [data / "rates_daily_compact.json"], "manifest.json": [data / "manifest.json"],
             "series/<id>.json": sorted((data / "series").glob("*.json"))}
    out = {name: set().union(*[flatten(json.loads(p.read_text(encoding="utf-8")), ids) for p in paths])
           for name, paths in files.items()}
    # una lista vacía en un archivo y con objetos en otro es la misma ruta: manda la del elemento
    return {name: {p for p in paths if p + "[]" not in paths} for name, paths in out.items()}


def resolves(ref: str) -> bool:
    """¿Existe lo que dice `implemented_in`? Ruta de archivo o nombre punteado de Python."""
    path = ref.split("::")[0]
    if "/" in path or path.endswith((".py", ".yml", ".yaml", ".md", ".json")):
        return (ROOT / path).exists()
    parts = ref.split(".")
    for i in range(len(parts), 0, -1):
        try:
            obj = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        for attr in parts[i:]:
            if not hasattr(obj, attr):
                return False
            obj = getattr(obj, attr)
        return True
    return False


def walk(node, path=""):
    """(ruta, clave, valor) de todas las claves de un documento."""
    if isinstance(node, dict):
        for k, v in node.items():
            yield f"{path}.{k}", k, v
            yield from walk(v, f"{path}.{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from walk(v, f"{path}[{i}]")


def config_path_exists(cfg: dict, dotted: str) -> bool:
    def rec(node, toks):
        if not toks:
            return True
        if not isinstance(node, dict):
            return False
        head, rest = toks[0], toks[1:]
        if head == "*":
            return any(rec(v, rest) for v in node.values())
        return head in node and rec(node[head], rest)
    return rec({k: v for k, v in cfg.items() if not k.startswith("_")}, dotted.split("."))


# ------------------------------------------------------------------ curaduría y configuración

def test_curation_covers_the_whole_configuration(env):
    cfg, _ = env
    assert cfg["_curation_problem"] is None
    assert semantic.check_invariants(cfg) == []
    assert semantic.check_curation(cfg, cfg["_curation"]) == []


def test_pairs_form_a_complete_cross_mesh_with_one_primary_each(run):
    cfg, cur, data, model, manifest = run
    pairs = by_id(model["dimensions"]["pairs"])
    series = model["dimensions"]["series"]
    ref = "USD"
    local = [c["id"] for c in model["dimensions"]["currencies"] if c["id"] != ref]
    assert len(local) == 7
    official_pairs = {f"{ref}-{c}" for c in local}
    cross_pairs = {p for p, e in pairs.items() if e["kind"] == "cross"}
    expected = {f"{b}-{q}" for b in local for q in local if b != q}
    # una sola dirección por cada par no ordenado de monedas locales: 7 * 6 / 2 = 21
    assert len(cross_pairs) == 21 and {frozenset(p.split("-")) for p in cross_pairs} == {frozenset(p.split("-")) for p in expected}
    assert official_pairs <= set(pairs) and len(pairs) == 28
    for pair, e in pairs.items():
        members = [s for s in series if s["pair"] == pair]
        assert [s["id"] for s in members] == e["series"]
        assert [s["id"] for s in members if s["primary"]] == [e["canonical_series"]]
    assert sum(1 for s in series if s["primary"]) == 28 == model["coverage"]["primary_series_count"]


def test_cross_derivation_uses_the_usd_quote_over_the_usd_base(run):
    cfg, cur, data, model, manifest = run
    series = by_id(model["dimensions"]["series"])
    for s in series.values():
        if s["kind"] != "cross":
            continue
        d = s["derivation"]
        assert series[d["numerator"]]["pair"] == f"USD-{s['quote_currency']}"
        assert series[d["denominator"]]["pair"] == f"USD-{s['base_currency']}"
        assert series[d["numerator"]]["date_basis"] == series[d["denominator"]]["date_basis"] == s["date_basis"]
        assert series[d["numerator"]]["kind"] == series[d["denominator"]]["kind"] == "official"
        assert s["rate_type"] == "cross_rate" and s["method"] == f"cross_via_usd: {d['numerator']} / {d['denominator']}"


def test_licence_note_still_matches_the_repository():
    """El modelo afirma que el repositorio no declara licencia: si se agrega una, hay que actualizar la nota."""
    assert not [p for p in ROOT.iterdir() if p.name.upper().startswith(("LICENSE", "LICENCE", "COPYING"))]


def test_new_series_without_curation_is_reported(env):
    """Agregar una serie, moneda o país sin curarlos hace fallar la CI (y se declara en curation.gaps)."""
    cfg, _ = env
    cfg2 = copy.deepcopy(cfg)
    cfg2["series"]["usd_zzz_test"] = {**cfg["series"]["usd_pen_interbank"], "pair": "USD-ZZZ", "quote_unit": "ZZZ por 1 USD"}
    gaps = semantic.check_curation(cfg2, cfg["_curation"])
    assert any("serie «usd_zzz_test» sin curaduría" in g for g in gaps)
    assert any("moneda «ZZZ» sin curaduría" in g for g in gaps)
    model = semantic.build_model(cfg2, cfg["_curation"], {}, {}, "2026-10-17", store.SCHEMA_VERSION)
    assert model["curation"]["complete"] is False and model["curation"]["gaps"] == gaps


def test_incomplete_translation_is_reported(env):
    cfg, _ = env
    cur = copy.deepcopy(cfg["_curation"])
    del cur["series"]["usd_mxn_fix"]["description"]["en"]
    cur["glossary"][0]["en"] = ""
    gaps = semantic.check_curation(cfg, cur)
    assert any("usd_mxn_fix" in g and "description" in g for g in gaps)
    cur2 = copy.deepcopy(cfg["_curation"])
    cur2["known_issues"][0]["resolution"]["en"].pop()
    assert any("resolution" in g for g in semantic.check_curation(cfg, cur2))


def test_provider_role_mismatch_is_reported(env):
    cfg, _ = env
    cur = copy.deepcopy(cfg["_curation"])
    cur["providers"]["sfc_trm"]["role"] = "primary"
    assert any("sfc_trm" in g and "rol" in g for g in semantic.check_curation(cfg, cur))


def test_cli_semantic_check_passes(capsys):
    from fxpipe.__main__ import main
    assert main(["semantic"]) == 0
    assert "Curaduría completa" in capsys.readouterr().out


# ------------------------------------------------------------------ esquema y serializaciones

def test_model_validates_against_its_schema_and_the_schema_is_strict(run):
    jsonschema = pytest.importorskip("jsonschema")
    cfg, cur, data, model, manifest = run
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator.check_schema(schema)
    jsonschema.validate(model, schema)
    # el esquema es cerrado: un campo desconocido o uno que falta lo rompe
    bad = copy.deepcopy(model)
    bad["dimensions"]["series"][0]["campo_inventado"] = 1
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    bad = copy.deepcopy(model)
    del bad["dimensions"]["series"][0]["thresholds"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
    bad = copy.deepcopy(model)
    bad["glossary"][0]["en"] = ""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_yaml_is_field_for_field_equivalent_to_json(run):
    yaml = pytest.importorskip("yaml")
    cfg, cur, data, model, manifest = run
    assert yaml.safe_load((data / "semantic_model.yaml").read_text(encoding="utf-8")) == model


def test_yaml_emitter_handles_awkward_values():
    yaml = pytest.importorskip("yaml")
    nasty = {
        "quote": 'x"y\\z\nline\ttab', "sep": "    \x85 ﻿ é ñ 日本 😀", "no": "yes", "on": None, "": "empty key",
        "es-CO": True, "k with space": 1, "empty_list": [], "empty_dict": {}, "n": "~", "s": "null", "t": "true",
        "d": "2026-10-17", "num": "123", "colon": "a: b", "hash": "a # b", "dash": "- x", "lead": "  spaces  ",
        "floats": [0.1, 1e-05, 123456789.5, 2.0, -0.5, 0.0], "ints": [0, -3, 10**12],
        "nested": [[1, 2], [3, [4, 5]], {"k": [{"z": 1}, {"z": 2}], "e": {}}, [], {}, None, "s"],
    }
    text = semantic.render_yaml(nasty)
    assert yaml.safe_load(text) == nasty
    with pytest.raises(TypeError):
        semantic.render_yaml({"x": {1, 2}})


def test_generation_is_deterministic_and_files_are_listed(run, env):
    cfg, cur, data, model, manifest = run
    a = semantic.build_model(cfg, cur, {}, {}, "2026-10-17", store.SCHEMA_VERSION)
    b = semantic.build_model(copy.deepcopy(cfg), copy.deepcopy(cur), {}, {}, "2026-10-17", store.SCHEMA_VERSION)
    assert semantic.render_json(a) == semantic.render_json(b)
    assert semantic.render_yaml(a) == semantic.render_yaml(b)
    published = {re.sub(r"^series/.+\.json$", "series/<id>.json", f) for f in manifest["files"]}
    cataloged = {f["path"] for f in model["files"]} - {"manifest.json"}
    assert published == cataloged, "los archivos del manifest y los del modelo deben coincidir"
    assert {"semantic_model.json", "semantic_model.yaml"} <= set(manifest["files"])
    assert model["schema_version"] == manifest["schema_version"] == store.SCHEMA_VERSION
    for f in model["files"]:
        if f["schema"]:
            assert (ROOT / f["schema"]).exists(), f["schema"]


def test_semantic_files_change_the_data_hash_only_through_their_content(env):
    cfg, data = env
    first = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    second = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert first["changed"] is True and second["changed"] is False, "una corrida repetida no reescribe nada"


# ------------------------------------------------------------------ el modelo contra lo publicado

def test_series_metadata_matches_what_is_published(run):
    cfg, cur, data, model, manifest = run
    wide = json.loads((data / "rates_daily.json").read_text(encoding="utf-8"))["series"]
    series = by_id(model["dimensions"]["series"])
    assert list(series) == list(wide), "mismo conjunto y mismo orden que rates_daily.json"
    for sid, m in wide.items():
        s = series[sid]
        for k in ("pair", "label", "quote_unit", "date_basis", "primary", "decimals", "method"):
            assert s[k] == m[k], (sid, k)
        if "inputs" in m:
            assert [s["derivation"]["numerator"], s["derivation"]["denominator"]] == m["inputs"]
        else:
            src = m["source"]
            assert (s["source"]["provider"], s["source"]["institution"], s["source"]["country"], s["source"]["series_id"],
                    s["source"]["docs_url"]) == (src["provider"], src["institution"], src["country"], src["series_id"], src["docs_url"])
            assert s["source"].get("verified_against", {}).get("provider") == src.get("verified_against", {}).get("provider")
        spec = cfg["series"].get(sid) or cfg["derived"][sid]
        assert s["thresholds"]["plausible_range"] == spec["plausible_range"]
        assert s["thresholds"]["max_daily_change_pct"] == spec["max_daily_change_pct"]
        assert s["thresholds"]["stale_after_days"] == spec["stale_after_days"]
        assert s["thresholds"].get("max_fill_days") == spec.get("max_fill_days")


def test_every_published_path_is_documented_and_every_documented_path_exists(env, tmp_path_factory):
    """El diccionario de campos describe exactamente lo que se publica: ni rutas sin documentar ni rutas fantasma."""
    cfg, data = env
    pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    model = json.loads((data / "semantic_model.json").read_text(encoding="utf-8"))
    ids = set(cfg["series"]) | set(cfg["derived"])
    observed = published_paths(data, ids)
    # una segunda corrida con una fuente caída hace aparecer los campos de error
    data2 = tmp_path_factory.mktemp("degradada")
    pipeline.run(cfg, data2, AS_OF, http_get=fake_http(fail="bcra"))
    # y una revisión de un valor ya publicado hace aparecer las advertencias
    revised = json.loads(json.dumps(load("banxico_ok.json")))
    revised["bmx"]["series"][0]["datos"][0]["dato"] = "18.9000"
    pipeline.run(cfg, data, AS_OF, http_get=fake_http(banxico=revised))
    for extra in (data2, data):
        for name, paths in published_paths(extra, ids).items():
            merged = observed[name] | paths
            observed[name] = {p for p in merged if p + "[]" not in merged}
    declared: dict[str, set[str]] = {}
    optional: dict[str, set[str]] = {}
    for f in model["fields"]:
        for loc in f["locations"]:
            declared.setdefault(loc["file"], set()).add(loc["path"])
            if f["optional"]:
                optional.setdefault(loc["file"], set()).add(loc["path"])
    problems = []
    for name, paths in observed.items():
        undocumented = paths - declared.get(name, set())
        phantom = declared.get(name, set()) - paths - optional.get(name, set())
        if undocumented:
            problems.append(f"{name}: rutas publicadas sin documentar: {sorted(undocumented)}")
        if phantom:
            problems.append(f"{name}: rutas documentadas que no existen: {sorted(phantom)}")
    assert not problems, "\n".join(problems)
    assert set(declared) <= set(observed), "ubicaciones en archivos que no se publican"
    # «optional» no esconde campos muertos: con una fuente caída y una revisión, todo lo opcional aparece
    never = {(n, p) for n, ps in optional.items() for p in ps if p not in observed[n]}
    assert not never, f"campos opcionales que ninguna corrida simulada produce: {sorted(never)}"


def test_lineage_reflects_the_real_compact_file(run):
    cfg, cur, data, model, manifest = run
    compact = json.loads((data / "rates_daily_compact.json").read_text(encoding="utf-8"))
    lineage = by_id(model["lineage"]["series"], "series")
    for sid in lineage:
        rep = lineage[sid]["compact_representation"]
        assert (rep == "column") == (sid in compact["columns"]), sid
        assert (rep != "absent") == (sid in compact["series"]), sid
        assert ("rates_daily_compact.json" in lineage[sid]["published_in"]) == (rep != "absent")
    series = by_id(model["dimensions"]["series"])
    stage_ids = {s["id"] for s in model["lineage"]["stages"]}
    for sid, ln in lineage.items():
        assert set(ln["stages"]) <= stage_ids
        if series[sid]["kind"] == "cross":
            d = series[sid]["derivation"]
            assert set(ln["root_providers"]) == {series[d["numerator"]]["source"]["provider"], series[d["denominator"]]["source"]["provider"]}
        else:
            assert ln["root_providers"] == [series[sid]["source"]["provider"]]
            assert ("verify_fallback" in ln["stages"]) == bool(series[sid]["source"].get("verified_against"))
    assert [s["order"] for s in model["lineage"]["stages"]] == sorted(s["order"] for s in model["lineage"]["stages"])


def test_relationship_instances_are_consistent(run):
    cfg, cur, data, model, manifest = run
    dim = model["dimensions"]
    ids = {"series": {s["id"] for s in dim["series"]}, "providers": {p["id"] for p in dim["providers"]},
           "pairs": {p["id"] for p in dim["pairs"]}, "currencies": {c["id"] for c in dim["currencies"]},
           "countries": {c["id"] for c in dim["countries"]}}
    types = by_id(model["relationships"]["types"], "type")
    counts: dict[str, int] = {}
    for r in model["relationships"]["instances"]:
        t = types[r["type"]]
        for end, key in (("from", "from"), ("to", "to")):
            entity, _, ident = r[end].partition(":")
            assert entity == t[key] and ident in ids[entity], r
        counts[r["type"]] = counts.get(r["type"], 0) + 1
    crosses = [s for s in dim["series"] if s["kind"] == "cross"]
    assert counts["belongs_to_provider"] == len(cfg["series"]) and counts["numerator_of"] == counts["denominator_of"] == len(crosses)
    assert counts["canonical_series_of"] == counts["base_currency"] == counts["quote_currency"] == len(dim["pairs"])
    assert counts["issued_in_country"] == len(cfg["providers"])
    assert counts["verified_against"] == sum(1 for s in cfg["series"].values() if s.get("fallback"))
    assert set(counts) == set(types), "cada tipo de relación declarado tiene instancias, y viceversa"


def test_coverage_matches_the_series_files(run):
    cfg, cur, data, model, manifest = run
    cov = by_id(model["coverage"]["series"])
    assert model["coverage"]["as_of"] == manifest["as_of"] == AS_OF.isoformat()
    for sid, c in cov.items():
        doc = json.loads((data / "series" / f"{sid}.json").read_text(encoding="utf-8"))
        assert (c["first_date"], c["last_date"], c["last_official_date"]) == (doc["first_date"], doc["last_date"], doc["last_official_date"])
        assert c["observations"] == len(doc["observations"])
        assert c["filled_observations"] == sum(1 for o in doc["observations"] if o["filled"])
        assert c["status"] == manifest["series_status"][sid]
    assert model["coverage"]["status_counts"] == {"ok": 31}
    assert model["coverage"]["date_range"]["last"] == max(c["last_date"] for c in cov.values())


def test_status_domain_covers_every_status_the_pipeline_can_emit(env):
    cfg, data = env
    pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="bcra"))
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    model = json.loads((data / "semantic_model.json").read_text(encoding="utf-8"))
    statuses = {s["value"] for s in model["domains"]["status"]}
    assert set(manifest["series_status"].values()) <= statuses and "error:provider" in manifest["series_status"].values()
    assert statuses == {"ok", "stale", "error:provider", "error:contract", "error:validation", "error:empty"}
    assert model["coverage"]["status_counts"]["error:provider"] >= 1
    # error:empty es la única situación en que ni siquiera hay último valor
    assert {s["value"] for s in model["domains"]["status"] if not s["keeps_last_value"]} == {"error:empty"}


def test_domain_definitions_are_all_in_use(run):
    cfg, cur, data, model, manifest = run
    for dom in ("rate_type", "date_basis", "series_kind"):
        for entry in model["domains"][dom]:
            assert entry["used_by"], f"{dom}={entry['value']} está definido pero ninguna serie lo usa"
    used = {s["rate_type"] for s in model["dimensions"]["series"]}
    assert used == {e["value"] for e in model["domains"]["rate_type"]}


def test_published_formulas_hold_on_the_latest_snapshot(run):
    """Las fórmulas que el modelo declara para las medidas se cumplen en latest.json."""
    cfg, cur, data, model, manifest = run
    latest = json.loads((data / "latest.json").read_text(encoding="utf-8"))
    measures = by_id(model["measures"])
    assert measures["change_abs"]["formula"] == "round(value - prev_value, 6)"
    assert measures["change_pct"]["formula"] == "round((value / prev_value - 1) * 100, 4)"
    assert measures["days_since_official"]["formula"] == "as_of - date"
    checked = 0
    for sid, s in latest["series"].items():
        if "prev_value" not in s:
            continue
        assert s["change_abs"] == round(s["value"] - s["prev_value"], 6), sid
        assert s["change_pct"] == round((s["value"] / s["prev_value"] - 1) * 100, 4), sid
        assert s["days_since_official"] == (date.fromisoformat(latest["as_of"]) - date.fromisoformat(s["date"])).days, sid
        checked += 1
    assert checked >= 20
    for m in model["measures"]:
        if m["id"] == "rate_value":
            assert m["additivity"] == "non_additive" and "sum" in m["forbidden_aggregations"]


def test_cross_rounding_rule_in_the_model_is_the_one_the_code_uses(run):
    cfg, cur, data, model, manifest = run
    conv = by_id(model["conventions"])
    assert "round_js" in conv["rounding_equals_javascript"]["rule"]["es"]
    assert transform.round_js(18.03125, 4) == 18.0313  # el ejemplo que cita la convención
    assert "18,03125" in conv["rounding_equals_javascript"]["rule"]["es"]


# ------------------------------------------------------------------ referencias a código, archivos y configuración

def test_implemented_in_references_resolve(run):
    cfg, cur, data, model, manifest = run
    refs = []
    for sect in (model["measures"], model["metrics"], model["lineage"]["stages"], model["quality"]["rules"]):
        refs += [(x["id"], x["implemented_in"], x.get("verifiable_in_repo", True)) for x in sect if "implemented_in" in x]
    refs += [(s["id"], s["derivation"]["implemented_in"], True) for s in model["dimensions"]["series"] if "derivation" in s]
    assert len(refs) >= 40
    for name, ref, verifiable in refs:
        if ref.startswith(EXTERNAL):
            assert verifiable is False, f"{name}: una referencia externa debe declarar verifiable_in_repo=false"
        else:
            assert resolves(ref), f"{name}: «{ref}» no existe"


def test_pipeline_stage_config_keys_exist_in_sources_json(run):
    cfg, cur, data, model, manifest = run
    for stage in model["lineage"]["stages"]:
        for key in stage["config_keys"]:
            assert config_path_exists(cfg, key), f"{stage['id']}: la clave de configuración «{key}» no existe"
    for rule in model["quality"]["rules"]:
        if "threshold_key" in rule:
            assert rule["applies_to"], rule["id"]


def test_source_references_point_to_files_that_exist(run):
    cfg, cur, data, model, manifest = run
    # «docs/X.md · rates_daily.json»: lo que sigue a «· » es el nombre de una sección, no una ruta
    token = re.compile(r"(?<![\w/:.\-])(?<!· )((?:[\w.\-]+/)*[\w.\-]+\.(?:py|md|json|yml|yaml|html|toml))(?![\w])")
    checked = 0
    for path, key, value in walk(model):
        if not (key == "source" or key.endswith("_source")) or not isinstance(value, str):
            continue
        for t in token.findall(value):
            if t.startswith(("http", "www.")) or "." not in t:
                continue
            assert (ROOT / t).exists(), f"{path}: «{t}» no existe en el repositorio ({value})"
            checked += 1
    assert checked >= 100
    for f in model["generated_from"]["files"]:
        assert (ROOT / f).exists()


def test_every_external_reference_is_flagged_as_not_verifiable_here(run):
    cfg, cur, data, model, manifest = run
    external_metrics = [m for m in model["metrics"] if m["implemented_in"].startswith(EXTERNAL)]
    assert external_metrics and all(m["scope"] == "consumer" and m["verifiable_in_repo"] is False for m in external_metrics)
    assert all(m["id"] in {"change_over_days", "ytd_change", "rebased_index_100", "conversion"} for m in external_metrics)


def test_consumers_read_files_the_model_catalogs(run):
    cfg, cur, data, model, manifest = run
    files = {f["path"] for f in model["files"]}
    consumers = {c["id"] for c in model["consumers"]}
    assert files >= {r["file"] for c in model["consumers"] for r in c["reads"]}
    assert consumers >= {c for f in model["files"] for c in f["consumers"]}
    events = model["events"]["items"]
    assert {e["series"] for e in events} <= {s["id"] for s in model["dimensions"]["series"]}
    assert all(date.fromisoformat(e["date"]) <= AS_OF for e in events)


# ------------------------------------------------------------------ incidencia del CLS

def test_cls_issue_is_recorded_as_resolved_with_its_evidence(run):
    cfg, cur, data, model, manifest = run
    issue = by_id(model["known_issues"])["cls_en_us_0_022"]
    assert issue["status"] == "resolved" and issue["resolved_on"] == "2026-10-17" and issue["affects"] == "dashboard"
    assert issue["reported"]["value"] == 0.022 and issue["reported"]["locale"] == "en-US"
    assert issue["metric"]["good_max"] == 0.1 and issue["metric"]["poor_above"] == 0.25 and issue["metric"]["percentile"] == 75
    assert issue["resolution"]["commit"] == "jaforero/dashboards@0a25d85"
    m = by_id(issue["measurements"])
    assert (m["lab_mobile_en"]["before"], m["lab_mobile_en"]["after"]) == (0.022, 0.0)
    assert m["stress_sweep_194"]["cases"] == 194
    assert (m["stress_sweep_194"]["before"]["sum"], m["stress_sweep_194"]["after"]["sum"]) == (8.64, 0.261)
    assert m["live_check"]["kind"] == "live" and m["live_check"]["after"] == {"locale_es": 0, "locale_en": 0}
    # lo que no se ha podido verificar queda dicho, no escondido
    assert issue["field_validation"]["status"] == "pending"
    assert len(issue["residual_risks"]) >= 3
    dash = by_id(model["consumers"])["dashboard"]
    assert dash["experience_quality"]["layout_stability"]["issue"] == "cls_en_us_0_022"
    assert any(g["term"] == "CLS" for g in model["glossary"])


# ------------------------------------------------------------------ robustez en la corrida diaria

def test_missing_curation_does_not_stop_publication(env):
    cfg, data = env
    cfg = {**cfg, "_curation": None, "_curation_problem": "no existe semantic.json"}
    res = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert res["overall_status"] == "ok"
    model = json.loads((data / "semantic_model.json").read_text(encoding="utf-8"))
    assert model["curation"]["complete"] is False and "no existe semantic.json" in model["curation"]["gaps"]
    assert len(model["dimensions"]["series"]) == 31, "la estructura sale de sources.json aunque falte la curaduría"
    assert (data / "latest.json").exists()


def test_unreadable_curation_is_reported_not_raised(tmp_path):
    cfgdir = tmp_path / "config"
    cfgdir.mkdir()
    (cfgdir / "sources.json").write_text((ROOT / "config" / "sources.json").read_text(encoding="utf-8"), encoding="utf-8")
    (cfgdir / "semantic.json").write_text("{ no es json", encoding="utf-8")
    cfg = pipeline.load_config(cfgdir / "sources.json")
    assert cfg["_curation"] is None and "ilegible" in cfg["_curation_problem"]
    (cfgdir / "semantic.json").unlink()
    cfg = pipeline.load_config(cfgdir / "sources.json")
    assert cfg["_curation"] is None and "no existe" in cfg["_curation_problem"]


def test_generator_failure_never_blocks_the_data(env, monkeypatch):
    cfg, data = env

    def boom(*a, **k):
        raise RuntimeError("fallo simulado del generador")
    monkeypatch.setattr(semantic, "build_model", boom)
    res = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert res["overall_status"] == "ok"
    for name in ("latest.json", "rates_daily.json", "rates_daily_compact.json"):
        assert (data / name).exists()
    assert not (data / "semantic_model.json").exists()
    assert "semantic_model.json" not in res["manifest"]["files"]


# ------------------------------------------------------------------ documentación

def test_documentation_tables_are_in_sync_with_the_model(run):
    cfg, cur, data, model, manifest = run
    doc = (ROOT / "docs" / "MODELO_SEMANTICO.md").read_text(encoding="utf-8")
    blocks = semantic.render_doc_blocks(model)
    assert semantic.sync_doc(doc, blocks) == doc, "docs/MODELO_SEMANTICO.md desactualizado: python -m fxpipe semantic --write-docs"
    with pytest.raises(ValueError):
        semantic.sync_doc("sin marcadores", blocks)
