"""Pruebas de la capa Kaggle (scripts/build_kaggle_dataset.py).

Construyen el paquete a partir de los productos canónicos reales de data/ y comprueban que la
transformación JSON → CSV no pierde, no inventa y no reinterpreta nada: mismas series, mismos
valores sin redondeo nuevo, las mismas marcas `filled`, cruzadas idénticas a las canónicas,
estados reconciliados con latest.json y manifest.json, y compuertas de licencias y de procedencia.
"""
import copy
import csv
import importlib.util
import json
from datetime import date
from pathlib import Path

import pytest

from fxpipe import transform

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("build_kaggle_dataset", ROOT / "scripts" / "build_kaggle_dataset.py")
bk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bk)


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    dist = tmp_path_factory.mktemp("dist")
    old = bk.DIST
    bk.DIST = dist
    try:
        r = bk.build(dist / "kaggle", "jaforero/latin-america-central-bank-fx-rates", "private", None, False)
    finally:
        bk.DIST = old
    assert not r["data_fail"], r["checks"]
    return r, bk.load_canonical()


def rows(path: Path) -> list[dict]:
    with path.open(encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def out(built) -> Path:
    return built[0]["out"]


# ------------------------------------------------------------------ cobertura y claves

def test_every_series_is_exported(built):
    r, c = built
    cfg_ids = list(c["config"]["series"]) + list(c["config"]["derived"])
    meta = rows(out(built) / bk.META)
    assert [m["series_id"] for m in meta] == cfg_ids
    daily_ids = {x["series_id"] for x in rows(out(built) / bk.DAILY)}
    for sid in cfg_ids:  # una fuente caída no hace desaparecer la serie
        assert sid in daily_ids or c["manifest"]["series_status"][sid] == "error:empty", sid
    assert r["n_series"] == len(cfg_ids)


def test_long_key_is_unique_and_values_are_valid(built):
    _, c = built
    idx = bk.model_index(c)
    seen = set()
    domains = {k: {d["value"] for d in idx["domains"][k]} for k in ("date_basis", "series_kind", "rate_type")}
    for x in rows(out(built) / bk.DAILY):
        key = (x["date"], x["series_id"])
        assert key not in seen
        seen.add(key)
        date.fromisoformat(x["date"])
        assert float(x["value"]) > 0
        assert x["pair"] == f"{x['base_currency']}-{x['quote_currency']}"
        assert x["base_currency"] in idx["currencies"] and x["quote_currency"] in idx["currencies"]
        assert x["date_basis"] in domains["date_basis"]
        assert x["series_kind"] in domains["series_kind"]
        assert x["rate_type"] in domains["rate_type"]
        assert x["filled"] in ("true", "false") and x["primary"] in ("true", "false")


# ------------------------------------------------------------------ reconciliación con el JSON canónico

def test_values_match_canonical_exactly_without_rounding(built):
    _, c = built
    canon = {(r["date"], s): r[s] for r in c["rates"]["rows"] for s in c["rates"]["series"] if r[s] is not None}
    daily = rows(out(built) / bk.DAILY)
    assert len(daily) == len(canon)
    for x in daily:
        v = canon[(x["date"], x["series_id"])]
        assert float(x["value"]) == v
        assert x["value"] == repr(float(v))  # mismo texto que el float canónico: ningún redondeo nuevo


def test_filled_flags_are_preserved_exactly(built):
    _, c = built
    canon = {(r["date"], s) for r in c["rates"]["rows"] for s in r.get("filled", [])}
    daily = {(x["date"], x["series_id"]) for x in rows(out(built) / bk.DAILY) if x["filled"] == "true"}
    assert daily == canon
    wide = rows(out(built) / bk.WIDE)
    assert [x["filled_series"].split(";") if x["filled_series"] else [] for x in wide] == \
           [r.get("filled", []) for r in c["rates"]["rows"]]


def test_wide_matches_rates_daily(built):
    _, c = built
    wide = rows(out(built) / bk.WIDE)
    assert len(wide) == len(c["rates"]["rows"])
    for x, r in zip(wide, c["rates"]["rows"]):
        assert x["date"] == r["date"]
        for s in c["rates"]["series"]:
            assert (float(x[s]) if x[s] else None) == r[s]


@pytest.mark.parametrize("sid", ["mxn_cop_cross", "clp_ars_cross", "brl_uyu_cross", "pen_uyu_cross"])
def test_cross_rate_samples_match_canonical(built, sid):
    _, c = built
    der = c["config"]["derived"][sid]
    by_date = {r["date"]: r for r in c["rates"]["rows"]}
    sample = [x for x in rows(out(built) / bk.DAILY) if x["series_id"] == sid]
    assert sample
    for x in sample[:: max(1, len(sample) // 50)] + sample[-5:]:
        r = by_date[x["date"]]
        assert float(x["value"]) == r[sid]
        assert r[sid] == transform.round_js(r[der["numerator"]] / r[der["denominator"]], der["decimals"])
        assert (x["filled"] == "true") == (der["numerator"] in r.get("filled", []) or der["denominator"] in r.get("filled", []))
        assert x["series_kind"] == "cross" and x["rate_type"] == "cross_rate"


def test_cross_check_detects_a_tampered_value(built):
    _, c = built
    assert bk.check_crosses(c) == []
    bad = copy.deepcopy(c)
    row = next(r for r in bad["rates"]["rows"] if r["mxn_cop_cross"] is not None)
    row["mxn_cop_cross"] += 0.0001
    assert any("mxn_cop_cross" in e for e in bk.check_crosses(bad))


def test_crosses_are_never_labelled_official(built):
    for m in rows(out(built) / bk.META):
        if m["series_kind"] == "cross":
            assert m["official_or_calculated"] == "calculated" and not m["provider"] and not m["institution"]
            assert m["formula"] == f"{m['numerator_series']} / {m['denominator_series']}"
        else:
            assert m["official_or_calculated"] == "official" and m["provider"] and not m["formula"]


# ------------------------------------------------------------------ metadatos, modelo semántico y estado

def test_metadata_matches_config_and_semantic_model(built):
    _, c = built
    meta = {m["series_id"]: m for m in rows(out(built) / bk.META)}
    model_ids = {s["id"] for s in c["model"]["dimensions"]["series"]}
    assert set(meta) == model_ids == set(c["rates"]["series"])  # sin huérfanas
    for sid, d in c["config"]["derived"].items():
        assert (meta[sid]["numerator_series"], meta[sid]["denominator_series"]) == (d["numerator"], d["denominator"])
        assert meta[sid]["date_basis"] == d["date_basis"]
    for sid, s in c["config"]["series"].items():
        assert meta[sid]["provider"] == s["provider"] and meta[sid]["source_series_id"] == s["source_series_id"]
        assert meta[sid]["date_basis"] == s["date_basis"]
    cov = {x["id"]: x for x in c["model"]["coverage"]["series"]}
    counts = {}
    for x in rows(out(built) / bk.DAILY):
        counts[x["series_id"]] = counts.get(x["series_id"], 0) + 1
    for sid, m in meta.items():
        assert int(m["observation_count"]) == cov[sid]["observations"] == counts.get(sid, 0)


def test_snapshot_reconciles_with_latest_and_manifest(built):
    _, c = built
    snap = rows(out(built) / bk.SNAP)
    assert [x["series_id"] for x in snap] == list(c["latest"]["series"])
    for x in snap:
        e = c["latest"]["series"][x["series_id"]]
        assert x["status"] == e["status"] == c["manifest"]["series_status"][x["series_id"]]
        assert (float(x["value"]) if x["value"] else None) == e.get("value")
        assert (x["date"] or None) == e.get("date")
        assert (x["error"] or None) == c["manifest"]["errors"].get(x["series_id"])
        assert (x["next_date"] or None) == (e.get("next") or {}).get("date")


def test_copies_of_semantic_model_and_manifest_are_identical(built):
    for name in ("semantic_model.json", "manifest.json"):
        assert (out(built) / name).read_bytes() == (ROOT / "data" / name).read_bytes()


def test_data_dictionary_documents_every_column(built):
    _, c = built
    text = (out(built) / "DATA_DICTIONARY.md").read_text(encoding="utf-8")
    for name in (bk.DAILY, bk.META, bk.SNAP):
        with (out(built) / name).open(encoding="utf-8") as fh:
            for col in next(csv.reader(fh)):
                assert f"`{col}`" in text, (name, col)


# ------------------------------------------------------------------ compuertas

def test_contract_check_rejects_incompatible_schema(built):
    _, c = built
    bad = copy.deepcopy(c)
    bad["rates"]["schema_version"] = "2.0.0"
    assert any("schema_version" in e for e in bk.check_contract(bad))
    bad = copy.deepcopy(c)
    bad["rates"]["series"].pop("mxn_cop_cross")
    assert any("rates_daily.json#series" in e for e in bk.check_contract(bad))


def test_history_loss_against_previous_version_is_detected(built, tmp_path):
    meta = rows(out(built) / bk.META)
    prev = copy.deepcopy(meta)
    prev[0]["official_count"] = str(int(prev[0]["official_count"]) + 1)
    prev.append({**prev[1], "series_id": "usd_xxx_gone"})
    with (tmp_path / bk.META).open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(meta[0]))
        w.writeheader()
        w.writerows(prev)
    errs = bk.check_against_previous(tmp_path, out(built))
    assert any("usd_xxx_gone" in e for e in errs) and any(meta[0]["series_id"] in e for e in errs)
    assert bk.check_against_previous(out(built), out(built)) == []


def test_license_gate_covers_every_provider_and_never_defaults_to_cc0(built):
    r, c = built
    assert set(c["config"]["providers"]) <= set(c["licenses"]["providers"])
    meta = json.loads((out(built) / "dataset-metadata.json").read_text(encoding="utf-8"))
    assert meta["licenses"] == [{"name": c["licenses"]["kaggle_license"]}]
    assert meta["licenses"][0]["name"] != "CC0-1.0"
    if r["license"]["status"] == "BLOCKED":
        assert (out(built) / "PUBLICATION_BLOCKERS.md").exists()
        blocked = {b["provider"] for b in r["license"]["blockers"]}
        for p, e in c["licenses"]["providers"].items():
            if e["redistribution"] == "prohibited_without_authorization":
                assert p in blocked


def test_public_visibility_requires_license_pass(built, tmp_path):
    r, _ = built
    if r["license"]["status"] == "PASS":
        pytest.skip("la compuerta de licencias pasa")
    old = bk.DIST
    bk.DIST = tmp_path
    try:
        with pytest.raises(bk.ValidationFailed):
            bk.build(tmp_path / "kaggle", "jaforero/latin-america-central-bank-fx-rates", "public", None, False)
    finally:
        bk.DIST = old


def test_kaggle_metadata_is_valid_and_private_by_default(built):
    meta = json.loads((out(built) / "dataset-metadata.json").read_text(encoding="utf-8"))
    assert bk.check_kaggle_metadata(meta, out(built)) == []
    assert meta["isPrivate"] is True  # `kaggle datasets metadata --update` trata la ausencia como pública
    assert meta["expectedUpdateFrequency"] == "daily"
    assert {r["path"] for r in meta["resources"]} >= {bk.DAILY, bk.META, bk.SNAP}
    for f in out(built).glob("*.md"):
        assert "${" not in f.read_text(encoding="utf-8"), f.name


def test_provenance_and_change_detection(built, tmp_path):
    r, c = built
    sc = bk.read_kv(out(built) / "SOURCE_COMMIT.txt")
    for k in ("repository", "commit_sha", "schema_version", "config_version", "generated_at", "data_as_of",
              "data_hash", "package_hash"):
        assert sc.get(k), k
    assert sc["data_hash"] == c["manifest"]["data_hash"] and sc["package_hash"] == r["package_hash"]
    assert bk.package_hash(out(built)) == r["package_hash"]  # determinista
    old = bk.DIST
    bk.DIST = tmp_path
    try:
        again = bk.build(tmp_path / "kaggle", "jaforero/latin-america-central-bank-fx-rates", "private", out(built), False)
    finally:
        bk.DIST = old
    assert again["package_hash"] == r["package_hash"] and again["update_required"] is False


def test_version_message_does_not_hide_degraded_sources(built):
    r, _ = built
    msg = r["version_message"]
    assert msg.startswith(f"FX data through {r['fresh']['as_of']}") and r["prov"]["commit_sha"][:7] in msg
    for i in r["fresh"]["provider_issues"]:
        assert i["provider"].upper() in msg and "historical data retained" in msg


def test_no_secrets_in_package(built):
    """Ningún valor de secreto viaja en el paquete. El modelo semántico nombra las variables (token_env),
    lo que no es un secreto; el resto de archivos ni siquiera las nombra."""
    import os
    names = ("BANXICO_TOKEN", "BCCH_TOKEN", "DATOS_GOV_CO_APP_TOKEN", "KAGGLE_API_TOKEN", "KAGGLE_KEY")
    values = [os.environ[n] for n in names if len(os.environ.get(n, "")) >= 8]
    for f in out(built).iterdir():
        text = f.read_text(encoding="utf-8", errors="ignore")
        assert not any(v in text for v in values), f.name
        if f.name in ("dataset-metadata.json", "SOURCE_COMMIT.txt") or f.suffix == ".csv":
            assert not any(n in text for n in names), f.name
