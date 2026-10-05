import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from fxpipe import pipeline, transform
from fxpipe.providers import ContractError
from fxpipe.providers.banxico import parse_payload
from fxpipe.providers.bcb_ptax import parse_payload as bcb_parse
from fxpipe.providers.banrep_sdmx import market_dates_from_daily, parse_generic_xml
from fxpipe.providers.bcra_cambiarias import parse_payload as bcra_parse
from fxpipe.providers.socrata_trm import expand_records, market_date_records

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
AS_OF = date(2026, 10, 4)  # domingo


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def fake_http(banxico=None, trm=None, bcra=None, banrep=None, bcb=None, fail=None):
    def get(url, headers):
        if "olinda.bcb.gov.br" in url:
            if fail == "bcb":
                from fxpipe.providers import ProviderError
                raise ProviderError("simulado: HTTP 503 BCB")
            return bcb if bcb is not None else load("bcb_ptax_ok.json")
        if "banrep" in url:
            if fail in ("banrep", "colombia"):
                from fxpipe.providers import ProviderError
                raise ProviderError("simulado: HTTP 503 BanRep")
            return banrep if banrep is not None else (FIX / "banrep_trm_ok.xml").read_text()
        if "datos.gov.co" in url and fail == "colombia":
            from fxpipe.providers import ProviderError
            raise ProviderError("simulado: HTTP 503 datos.gov.co")
        if "bcra" in url:
            if fail == "bcra":
                from fxpipe.providers import ProviderError
                raise ProviderError("simulado: HTTP 403")
            return bcra if bcra is not None else load("bcra_ref_ok.json")
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
    from fxpipe.providers import bcra_cambiarias
    monkeypatch.setattr(bcra_cambiarias, "today_buenos_aires", lambda: date(2026, 12, 31))
    from fxpipe.providers import bcb_ptax
    monkeypatch.setattr(bcb_ptax, "today_brasilia", lambda: date(2026, 12, 31))
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


def test_bcra_parse_and_contract():
    rows, n, total = bcra_parse(load("bcra_ref_ok.json"), "REF")
    assert rows["2026-10-02"] == 1523.0868 and n == 5 and total == 5
    with pytest.raises(ContractError):  # moneda distinta a la pedida
        bcra_parse(load("bcra_ref_ok.json"), "USD")
    with pytest.raises(ContractError):  # error declarado por la API
        bcra_parse({"status": 400, "errorMessages": ["Parámetro erróneo"]}, "REF")


def test_bcra_paginates_and_clamps_future(monkeypatch):
    from fxpipe.providers import bcra_cambiarias
    monkeypatch.setattr(bcra_cambiarias, "today_buenos_aires", lambda: date(2026, 10, 2))
    full = load("bcra_ref_ok.json")
    calls = []

    def get(url, headers):
        calls.append(url)
        off = int(url.split("offset=")[1])
        page = full["results"][off:off + 3]
        return {"status": 200, "metadata": {"resultset": {"count": 5}}, "results": page}

    prov = bcra_cambiarias.BcraCambiariasProvider("bcra", {"base_url": "https://x/bcra", "max_days_per_request": 366}, http_get=get)
    out = prov.fetch([pipeline.FetchRequest("usd_ars_a3500", "REF")], date(2026, 9, 1), date(2026, 10, 11))
    assert len(out["usd_ars_a3500"]) == 5 and len(calls) == 2
    assert all("fechahasta=2026-10-02" in u for u in calls)  # no pide fechas futuras


def test_banrep_parse_and_contract():
    xml = (FIX / "banrep_trm_ok.xml").read_text()
    d = parse_generic_xml(xml)
    assert d["2026-10-04"] == 3273.49 and len(d) == 10
    with pytest.raises(ContractError):
        parse_generic_xml(xml.replace('Sender id="BANREP"', 'Sender id="OTRO"'))
    with pytest.raises(ContractError):
        parse_generic_xml(xml.replace('value="COP"', 'value="USD"'))
    with pytest.raises(ContractError):
        parse_generic_xml("<html>mantenimiento</html>")


def test_banrep_market_dates_match_sfc_rule():
    daily = parse_generic_xml((FIX / "banrep_trm_ok.xml").read_text())
    via_banrep = market_dates_from_daily(daily)
    via_sfc = market_date_records(load("trm_ok.json"), date(2026, 9, 1), date(2026, 10, 31))
    assert via_banrep == via_sfc  # misma regla: inicio de vigencia - 1 día


def test_colombia_attribution_banrep_with_sfc_verification(env):
    cfg, data = env
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    src = json.loads((data / "latest.json").read_text())["series"]["usd_cop_trm"]["source"]
    assert src["institution"].startswith("Banco de la República")
    assert "Superintendencia Financiera" in src["certified_by"]
    assert src["verified_against"]["provider"] == "sfc_trm" and src["fallback_used"] is False
    assert not r["manifest"]["warnings"].get("usd_cop_trm")  # BanRep y SFC coinciden


