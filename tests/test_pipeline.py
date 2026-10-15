import json
import shutil
from datetime import date
from pathlib import Path

import pytest

from fxpipe import pipeline, transform
from fxpipe.providers import ContractError
from fxpipe.providers.banxico import parse_payload
from fxpipe.providers.bcb_ptax import parse_payload as bcb_parse
from fxpipe.providers.bcch_bde import market_dates_from_observations, parse_payload as bcch_parse
from fxpipe.providers.bcu_ws import parse_payload as bcu_parse
from fxpipe.providers.banrep_sdmx import market_dates_from_daily, parse_generic_xml
from fxpipe.providers.bcra_cambiarias import parse_payload as bcra_parse
from fxpipe.providers.socrata_trm import expand_records, market_date_records

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
AS_OF = date(2026, 10, 4)  # domingo


def load(name):
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def bcch_response(url, payload=None):
    """Simula la API BDE: devuelve solo las observaciones entre firstdate y lastdate."""
    from urllib.parse import parse_qs, urlsplit
    q = parse_qs(urlsplit(url).query)
    a, b = date.fromisoformat(q["firstdate"][0]), date.fromisoformat(q["lastdate"][0])
    doc = json.loads(json.dumps(payload if payload is not None else load("bcch_obs_ok.json")))
    from datetime import datetime
    doc["Series"]["Obs"] = [o for o in doc["Series"]["Obs"]
                            if a <= datetime.strptime(o["indexDateString"], "%d-%m-%Y").date() <= b]
    return doc


def bcu_values():
    """Valores reales de la serie 2225 (sep-oct 2026) tomados de tests/fixtures/bcu_sep_oct.xml."""
    return {k: v["TCV"] for k, v in bcu_parse((FIX / "bcu_sep_oct.xml").read_text(encoding="utf-8"), {2225})[2225].items()}


def bcu_response(data: bytes, values=None) -> str:
    """Simula el web service del BCU con el mismo formato de la respuesta real."""
    import re as _re
    body = data.decode("utf-8")
    a = date.fromisoformat(_re.search(r"<cot:FechaDesde>([\d-]+)<", body).group(1))
    b = date.fromisoformat(_re.search(r"<cot:FechaHasta>([\d-]+)<", body).group(1))
    vals = {k: v for k, v in (values or bcu_values()).items() if a <= date.fromisoformat(k) <= b}
    if not vals:
        return (FIX / "bcu_sin_cotizacion_real.xml").read_text(encoding="utf-8")
    real = (FIX / "bcu_cotizaciones_real.xml").read_text(encoding="utf-8")
    head, tail = real.split("\t\t\t\t\t<datoscotizaciones.dato", 1)[0], real.split("</datoscotizaciones.dato>\n")[-1]
    tpl = ("\t\t\t\t\t<datoscotizaciones.dato" + real.split("\t\t\t\t\t<datoscotizaciones.dato", 2)[1]
           ).replace("2026-10-01", "{f}").replace("40.463000", "{v}")
    return head + "".join(tpl.format(f=k, v=f"{v:.6f}") for k, v in sorted(vals.items())) + tail


def fake_http(banxico=None, trm=None, bcra=None, banrep=None, bcb=None, bcch=None, fail=None):
    def get(url, headers, data=None):
        if "bcu.gub.uy" in url:
            if fail == "bcu":
                from fxpipe.providers import ProviderError
                raise ProviderError("simulado: HTTP 503 BCU")
            return bcu_response(data)
        if "bcentral.cl" in url:
            if fail == "bcch":
                from fxpipe.providers import ProviderError
                raise ProviderError("simulado: HTTP 503 BCCh")
            return bcch_response(url, bcch)
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
    monkeypatch.setenv("BCCH_TOKEN", "token-bcch-de-prueba")
    from fxpipe.providers import bcra_cambiarias
    monkeypatch.setattr(bcra_cambiarias, "today_buenos_aires", lambda: date(2026, 12, 31))
    from fxpipe.providers import bcb_ptax
    monkeypatch.setattr(bcb_ptax, "today_brasilia", lambda: date(2026, 12, 31))
    from fxpipe.providers import bcch_bde
    monkeypatch.setattr(bcch_bde, "today_santiago", lambda: AS_OF)
    from fxpipe.providers import bcu_ws
    monkeypatch.setattr(bcu_ws, "today_montevideo", lambda: AS_OF)
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


