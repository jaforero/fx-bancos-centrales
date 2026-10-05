"""Conectores para el Banco Central do Brasil (BCB).

1) PTAX (fuente principal) — servicio OData "Olinda":
   {base}/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)
         ?@dataInicial='MM-DD-AAAA'&@dataFinalCotacao='MM-DD-AAAA'&$format=json
   Respuesta: {"value": [{"cotacaoCompra": 5.33, "cotacaoVenda": 5.33,
                          "dataHoraCotacao": "AAAA-MM-DD HH:MM:SS.mmm"}]}
   Documentación: https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/aplicacao
   La PTAX de cierre se calcula con las consultas del mismo día (mercado del día D).

2) SGS (verificación y respaldo) — serie 1:
   {base}/bcdata.sgs.1/dados?formato=json&dataInicial=dd/MM/aaaa&dataFinal=dd/MM/aaaa
   Respuesta: [{"data": "dd/MM/aaaa", "valor": "5.3317"}]
   Metadatos oficiales: "Taxa de câmbio - Livre - Dólar americano (venda) - diário";
   desde marzo de 1992 se denomina taxa PTAX (fechamento).
   Desde 26-mar-2025 cada consulta admite como máximo 10 años.

Ninguno requiere token. Fechas futuras: se recorta el rango al día de hoy en São Paulo.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .base import ContractError, FetchRequest, Provider


def today_sao_paulo() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


class BcbPtaxProvider(Provider):
    kind = "bcb_olinda_ptax"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        end = min(end, today_sao_paulo())
        out: dict[str, dict[str, float]] = {r.series_key: {} for r in requests}
        if start > end:
            return out
        for r in requests:
            if r.source_series_id != "CotacaoDolarPeriodo":
                raise ContractError(f"BCB PTAX: recurso no soportado {r.source_series_id!r}")
            field = r.opt("field", "cotacaoVenda")
            for a, b in self.chunks(start, end):
                url = (f"{self.cfg['base_url']}/CotacaoDolarPeriodo("
                       f"dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
                       f"?@dataInicial=%27{a:%m-%d-%Y}%27&@dataFinalCotacao=%27{b:%m-%d-%Y}%27"
                       f"&$format=json&$select=cotacaoCompra,cotacaoVenda,dataHoraCotacao")
                out[r.series_key].update(parse_ptax(self.http_get(url, {"Accept": "application/json"}), field))
        return out


def parse_ptax(payload: object, field: str = "cotacaoVenda") -> dict[str, float]:
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
        raise ContractError("BCB PTAX: falta la lista 'value'")
    best: dict[str, tuple[str, float]] = {}
    for row in payload["value"]:
        if not isinstance(row, dict) or field not in row or "dataHoraCotacao" not in row:
            raise ContractError(f"BCB PTAX: registro sin '{field}' o 'dataHoraCotacao'")
        if row.get("tipoBoletim") not in (None, "Fechamento"):
            continue  # en recursos por moneda llegan también boletines intermedios
        ts = str(row["dataHoraCotacao"])
        try:
            iso = date.fromisoformat(ts[:10]).isoformat()
        except ValueError as exc:
            raise ContractError(f"BCB PTAX: fecha inesperada {ts!r}") from exc
        try:
            val = float(row[field])
        except (TypeError, ValueError) as exc:
            raise ContractError(f"BCB PTAX: valor no numérico {row[field]!r}") from exc
        if iso not in best or ts > best[iso][0]:  # si hubiera varios, el último del día es el cierre
            best[iso] = (ts, val)
    return {k: v for k, (_, v) in best.items()}


class BcbSgsProvider(Provider):
    kind = "bcb_sgs"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        end = min(end, today_sao_paulo())
        out: dict[str, dict[str, float]] = {r.series_key: {} for r in requests}
        if start > end:
            return out
        for r in requests:
            code = r.source_series_id
            if not code.isdigit():
                raise ContractError(f"BCB SGS: código de serie inválido {code!r}")
            for a, b in self.chunks(start, end):
                url = (f"{self.cfg['base_url']}/bcdata.sgs.{code}/dados?formato=json"
                       f"&dataInicial={a:%d/%m/%Y}&dataFinal={b:%d/%m/%Y}")
                out[r.series_key].update(parse_sgs(self.http_get(url, {"Accept": "application/json"})))
        return out


def parse_sgs(payload: object) -> dict[str, float]:
    if not isinstance(payload, list):
        raise ContractError("BCB SGS: se esperaba una lista")
    out: dict[str, float] = {}
    for row in payload:
        if not isinstance(row, dict) or "data" not in row or "valor" not in row:
            raise ContractError("BCB SGS: registro sin 'data' o 'valor'")
        try:
            iso = datetime.strptime(row["data"], "%d/%m/%Y").date().isoformat()
        except ValueError as exc:
            raise ContractError(f"BCB SGS: fecha inesperada {row['data']!r}") from exc
        try:
            out[iso] = float(str(row["valor"]).replace(",", "."))
        except ValueError as exc:
            raise ContractError(f"BCB SGS: valor no numérico {row['valor']!r}") from exc
    return out
