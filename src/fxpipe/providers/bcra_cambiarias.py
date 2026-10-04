"""Conector para la API de Estadísticas Cambiarias v1.0 del BCRA (Argentina).

Manual:   https://www.bcra.gob.ar/archivos/Catalogo/Content/files/pdf/estadisticascambiarias-v1.pdf
Endpoint: /Cotizaciones/{codigo}?fechadesde=AAAA-MM-DD&fechahasta=AAAA-MM-DD&limit=N&offset=N
Auth:     no requerida (control de tráfico por IP).
Respuesta:
  {"status": 200,
   "metadata": {"resultset": {"count": N, "offset": 0, "limit": 500}},
   "results": [{"fecha": "AAAA-MM-DD",
                "detalle": [{"codigoMoneda": "REF", "descripcion": "...",
                             "tipoPase": 0.0, "tipoCotizacion": 1523.0868}]}]}

Particularidades verificadas (oct-2026):
- "REF" = DOLAR REFERENCIA COM 3500 (A 3500), en ARS por USD.
- El peso mexicano usa el código antiguo "MXP", no "MXN".
- `tipoCotizacion` = pesos argentinos por unidad; 0 significa "no aplica".
- Fechas futuras devuelven HTTP 400: el fin del rango se recorta al día
  actual en Buenos Aires.
"""
from __future__ import annotations

import urllib.parse
from datetime import date, datetime, timedelta, timezone

from .base import ContractError, FetchRequest, Provider

PAGE = 500          # el manual pide limit > 10 y < 1000
MAX_PAGES = 200     # salvaguarda contra paginación infinita


def today_buenos_aires() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Argentina/Buenos_Aires")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


class BcraCambiariasProvider(Provider):
    kind = "bcra_cambiarias"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        end = min(end, today_buenos_aires())
        out: dict[str, dict[str, float]] = {r.series_key: {} for r in requests}
        if start > end:
            return out
        headers = {"Accept": "application/json"}
        for r in requests:
            code = r.source_series_id
            for a, b in self.chunks(start, end):
                offset = 0
                for _ in range(MAX_PAGES):
                    params = {"fechadesde": a.isoformat(), "fechahasta": b.isoformat(),
                              "limit": PAGE, "offset": offset}
                    url = (f"{self.cfg['base_url']}/Cotizaciones/{urllib.parse.quote(code)}?"
                           f"{urllib.parse.urlencode(params)}")
                    rows, n_results, total = parse_payload(self.http_get(url, headers), code)
                    out[r.series_key].update(rows)
                    offset += n_results
                    if n_results == 0 or (total is not None and offset >= total):
                        break
                else:
                    raise ContractError(f"BCRA: paginación no terminó tras {MAX_PAGES} páginas")
        return out


def parse_payload(payload: object, code: str) -> tuple[dict[str, float], int, int | None]:
    """Devuelve (observaciones, resultados en esta página, total declarado)."""
    if not isinstance(payload, dict):
        raise ContractError("BCRA: la respuesta no es un objeto JSON")
    if payload.get("status") != 200:
        raise ContractError(f"BCRA: status {payload.get('status')} {payload.get('errorMessages')}")
    results = payload.get("results")
    if not isinstance(results, list):
        raise ContractError("BCRA: 'results' no es una lista")
    total = None
    try:
        total = int(payload["metadata"]["resultset"]["count"])
    except (KeyError, TypeError, ValueError):
        pass  # sin metadata: se pagina hasta recibir una página vacía

    rows: dict[str, float] = {}
    for item in results:
        try:
            raw_date, detalle = item["fecha"], item["detalle"]
        except (KeyError, TypeError) as exc:
            raise ContractError("BCRA: resultado sin 'fecha' o 'detalle'") from exc
        try:
            iso = date.fromisoformat(str(raw_date)[:10]).isoformat()
        except ValueError as exc:
            raise ContractError(f"BCRA: fecha con formato inesperado {raw_date!r}") from exc
        if not isinstance(detalle, list):
            raise ContractError("BCRA: 'detalle' no es una lista")
        for d in detalle:
            if not isinstance(d, dict) or "codigoMoneda" not in d or "tipoCotizacion" not in d:
                raise ContractError("BCRA: detalle sin 'codigoMoneda' o 'tipoCotizacion'")
            if d["codigoMoneda"] != code:
                raise ContractError(f"BCRA: se pidió {code} y llegó {d['codigoMoneda']}")
            try:
                val = float(d["tipoCotizacion"])
            except (TypeError, ValueError) as exc:
                raise ContractError(f"BCRA: tipoCotizacion no numérico {d['tipoCotizacion']!r}") from exc
            if val > 0:
                rows[iso] = val
    return rows, len(results), total