# ---------- Chile: dólar observado (BCCh, API BDE) ----------

def test_bcch_parse_and_contract():
    obs = bcch_parse(load("bcch_obs_ok.json"), "F073.TCO.PRE.Z.D")
    assert len(obs) == 29 and obs["2026-09-21"] == 958.42 and obs["2026-10-13"] == 978.61
    assert "2026-09-18" not in obs and "2026-10-12" not in obs  # "ND"/"NaN": feriados bancarios
    with pytest.raises(ContractError):  # serie distinta a la pedida
        bcch_parse(load("bcch_obs_ok.json"), "F073.TCO.PRE.Z.M")
    with pytest.raises(ContractError):
        bcch_parse({"Codigo": 0, "Descripcion": "Success", "Series": {"seriesId": "F073.TCO.PRE.Z.D"}}, "F073.TCO.PRE.Z.D")
    with pytest.raises(ContractError):  # formato de fecha cambiado
        bcch_parse({"Codigo": 0, "Series": {"seriesId": "F073.TCO.PRE.Z.D", "Obs": [
            {"indexDateString": "2026-10-02", "value": "983.84", "statusCode": "OK"}]}}, "F073.TCO.PRE.Z.D")
    with pytest.raises(ContractError):
        bcch_parse("<html>mantenimiento</html>", "F073.TCO.PRE.Z.D")
    with pytest.raises(ContractError):  # código de estado nuevo: no se descarta en silencio
        bcch_parse({"Codigo": 0, "Series": {"seriesId": "F073.TCO.PRE.Z.D", "Obs": [
            {"indexDateString": "02-10-2026", "value": "983.84", "statusCode": "ok"}]}}, "F073.TCO.PRE.Z.D")


def test_bcch_api_error_is_operational_with_token_hint():
    from fxpipe.providers import ProviderError
    with pytest.raises(ProviderError) as exc:
        bcch_parse({"Codigo": -5, "Descripcion": "Invalid username or password"}, "F073.TCO.PRE.Z.D")
    assert not isinstance(exc.value, ContractError) and "BCCH_TOKEN" in str(exc.value)


def test_bcch_market_date_is_previous_banking_day():
    m = market_dates_from_observations(bcch_parse(load("bcch_obs_ok.json"), "F073.TCO.PRE.Z.D"))
    assert m["2026-10-02"] == 984.82   # vigente el lunes 5-oct: mercado del viernes 2
    assert m["2026-09-17"] == 958.42   # vigente el lunes 21-sep: mercado del jueves 17 (18-sep feriado)
    assert m["2026-10-09"] == 978.61   # vigente el martes 13-oct: mercado del viernes 9 (12-oct feriado)
    obs = bcch_parse(load("bcch_obs_ok.json"), "F073.TCO.PRE.Z.D")
    assert len(m) == 28 and set(m) == set(obs) - {"2026-10-13"}  # cada vigencia, salvo la última, es un día de mercado
    assert 933.40 not in m.values()  # el valor de la primera vigencia (1-sep) refleja el 31-ago, fuera de la ventana
    assert market_dates_from_observations({"2026-10-01": 1.0, "2026-10-08": 2.0}) == {"2026-10-01": 2.0}  # 7 días: se acepta
    assert market_dates_from_observations({"2026-10-01": 1.0, "2026-10-09": 2.0}) == {}  # 8 días: no adivina
    with pytest.raises(ContractError):  # una vigencia en sábado rompería la regla
        market_dates_from_observations({"2026-10-03": 1.0, "2026-10-05": 2.0})


