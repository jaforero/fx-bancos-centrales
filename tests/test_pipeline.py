import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from fxpipe import pipeline, transform
from fxpipe.providers import ContractError
from fxpipe.providers.banxico import parse_payload
from fxpipe.providers.socrata_trm import expand_records, market_date_records

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
AS_OF = date(2026, 10, 4)  # domingo


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fake_http(banxico=None, trm=None, fail=None):
    def get(url, headers):
        if "banxico" in url:
            if fail == "banxico":
                from fxpipe.providers import ProviderError
                raise ProviderError("simulado: HTTP 503")
            return banxico if banxico is not None else load("banxico_ok.json")
        return trm if trm is not None else load("trm_ok.json")
    return get


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("BANXICO_TOKEN", "token-de-prueba")
    cfg = pipeline.load_config(ROOT / "config" / "sources.json")
    return cfg, tmp_path


# ---------- Conectores ----------

def test_banxico_parse_handles_ne_and_dates():
    out = parse_payload(load("banxico_ok.json"))
    assert out["SF43718"]["2026-10-02"] == 18.2
    assert "2026-09-28" not in out["SF60653"]  # N/E se descarta


def test_banxico_contract_drift_detected():
    with pytest.raises(ContractError):
        parse_payload({"data": []})
    with pytest.raises(ContractError):
        parse_payload({"bmx": {"series": [{"idSerie": "SF43718", "datos": [{"fecha": "2026-10-02", "dato": "1"}]}]}})


def test_trm_expands_weekend_vigencia():
    out = expand_records(load("trm_ok.json"), date(2026, 9, 1), date(2026, 10, 31))
    assert out["2026-10-03"] == out["2026-10-04"] == out["2026-10-05"] == 3273.49


def test_trm_contract_drift_detected():
    with pytest.raises(ContractError):
        expand_records([{"value": "1", "fecha": "2026-10-01"}], date(2026, 1, 1), date(2026, 12, 31))


def test_trm_market_date_index():
    out = market_date_records(load("trm_ok.json"), date(2026, 9, 1), date(2026, 10, 31))
    assert out["2026-10-02"] == 3273.49          # viernes: mercado que fijó la TRM sáb-lun
    assert out["2026-09-28"] == 3349.63
    assert "2026-10-03" not in out and "2026-10-04" not in out


def test_config_rejects_misaligned_cross(tmp_path):
    cfg = json.loads((ROOT / "config" / "sources.json").read_text())
    cfg["derived"]["mxn_cop_cross"]["numerator"] = "usd_cop_trm"  # vigencia vs determinación
    p = tmp_path / "c.json"
    p.write_text(json.dumps(cfg))
    from fxpipe.providers import ConfigError
    with pytest.raises(ConfigError):
        pipeline.load_config(p)


# ---------- Transformaciones ----------

def test_fill_forward_limits():
    s = transform.fill_forward({"2026-10-01": 1.0}, date(2026, 10, 10), max_fill_days=3)
    assert s["2026-10-04"] == {"value": 1.0, "filled": True}
    assert "2026-10-05" not in s  # supera el límite: hueco explícito


def test_cross_rate():
    num = {"2026-10-01": {"value": 3300.0, "filled": False}}
    den = {"2026-10-01": {"value": 18.0, "filled": True}}
    out = transform.cross_rate(num, den, 4)
    assert out["2026-10-01"] == {"value": round(3300 / 18, 4), "filled": True}


def test_snapshot_compares_against_previous_record_not_same_vigencia():
    s = transform.fill_forward(expand_records(load("trm_ok.json"), date(2026, 9, 1), date(2026, 10, 31)), AS_OF, 5)
    snap = transform.snapshot(s, AS_OF)
    assert snap["value"] == 3273.49 and snap["prev_value"] == 3307.73
    assert "next" not in snap  # 5-oct pertenece a la misma vigencia


# ---------- Pipeline completo ----------

def test_end_to_end_and_idempotent(env):
    cfg, data = env
    r1 = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert r1["overall_status"] == "ok" and r1["changed"]
    latest = json.loads((data / "latest.json").read_text())
    cross = latest["series"]["mxn_cop_cross"]
    # Misma fecha de mercado: TRM calculada con el mercado del 2-oct (vigente 3..5-oct)
    # dividida por el FIX determinado el 2-oct.
    assert cross["date"] == "2026-10-02" and cross["value"] == round(3273.49 / 18.20, 4)
    assert cross["prev_date"] == "2026-10-01" and cross["prev_value"] == round(3307.73 / 18.25, 4)
    wide = json.loads((data / "rates_daily.json").read_text())
    row = next(r for r in wide["rows"] if r["date"] == "2026-10-04")
    assert "usd_mxn_fix" in row["filled"] and "usd_cop_trm" not in row["filled"]

    r2 = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert not r2["changed"]


def test_provider_failure_keeps_history(env):
    cfg, data = env
    pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    before = (data / "series" / "usd_mxn_fix.json").read_text()
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="banxico"))
    assert r["overall_status"] == "degraded"
    assert r["manifest"]["series_status"]["usd_mxn_fix"] == "error:provider"
    assert r["manifest"]["series_status"]["usd_cop_trm"] == "ok"
    assert r["manifest"]["series_status"]["mxn_cop_cross"] == "error:provider"
    assert (data / "series" / "usd_mxn_fix.json").read_text() == before


def test_out_of_range_rejected(env):
    cfg, data = env
    bad = load("banxico_ok.json")
    bad["bmx"]["series"][0]["datos"].append({"fecha": "03/10/2026", "dato": "182.000"})  # error de escala
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http(banxico=bad))
    assert r["manifest"]["series_status"]["usd_mxn_fix"] == "error:validation"


def test_missing_token_is_reported(env, monkeypatch):
    cfg, data = env
    monkeypatch.delenv("BANXICO_TOKEN")
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert r["manifest"]["series_status"]["usd_mxn_fix"] == "error:provider"
    assert "BANXICO_TOKEN" in r["manifest"]["errors"]["usd_mxn_fix"]


def test_stale_detection(env):
    cfg, data = env
    r = pipeline.run(cfg, data, date(2026, 10, 20), http_get=fake_http())
    assert r["manifest"]["series_status"]["usd_mxn_fix"] == "stale"


def test_outputs_match_schema(env):
    jsonschema = pytest.importorskip("jsonschema")
    cfg, data = env
    pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    for name in ("latest", "rates_daily"):
        schema = json.loads((ROOT / "schema" / f"{name}.schema.json").read_text())
        jsonschema.validate(json.loads((data / f"{name}.json").read_text()), schema)
