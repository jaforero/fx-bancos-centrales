"""Conector para el servicio web SDMX 2.1 del Banco de la República (Colombia).

Portal:   https://suameca.banrep.gov.co/estadisticas-economicas/ (sección "Servicios web SDMX")
Endpoint: {base}/data/ESTAT,{dataflow},1.0/all/ALL/?startPeriod=AAAA
Dataflow: DF_TRM_DAILY_HIST (TRM diaria, histórica)
Auth:     no requerida.
Formato:  SDMX-ML 2.1 GenericData (XML). Remitente id="BANREP".
          Una observación por día CALENDARIO (fecha de vigencia):
          <generic:Obs><generic:ObsDimension value="AAAAMMDD"/>
                       <generic:ObsValue value="3273.49"/>...</generic:Obs>

Contexto normativo (verificado):
- La Junta Directiva del Banco de la República define la TRM y su metodología
  (Res. Externa 1 de 2018, art. 40; Circular Reglamentaria DODM-146).
- La Superintendencia Financiera la calcula y certifica a diario.
- Desde el 1-nov-2016 el BanRep toma la TRM del servicio web de la SFC.
  Por eso el dato es idéntico al de datos.gov.co (verificado 13/13).

Notas de implementación:
- Solo se usa `startPeriod` con año (forma verificada en vivo); el filtro por
  fecha se hace localmente.
- `index: "market_date"`: se deriva el día de mercado como el día anterior al
  inicio de cada tramo de vigencia (mismo criterio que vigenciadesde - 1).
  El primer tramo de la descarga se descarta porque puede venir cortado.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta

from .base import ContractError, FetchRequest, Provider

NS = {
    "message": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "generic": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic",
}
ACCEPT = "application/vnd.sdmx.genericdata+xml;version=2.1"


class BanrepSdmxProvider(Provider):
    kind = "banrep_sdmx"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        cache: dict[str, dict[str, float]] = {}
        out: dict[str, dict[str, float]] = {}
        # 15 días de margen para reconstruir el tramo de vigencia en curso
        year = (start - timedelta(days=15)).year
        for r in requests:
            flow = r.source_series_id
            if flow not in cache:
                url = f"{self.cfg['base_url']}/data/ESTAT,{flow},1.0/all/ALL/?startPeriod={year}"
                cache[flow] = parse_generic_xml(self.http_get(url, {"Accept": ACCEPT}),
                                                self.cfg.get("expected_sender", "BANREP"))
            daily = cache[flow]
            mode = r.opt("index", "vigencia")
            if mode == "vigencia":
                rows = daily
            elif mode == "market_date":
                rows = market_dates_from_daily(daily)
            else:
                raise ContractError(f"BanRep: opción index desconocida {mode!r}")
            out[r.series_key] = {k: v for k, v in rows.items() if start <= date.fromisoformat(k) <= end}
        return out


def parse_generic_xml(payload: object, expected_sender: str = "BANREP") -> dict[str, float]:
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if not isinstance(payload, str):
        raise ContractError("BanRep: se esperaba XML (texto)")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ContractError(f"BanRep: XML inválido ({exc})") from exc
    if not root.tag.endswith("GenericData"):
        raise ContractError(f"BanRep: raíz inesperada {root.tag}")
    sender = root.find("message:Header/message:Sender", NS)
    if sender is None or sender.get("id") != expected_sender:
        raise ContractError(f"BanRep: remitente inesperado {sender.get('id') if sender is not None else None}")
    series = root.findall(".//generic:Series", NS)
    if len(series) != 1:
        raise ContractError(f"BanRep: se esperaba 1 serie y llegaron {len(series)}")
    keys = {v.get("id"): v.get("value") for v in series[0].findall("generic:SeriesKey/generic:Value", NS)}
    if keys.get("UNIT_MEASURE") != "COP" or keys.get("FREQ") != "D":
        raise ContractError(f"BanRep: unidad/frecuencia inesperadas {keys}")

    out: dict[str, float] = {}
    for obs in series[0].findall("generic:Obs", NS):
        dim = obs.find("generic:ObsDimension", NS)
        val = obs.find("generic:ObsValue", NS)
        if dim is None or val is None:
            raise ContractError("BanRep: observación sin ObsDimension u ObsValue")
        try:
            iso = datetime.strptime(dim.get("value", ""), "%Y%m%d").date().isoformat()
        except ValueError as exc:
            raise ContractError(f"BanRep: fecha inesperada {dim.get('value')!r}") from exc
        raw = val.get("value")
        if raw in (None, "", "NaN"):
            continue
        try:
            out[iso] = float(raw)
        except ValueError as exc:
            raise ContractError(f"BanRep: valor no numérico {raw!r}") from exc
    return out


def market_dates_from_daily(daily: dict[str, float]) -> dict[str, float]:
    """Día de mercado = día anterior al inicio de cada tramo de vigencia.

    Un tramo es una racha de días consecutivos con el mismo valor (p. ej. la
    TRM vigente sábado-lunes). Si dos tramos seguidos tienen el mismo valor se
    funden; el día de mercado faltante lo rellena el pipeline con ese mismo
    valor, así que el número resultante no cambia.
    """
    keys = sorted(daily)
    out: dict[str, float] = {}
    first_run = True
    for i, k in enumerate(keys):
        d = date.fromisoformat(k)
        starts_run = i == 0 or (d - date.fromisoformat(keys[i - 1])).days != 1 or daily[keys[i - 1]] != daily[k]
        if not starts_run:
            continue
        if first_run:  # la descarga puede empezar a mitad de un tramo
            first_run = False
            continue
        m = d - timedelta(days=1)
        if m.weekday() >= 5:
            raise ContractError(f"BanRep: día de mercado {m} cae en fin de semana; revisar regla de vigencia")
        out[m.isoformat()] = daily[k]
    return out