def test_bcch_url_window_and_token_never_logged(env, monkeypatch):
    cfg, _ = env
    from urllib.parse import quote
    from fxpipe.providers import ProviderError, build_provider
    from fxpipe.providers.base import FetchRequest as FR
    seen = []
    prov = build_provider("bcch", cfg["providers"]["bcch"], http_get=lambda u, h: seen.append(u) or bcch_response(u))
    out = prov.fetch([FR("usd_clp_obs", "F073.TCO.PRE.Z.D"), FR("usd_clp_obs_mkt", "F073.TCO.PRE.Z.D", (("index", "market_date"),))],
                     date(2026, 9, 20), date(2026, 10, 11))
    # Dos descargas (pasado hasta hoy en Santiago = 4-oct, y vigencias futuras) sirven a las dos series
    assert len(seen) == 2 and all("timeseries=F073.TCO.PRE.Z.D" in u for u in seen)
    assert "firstdate=2026-09-20" in seen[0] and "lastdate=2026-10-04" in seen[0]
    assert "firstdate=2026-10-05" in seen[1] and "lastdate=2026-10-11" in seen[1]
    assert min(out["usd_clp_obs"]) == "2026-09-21" and max(out["usd_clp_obs"]) == "2026-10-09"
    assert min(out["usd_clp_obs_mkt"]) == "2026-09-21" and out["usd_clp_obs_mkt"]["2026-10-08"] == 981.61
    # Un token con caracteres reservados viaja codificado y nunca aparece en un mensaje de error
    tricky = "a&b#c+d/e"
    monkeypatch.setenv("BCCH_TOKEN", tricky)
    urls = []
    bad = build_provider("bcch", cfg["providers"]["bcch"], http_get=lambda u, h: urls.append(u) or
                         {"Codigo": -1, "Descripcion": f"Token inválido o expirado: {tricky} / {quote(tricky, safe='')}"})
    with pytest.raises(ProviderError) as exc:
        bad.fetch([FR("usd_clp_obs", "F073.TCO.PRE.Z.D")], date(2026, 10, 1), date(2026, 10, 2))
    assert "token=" + quote(tricky, safe="") in urls[0] and "#" not in urls[0]
    msg = str(exc.value)
    assert tricky not in msg and quote(tricky, safe="") not in msg and "BCCH_TOKEN" in msg


def test_bcch_future_window_is_best_effort(env):
    cfg, _ = env
    from fxpipe.providers import ProviderError, build_provider
    from fxpipe.providers.base import FetchRequest as FR

    def get(url, headers):
        if "lastdate=2026-10-11" in url:
            raise ProviderError("simulado: la API rechaza fechas futuras")
        return bcch_response(url)
    prov = build_provider("bcch", cfg["providers"]["bcch"], http_get=get)
    out = prov.fetch([FR("usd_clp_obs", "F073.TCO.PRE.Z.D")], date(2026, 9, 20), date(2026, 10, 11))
    assert max(out["usd_clp_obs"]) == "2026-10-02"  # lo pasado llega completo; el futuro llega en otra corrida


def test_latest_overall_status_ignores_hidden_series(env):
    cfg, data = env
    for sid in ("usd_clp_obs", "usd_clp_obs_mkt"):  # una fuente cuyas series no muestra el dashboard
        cfg["series"][sid]["primary"] = False
    for did in ("mxn_clp_cross", "clp_cop_cross", "clp_ars_cross", "brl_clp_cross"):
        cfg["derived"][did]["primary"] = False
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="bcch"))
    assert r["manifest"]["overall_status"] == "degraded"  # la alerta fx-alerta sí se dispara
    assert json.loads((data / "latest.json").read_text())["overall_status"] == "ok"  # el tablero no cambia


def test_http_get_retries_connection_reset(monkeypatch):
    from fxpipe.providers import ProviderError, base
    calls = []

    def boom(*a, **k):
        calls.append(1)
        raise ConnectionResetError("simulado")
    monkeypatch.setattr(base.urllib.request, "urlopen", boom)
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    with pytest.raises(ProviderError):
        base.default_http_get("https://x.test/?token=secreto", {"Accept": "application/json"})
    assert len(calls) == 4


