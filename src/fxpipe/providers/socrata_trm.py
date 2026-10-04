"""Conector para la TRM de Colombia en datos.gov.co (API Socrata).

Dataset:  32sa-8pi3 (TRM certificada por la Superintendencia Financiera)
Registro: {"valor": "3273.49", "unidad": "COP",
           "vigenciadesde": "2026-10-03T00:00:00.000",
           "vigenciahasta": "2026-10-05T00:00:00.000"}
Cada registro cubre un rango de vigencia (fines de semana y festivos
incluidos), por eso se expande a un valor por día calendario: es el valor
oficialmente vigente, no un relleno del pipeline.

Opción `index: "market_date"`: indexa cada registro por la fecha del mercado
con que se calculó (vigenciadesde - 1 día), un valor por día hábil. Verificado
sobre 1.600 registros 2020-2026: esa fecha siempre cae de lunes a viernes.
Es la base que permite cruzar la TRM con el FIX el MISMO día de mercado.
"""
from __future__ import annotations

import urllib.parse
from datetime import date, datetime, timedelta

from .base import ContractError, FetchRequest, Provider, read_token


class SocrataTrmProvider(Provider):
    kind = "socrata_trm"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        token = read_token(self.cfg)
        headers = {"Accept": "application/json"}
        if token:
            headers["X-App-Token"] = token

        records: list = []
        for a, b in self.chunks(start, end):
            params = {
                "$where": f"vigenciahasta >= '{a.isoformat()}T00:00:00' AND vigenciadesde <= '{b.isoformat()}T00:00:00'",
                "$order": "vigenciadesde ASC",
                "$limit": "50000",
            }
            url = f"{self.cfg['base_url']}?{urllib.parse.urlencode(params)}"
            payload = self.http_get(url, headers)
            if not isinstance(payload, list):
                raise ContractError("TRM: se esperaba una lista de registros")
            records.extend(payload)
        out: dict[str, dict[str, float]] = {}
        for r in requests:
            mode = r.opt("index", "vigencia")
            if mode == "vigencia":
                out[r.series_key] = expand_records(records, start, end)
            elif mode == "market_date":
                out[r.series_key] = market_date_records(records, start, end)
            else:
                raise ContractError(f"TRM: opción index desconocida {mode!r}")
        return out


def _parse_ts(value: object, field: str) -> date:
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except ValueError as exc:
        raise ContractError(f"TRM: '{field}' con formato inesperado {value!r}") from exc


def expand_records(payload: object, start: date, end: date) -> dict[str, float]:
    if not isinstance(payload, list):
        raise ContractError("TRM: se esperaba una lista de registros")
    out: dict[str, float] = {}
    for rec in payload:
        if not isinstance(rec, dict):
            raise ContractError("TRM: registro no es un objeto")
        missing = {"valor", "vigenciadesde", "vigenciahasta"} - rec.keys()
        if missing:
            raise ContractError(f"TRM: faltan campos {sorted(missing)}")
        if rec.get("unidad", "COP") != "COP":
            raise ContractError(f"TRM: unidad inesperada {rec.get('unidad')!r}")
        try:
            val = float(str(rec["valor"]).replace(",", ""))
        except ValueError as exc:
            raise ContractError(f"TRM: valor no numérico {rec['valor']!r}") from exc
        d0 = _parse_ts(rec["vigenciadesde"], "vigenciadesde")
        d1 = _parse_ts(rec["vigenciahasta"], "vigenciahasta")
        if d1 < d0:
            raise ContractError(f"TRM: vigencia invertida {d0}..{d1}")
        d = max(d0, start)
        while d <= min(d1, end):
            out[d.isoformat()] = val
            d += timedelta(days=1)
    return out


def market_date_records(payload: object, start: date, end: date) -> dict[str, float]:
    """Un valor por día de mercado: fecha = vigenciadesde - 1 día."""
    vig = expand_records(payload, date.min, date.max)  # valida el contrato
    out: dict[str, float] = {}
    for rec in payload:  # type: ignore[union-attr]
        d0 = _parse_ts(rec["vigenciadesde"], "vigenciadesde")
        m = d0 - timedelta(days=1)
        if m.weekday() >= 5:
            raise ContractError(f"TRM: fecha de mercado {m} cae en fin de semana; revisar regla vigencia-1")
        if start <= m <= end:
            out[m.isoformat()] = vig[d0.isoformat()]
    return out
