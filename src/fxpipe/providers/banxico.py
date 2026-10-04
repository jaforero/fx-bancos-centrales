"""Conector para la API REST del SIE de Banco de México.

Documentación: https://www.banxico.org.mx/SieAPIRest/service/v1/
Endpoint:      /series/{id1,id2}/datos/{AAAA-MM-DD}/{AAAA-MM-DD}
Autenticación: encabezado Bmx-Token (token gratuito del portal SIE).
Respuesta:     {"bmx": {"series": [{"idSerie", "titulo",
                "datos": [{"fecha": "DD/MM/AAAA", "dato": "18.1234"}]}]}}
Valores no disponibles llegan como "N/E"; los miles pueden traer comas.
"""
from __future__ import annotations

from datetime import date, datetime

from .base import ContractError, FetchRequest, Provider, read_token


class BanxicoProvider(Provider):
    kind = "banxico_sie"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        token = read_token(self.cfg)
        by_source = {r.source_series_id: r.series_key for r in requests}
        ids = ",".join(by_source)
        headers = {"Bmx-Token": token or "", "Accept": "application/json"}
        out: dict[str, dict[str, float]] = {r.series_key: {} for r in requests}

        for a, b in self.chunks(start, end):
            url = f"{self.cfg['base_url']}/series/{ids}/datos/{a.isoformat()}/{b.isoformat()}"
            payload = self.http_get(url, headers)
            for sid, rows in parse_payload(payload).items():
                key = by_source.get(sid)
                if key is None:
                    continue  # serie no solicitada: se ignora
                out[key].update(rows)
        return out


def parse_payload(payload: object) -> dict[str, dict[str, float]]:
    """Valida la forma de la respuesta y normaliza fechas/valores."""
    try:
        series = payload["bmx"]["series"]  # type: ignore[index]
    except (KeyError, TypeError) as exc:
        raise ContractError("Banxico: falta 'bmx.series' en la respuesta") from exc
    if not isinstance(series, list):
        raise ContractError("Banxico: 'bmx.series' no es una lista")

    result: dict[str, dict[str, float]] = {}
    for s in series:
        if not isinstance(s, dict) or "idSerie" not in s:
            raise ContractError("Banxico: serie sin 'idSerie'")
        rows: dict[str, float] = {}
        for d in s.get("datos") or []:  # 'datos' se omite si no hay valores en el rango
            try:
                raw_date, raw_val = d["fecha"], d["dato"]
            except (KeyError, TypeError) as exc:
                raise ContractError("Banxico: observación sin 'fecha' o 'dato'") from exc
            try:
                iso = datetime.strptime(raw_date, "%d/%m/%Y").date().isoformat()
            except ValueError as exc:
                raise ContractError(f"Banxico: formato de fecha inesperado {raw_date!r}") from exc
            val = str(raw_val).replace(",", "").strip()
            if val.upper() in {"N/E", "NE", ""}:
                continue
            try:
                rows[iso] = float(val)
            except ValueError as exc:
                raise ContractError(f"Banxico: valor no numérico {raw_val!r}") from exc
        result[s["idSerie"]] = rows
    return result