def test_chile_end_to_end_crosses(env):
    cfg, data = env
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert r["overall_status"] == "ok"
    L = json.loads((data / "latest.json").read_text())["series"]
    obs = L["usd_clp_obs"]
    assert obs["value"] == 983.84 and obs["date"] == "2026-10-02" and obs["date_basis"] == "vigencia"
    assert obs["next"] == {"date": "2026-10-05", "value": 984.82}  # publicado el viernes 2 a las 17:30
    assert obs["source"]["country"] == "CL" and obs["source"]["institution"].startswith("Banco Central de Chile")
    assert obs["source"]["series_id"] == "F073.TCO.PRE.Z.D" and "Capítulo I" in obs["source"]["legal_basis"]
    mkt = L["usd_clp_obs_mkt"]
    assert mkt["date"] == "2026-10-02" and mkt["value"] == 984.82 and mkt["date_basis"] == "determinacion"
    # Cruzadas del mismo día de mercado (2-oct): el observado vigente el 5-oct ÷/× insumos del 2-oct
    assert L["mxn_clp_cross"]["date"] == "2026-10-02" and L["mxn_clp_cross"]["value"] == round(984.82 / 18.20, 4)
    assert L["clp_cop_cross"]["value"] == round(3273.49 / 984.82, 4) and L["clp_cop_cross"]["pair"] == "CLP-COP"
    assert L["clp_ars_cross"]["value"] == round(1523.0868 / 984.82, 6) and L["clp_ars_cross"]["decimals"] == 6
    assert L["brl_clp_cross"]["value"] == round(984.82 / 5.275, 4)
    assert all(L[i]["method"].startswith("cross_via_usd") for i in ("mxn_clp_cross", "clp_cop_cross", "clp_ars_cross", "brl_clp_cross"))


def test_chile_visible_in_dashboard(env):
    """config 1.6.0 (2026-10-12): el dashboard ya incorpora Chile; sus 5 series pasan a primary.
    El insumo por día de mercado sigue oculto, como usd_cop_trm_mkt."""
    cfg, data = env
    pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    L = json.loads((data / "latest.json").read_text())["series"]
    prim = sorted(k for k, v in L.items() if v["primary"])
    assert prim == sorted(["usd_mxn_fix", "usd_cop_trm", "usd_ars_a3500", "usd_brl_ptax", "usd_clp_obs",
                           "mxn_cop_cross", "mxn_ars_cross", "ars_cop_cross", "brl_mxn_cross", "brl_cop_cross",
                           "brl_ars_cross", "mxn_clp_cross", "clp_cop_cross", "clp_ars_cross", "brl_clp_cross"])
    assert L["usd_clp_obs_mkt"]["primary"] is False and L["usd_cop_trm_mkt"]["primary"] is False


def test_chile_failure_isolated(env):
    cfg, data = env
    st = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="bcch"))["manifest"]["series_status"]
    chile = ["usd_clp_obs", "usd_clp_obs_mkt", "mxn_clp_cross", "clp_cop_cross", "clp_ars_cross", "brl_clp_cross",
             "uyu_clp_cross"]  # la cruzada con Uruguay también depende del observado
    assert all(st[i] == "error:provider" for i in chile)
    assert all(v == "ok" for k, v in st.items() if k not in chile)


def test_chile_missing_token_is_reported(env, monkeypatch):
    cfg, data = env
    monkeypatch.delenv("BCCH_TOKEN")
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert r["manifest"]["series_status"]["usd_clp_obs"] == "error:provider"
    assert "BCCH_TOKEN" in r["manifest"]["errors"]["usd_clp_obs"]


BCCH_CTYPE = "application/json; charset=ISO-8859-1"  # encabezado real de la API BDE (2026-10-12)


def test_bcch_real_capture_matches_official_table():
    """La captura real (tests/fixtures/bcch_obs_real.json, ISO-8859-1) coincide con la tabla oficial."""
    from fxpipe.providers.base import decode_body
    p = FIX / "bcch_obs_real.json"
    if not p.exists():
        pytest.skip("sin captura real de la API BDE (ver README: Chile)")
    payload = json.loads(decode_body(p.read_bytes(), BCCH_CTYPE))
    assert "dólar observado" in payload["Series"]["descripEsp"]  # la tilde que rompía la lectura en UTF-8
    real = bcch_parse(payload, "F073.TCO.PRE.Z.D")
    ref = bcch_parse(load("bcch_obs_ok.json"), "F073.TCO.PRE.Z.D")
    common = sorted(set(real) & set(ref))
    assert common, "la captura no se solapa con sep-oct 2026; captura ese rango"
    assert {k: real[k] for k in common} == {k: ref[k] for k in common}


