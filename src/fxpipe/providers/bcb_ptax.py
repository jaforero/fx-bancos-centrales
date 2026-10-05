"""Conector para la API PTAX (Olinda, OData) del Banco Central do Brasil.

Documentación: https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/documentacao
Swagger:       https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/swagger-ui2
Recurso:       CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)
               ?@dataInicial='MM-DD-AAAA'&@dataFinalCotacao='MM-DD-AAAA'&$format=json
Auth:          no requerida. Licencia ODbL (citar al BCB como fuente).
Respuesta:     {"@odata.context": "...", "value": [
                 {"cotacaoCompra": 4.89100, "cotacaoVenda": 4.89160,
                  "dataHoraCotacao": "2024-01-02 13:05:50.319"}, ...]}
               Un registro por día hábil: la PTAX de cierre.

Metodología (Resolución BCB nº 45/2020, que reemplazó la Circular 3.506/2010):
cuatro consultas diarias a los dealers acreditados; la PTAX es el promedio
simple de las cuatro y se divulga hacia las 13:00 de Brasilia. Solo hay PTAX en
días hábiles con mercado cambiario. Refleja el mercado del mismo día
(date_basis = determinacion), igual que el FIX y la A 3500.

Opción `field`: "cotacaoVenda" (por defecto; referencia usual en contratos) o
"cotacaoCompra".
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .base import ContractError, FetchRequest, Provider

FIELDS = {"cotacaoVenda", "cotacaoCompra"}


def today_brasilia() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Sao_Paulo")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


class BcbPtaxProvider(Provider):
    kind = "bcb_ptax"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        end = min(end, today_brasilia())
        out: dict[str, dict[str, float]] = {r.series_key: {} for r in requests}
        if start > end:
            return out
        for a, b in self.chunks(start, end):
            url = (f"{self.cfg['base_url']}/CotacaoDolarPeriodo(dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
                   f"?@dataInicial=%27{a:%m-%d-%Y}%27&@dataFinalCotacao=%27{b:%m-%d-%Y}%27&$format=json")
            records = parse_payload(self.http_get(url, {"Accept": "application/json"}))
            for r in requests:
                field = r.opt("field", "cotacaoVenda")
                if field not in FIELDS:
                    raise ContractError(f"BCB: campo desconocido {field!r}")
                out[r.series_key].update({d: rec[field] for d, rec in records.items()})
        return out


def parse_payload(payload: object) -> dict[str, dict]:
    """{fecha ISO: {"cotacaoCompra", "cotacaoVenda", "ts"}} con la última cotización de cada día."""
    if not isinstance(payload, dict) or not isinstance(payload.get("value"), list):
        raise ContractError("BCB: falta la lista 'value' en la respuesta OData")
    out: dict[str, dict] = {}
    for rec in payload["value"]:
        if not isinstance(rec, dict):
            raise ContractError("BCB: registro no es un objeto")
        missing = {"cotacaoCompra", "cotacaoVenda", "dataHoraCotacao"} - rec.keys()
        if missing:
            raise ContractError(f"BCB: faltan campos {sorted(missing)}")
        ts = str(rec["dataHoraCotacao"])
        try:
            day = datetime.strptime(ts[:10], "%Y-%m-%d").date().isoformat()
        except ValueError as exc:
            raise ContractError(f"BCB: dataHoraCotacao inesperada {ts!r}") from exc
        try:
            buy, sell = float(rec["cotacaoCompra"]), float(rec["cotacaoVenda"])
        except (TypeError, ValueError) as exc:
            raise ContractError("BCB: cotización no numérica") from exc
        if sell < buy:
            raise ContractError(f"BCB: venta < compra el {day} ({sell} < {buy}); ¿campos invertidos?")
        if day not in out or ts > out[day]["ts"]:  # si hay más de un registro, el más reciente
            out[day] = {"cotacaoCompra": buy, "cotacaoVenda": sell, "ts": ts}
    return out