def test_banrep_down_uses_sfc_fallback_transparently(env):
    cfg, data = env
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="banrep"))
    st = r["manifest"]["series_status"]
    assert st["usd_cop_trm"] == st["usd_cop_trm_mkt"] == st["mxn_cop_cross"] == "ok"
    latest = json.loads((data / "latest.json").read_text())["series"]
    assert latest["usd_cop_trm"]["source"]["fallback_used"] is True
    assert any("respaldo" in w for w in r["manifest"]["warnings"]["usd_cop_trm"])
    assert latest["mxn_cop_cross"]["value"] == round(3273.49 / 18.20, 4)


def test_banrep_and_sfc_discrepancy_warned(env):
    cfg, data = env
    xml = (FIX / "banrep_trm_ok.xml").read_text().replace('"20261002" /><generic:ObsValue value="3307.73"',
                                                          '"20261002" /><generic:ObsValue value="3307.99"')
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http(banrep=xml))
    assert any("discrepancia" in w for w in r["manifest"]["warnings"]["usd_cop_trm"])


def test_colombia_fully_down_degrades(env):
    cfg, data = env
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="colombia"))
    assert r["manifest"]["series_status"]["usd_cop_trm"] == "error:provider"


def test_bcb_parse_real_response():
    recs = bcb_parse(load("bcb_ptax_real_2024.json"))
    assert len(recs) == 9 and recs["2024-01-02"]["cotacaoVenda"] == 4.8916
    assert recs["2024-01-11"]["cotacaoVenda"] == 4.8794  # publicada tarde (17:03): misma fecha
    with pytest.raises(ContractError):
        bcb_parse({"value": [{"cotacaoCompra": 5.0, "dataHoraCotacao": "2024-01-02 13:00:00"}]})
    with pytest.raises(ContractError):  # venta < compra: campos invertidos
        bcb_parse({"value": [{"cotacaoCompra": 5.1, "cotacaoVenda": 5.0, "dataHoraCotacao": "2024-01-02 13:00:00"}]})
    dup = {"value": [{"cotacaoCompra": 5.0, "cotacaoVenda": 5.01, "dataHoraCotacao": "2024-01-02 13:00:00.0"},
                     {"cotacaoCompra": 5.1, "cotacaoVenda": 5.11, "dataHoraCotacao": "2024-01-02 16:00:00.0"}]}
    assert bcb_parse(dup)["2024-01-02"]["cotacaoVenda"] == 5.11  # el registro más reciente


def test_bcb_url_and_clamp(monkeypatch):
    from fxpipe.providers import bcb_ptax
    monkeypatch.setattr(bcb_ptax, "today_brasilia", lambda: date(2026, 10, 2))
    seen = []
    prov = bcb_ptax.BcbPtaxProvider("bcb", {"base_url": "https://olinda.bcb.gov.br/x", "max_days_per_request": 366},
                                    http_get=lambda u, h: seen.append(u) or load("bcb_ptax_ok.json"))
    out = prov.fetch([pipeline.FetchRequest("usd_brl_ptax", "CotacaoDolarPeriodo", (("field", "cotacaoVenda"),))],
                     date(2026, 9, 1), date(2026, 10, 9))
    assert "@dataInicial=%2709-01-2026%27" in seen[0] and "@dataFinalCotacao=%2710-02-2026%27" in seen[0]
    assert out["usd_brl_ptax"]["2026-10-02"] == 5.275


def test_brazil_end_to_end_crosses(env):
    cfg, data = env
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert r["overall_status"] == "ok"
    L = json.loads((data / "latest.json").read_text())["series"]
    assert L["usd_brl_ptax"]["value"] == 5.275 and L["usd_brl_ptax"]["source"]["institution"].startswith("Banco Central do Brasil")
    assert L["brl_mxn_cross"]["value"] == round(18.20 / 5.275, 4) and L["brl_mxn_cross"]["pair"] == "BRL-MXN"
    assert L["brl_cop_cross"]["value"] == round(3273.49 / 5.275, 4)
    assert L["brl_ars_cross"]["value"] == round(1523.0868 / 5.275, 4)


def test_brazil_failure_isolated(env):
    cfg, data = env
    st = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="bcb"))["manifest"]["series_status"]
    assert st["usd_brl_ptax"] == st["brl_mxn_cross"] == st["brl_cop_cross"] == st["brl_ars_cross"] == "error:provider"
    assert st["usd_mxn_fix"] == st["usd_cop_trm"] == st["usd_ars_a3500"] == st["mxn_cop_cross"] == "ok"


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
    # Argentina: mismas reglas de día de mercado
    mxn_ars = latest["series"]["mxn_ars_cross"]
    assert mxn_ars["date"] == "2026-10-02" and mxn_ars["value"] == round(1523.0868 / 18.20, 4)
    ars_cop = latest["series"]["ars_cop_cross"]
    assert ars_cop["date"] == "2026-10-02" and ars_cop["value"] == round(3273.49 / 1523.0868, 4)
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


def test_bcra_failure_isolated(env):
    cfg, data = env
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="bcra"))
    st = r["manifest"]["series_status"]
    assert st["usd_ars_a3500"] == "error:provider"
    assert st["mxn_ars_cross"] == "error:provider" and st["ars_cop_cross"] == "error:provider"
    assert st["usd_mxn_fix"] == st["usd_cop_trm"] == st["mxn_cop_cross"] == "ok"


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