def test_decode_body_handles_latin1_and_gzip():
    import gzip as gz
    from fxpipe.providers.base import decode_body
    raw = (FIX / "bcch_obs_real.json").read_bytes()
    with pytest.raises(UnicodeDecodeError):  # sin charset declarado no se adivina
        decode_body(raw, "application/json")
    text = decode_body(raw, BCCH_CTYPE)
    assert decode_body(gz.compress(raw), BCCH_CTYPE, "gzip") == text
    assert decode_body(gz.compress(raw), BCCH_CTYPE, "") == text  # comprimido sin avisar
    assert decode_body("ñandú".encode("utf-8"), BCCH_CTYPE) == "ñandú"  # UTF-8 válido se respeta


def test_http_get_reads_real_bcch_bytes(monkeypatch):
    import gzip as gz
    from fxpipe.providers import base

    class Resp:
        def __init__(self, body, headers):
            self.body, self.headers = body, headers
        def read(self):
            return self.body
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
    raw = (FIX / "bcch_obs_real.json").read_bytes()
    monkeypatch.setattr(base.urllib.request, "urlopen",
                        lambda req, timeout: Resp(gz.compress(raw), {"Content-Type": BCCH_CTYPE, "Content-Encoding": "gzip"}))
    payload = base.default_http_get("https://si3.bcentral.cl/x?token=t", {"Accept": "application/json"})
    assert bcch_parse(payload, "F073.TCO.PRE.Z.D")["2026-10-13"] == 978.61


def test_bcch_real_error_response_is_operational():
    """Respuesta real de la API con un token inválido (HTTP 200, Codigo -5), capturada el 2026-10-12."""
    from fxpipe.providers import ProviderError
    from fxpipe.providers.base import decode_body
    payload = json.loads(decode_body((FIX / "bcch_error_real.json").read_bytes(), BCCH_CTYPE))
    with pytest.raises(ProviderError) as exc:
        bcch_parse(payload, "F073.TCO.PRE.Z.D")
    assert not isinstance(exc.value, ContractError)
    assert "Codigo=-5" in str(exc.value) and "BCCH_TOKEN" in str(exc.value)


# ---------- Uruguay: cotización interbancaria (BCU, web service SOAP) ----------

UY = ["usd_uyu_interbank", "mxn_uyu_cross", "uyu_cop_cross", "uyu_ars_cross", "brl_uyu_cross", "uyu_clp_cross"]


def test_bcu_parse_real_response():
    """Respuesta real del web service (serie 2225, 1 al 15-oct-2026), capturada el 2026-10-15."""
    rows = bcu_parse((FIX / "bcu_cotizaciones_real.xml").read_text(encoding="utf-8"), {2225})[2225]
    assert len(rows) == 10 and rows["2026-10-15"]["TCV"] == 40.135 and rows["2026-10-01"]["TCC"] == 40.463
    assert "2026-10-12" not in rows  # feriado en Uruguay: el BCU no publica
    assert bcu_parse((FIX / "bcu_sin_cotizacion_real.xml").read_text(encoding="utf-8"), {2225}) == {}  # codigoerror 100


def test_bcu_contract_and_operational_errors():
    from fxpipe.providers import ProviderError
    real = (FIX / "bcu_cotizaciones_real.xml").read_text(encoding="utf-8")
    err = (FIX / "bcu_sin_cotizacion_real.xml").read_text(encoding="utf-8")
    with pytest.raises(ContractError):  # moneda inexistente (101): la consulta cambió
        bcu_parse(err.replace("<codigoerror>100</codigoerror>", "<codigoerror>101</codigoerror>"), {2225})
    with pytest.raises(ProviderError) as exc:  # 106/107: servicio no disponible, transitorio
        bcu_parse(err.replace("<codigoerror>100</codigoerror>", "<codigoerror>107</codigoerror>"), {2225})
    assert not isinstance(exc.value, ContractError)
    with pytest.raises(ContractError):  # llega una moneda que no se pidió
        bcu_parse(real, {2230})
    with pytest.raises(ContractError):  # compra y venta distintas: revisar metodología
        bcu_parse(real.replace("<TCC>40.135000</TCC>", "<TCC>40.100000</TCC>"), {2225})
    with pytest.raises(ContractError):
        bcu_parse("<html>mantenimiento</html>", {2225})


def test_bcu_soap_request_and_window(env):
    cfg, _ = env
    from fxpipe.providers import build_provider
    from fxpipe.providers.base import FetchRequest as FR
    calls = []
    prov = build_provider("bcu", cfg["providers"]["bcu"],
                          http_get=lambda u, h, data=None: calls.append((u, h, data)) or bcu_response(data))
    out = prov.fetch([FR("usd_uyu_interbank", "2225", (("field", "TCV"),))], date(2026, 8, 20), date(2026, 10, 11))
    assert all(h["SOAPAction"] == "Cotizaaction/AWSBCUCOTIZACIONES.Execute" and b"<cot:item>2225</cot:item>" in d
               for _, h, d in calls)
    assert len(calls) == 2 and b"<cot:FechaDesde>2026-08-20<" in calls[0][2] and b"<cot:FechaHasta>2026-09-19<" in calls[0][2]
    assert b"<cot:FechaHasta>2026-10-04<" in calls[1][2]  # se recorta a hoy en Montevideo: no hay futuro
    assert out["usd_uyu_interbank"]["2026-10-02"] == 40.342 and max(out["usd_uyu_interbank"]) == "2026-10-02"


def test_http_get_posts_soap_body(monkeypatch):
    from fxpipe.providers import base
    seen = {}

    class Resp:
        headers = {"Content-Type": "text/xml;charset=utf-8"}
        def read(self):
            return (FIX / "bcu_cotizaciones_real.xml").read_bytes()
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout):
        seen.update(method=req.get_method(), data=req.data)
        return Resp()
    monkeypatch.setattr(base.urllib.request, "urlopen", fake_urlopen)
    txt = base.default_http_get("https://cotizaciones.bcu.gub.uy/x", {"Accept": "text/xml"}, data=b"<a/>")
    assert seen == {"method": "POST", "data": b"<a/>"} and "DLS. USA BILLETE" in txt


def test_uruguay_end_to_end_crosses(env):
    cfg, data = env
    r = pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    assert r["overall_status"] == "ok"
    L = json.loads((data / "latest.json").read_text())["series"]
    uy = L["usd_uyu_interbank"]
    assert uy["value"] == 40.342 and uy["date"] == "2026-10-02" and uy["date_basis"] == "determinacion"
    assert uy["source"]["country"] == "UY" and uy["source"]["institution"].startswith("Banco Central del Uruguay")
    assert uy["source"]["series_id"] == "2225" and "next" not in uy
    # Mismo día de mercado (2-oct) sin desplazar: el BCU fecha cada cotización por el día de sus operaciones
    assert L["mxn_uyu_cross"]["date"] == "2026-10-02" and L["mxn_uyu_cross"]["value"] == round(40.342 / 18.20, 4)
    assert L["uyu_cop_cross"]["value"] == round(3273.49 / 40.342, 4) and L["uyu_cop_cross"]["pair"] == "UYU-COP"
    assert L["uyu_ars_cross"]["value"] == round(1523.0868 / 40.342, 4)
    assert L["brl_uyu_cross"]["value"] == round(40.342 / 5.275, 4)
    assert L["uyu_clp_cross"]["value"] == round(984.82 / 40.342, 4)


def test_uruguay_hidden_until_dashboard_ready(env):
    """Como Chile: los datos se publican con primary:false hasta adaptar el tablero."""
    cfg, data = env
    pipeline.run(cfg, data, AS_OF, http_get=fake_http())
    L = json.loads((data / "latest.json").read_text())["series"]
    assert not any(L[i]["primary"] for i in UY)
    assert sum(1 for v in L.values() if v["primary"]) == 15


def test_uruguay_failure_isolated(env):
    cfg, data = env
    st = pipeline.run(cfg, data, AS_OF, http_get=fake_http(fail="bcu"))["manifest"]["series_status"]
    assert all(st[i] == "error:provider" for i in UY)
    assert all(v == "ok" for k, v in st.items() if k not in UY)


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
