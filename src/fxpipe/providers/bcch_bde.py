"""Conector para la API REST de la Base de Datos Estadísticos (BDE) del Banco Central de Chile.

Documentación: https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/documentacion.html
Acceso:        https://si3.bcentral.cl/estadisticas/Principal1/Web_Services/acceso_api.html
Endpoint:      https://si3.bcentral.cl/SieteRestWS/SieteRestWS.ashx
               ?token=…&function=GetSeries&timeseries=F073.TCO.PRE.Z.D
               &firstdate=AAAA-MM-DD&lastdate=AAAA-MM-DD
Auth:          API Key Token de la cuenta BDE (parámetro `token`; vigencia de un año,
               se renueva en "Mi Cuenta" > "Apikey Token"). Va en el secreto BCCH_TOKEN.
Respuesta:     {"Codigo": 0, "Descripcion": "Success",
                "Series": {"seriesId": "...", "descripEsp": "...", "Obs": [
                   {"indexDateString": "DD-MM-AAAA", "value": "978.61", "statusCode": "OK"},
                   {"indexDateString": "DD-MM-AAAA", "value": "NaN", "statusCode": "ND"}]},
                "SeriesInfos": [...]}
               Codigo distinto de 0 = error declarado por la API.

Serie (verificada en el cuaderno oficial de ejemplos de la API y en la tabla de la BDE):
F073.TCO.PRE.Z.D, "Tipo de cambio nominal (dólar observado $CLP/USD)", diaria, dos decimales.

Metodología (BCCh, "Dólar observado"; Compendio de Normas de Cambios Internacionales,
Capítulo I, sección VII): promedio ponderado de las operaciones spot USD/CLP del Mercado
Cambiario Formal del día hábil bancario inmediatamente anterior. Se difunde desde las
17:30 de ese día y se publica en el Diario Oficial el día hábil bancario de su vigencia.

Fecha de cada observación = fecha de VIGENCIA (date_basis = vigencia), igual que la TRM:
- La BDE y la tabla del SII asignan el valor al día en que rige (verificado: 22-sep-2025
  = 951,03 y 21-sep-2026 = 958,42 en ambas; el 13-oct-2026 ya figuraba el viernes 9-oct).
- `index: "market_date"`: el día de mercado de una observación es la fecha de la
  observación oficial anterior (el día hábil bancario inmediatamente anterior). Así se
  usa el calendario bancario chileno de la propia serie, sin tabla de feriados.
  La primera observación descargada no genera día de mercado (no tiene antecesora);
  como su fecha es >= start, ningún día de mercado de la ventana se pierde.

Ventana: la parte pasada (hasta hoy en Santiago) debe responder; la parte futura
(vigencias ya publicadas, p. ej. el lunes que se publica el viernes) se pide aparte y,
si la API la rechaza, se omite con una advertencia en el log: solo aporta `next` y el
último día de mercado, que llegan igual en la corrida siguiente.
"""
from __future__ import annotations

import logging
import urllib.parse
from datetime import date, datetime, timedelta, timezone

from .base import ContractError, FetchRequest, Provider, ProviderError, read_token, redact

log = logging.getLogger("fxpipe")

MAX_GAP_DAYS = 7  # margen sobre fines de semana largos con feriado bancario (p. ej., Fiestas Patrias)


def today_santiago() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Santiago")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


def scrub(text: str, token: str | None) -> str:
    """Quita el token (crudo o codificado) de cualquier texto que vaya al manifest o a un issue."""
    if not token:
        return text
    for form in {token, urllib.parse.quote(token, safe=""), urllib.parse.quote_plus(token)}:
        text = text.replace(form, "***")
    return text


class BcchBdeProvider(Provider):
    kind = "bcch_bde"

    def _get(self, token: str | None, sid: str, a: date, b: date) -> dict[str, float]:
        query = urllib.parse.urlencode({"token": token or "", "function": "GetSeries", "timeseries": sid,
                                        "firstdate": a.isoformat(), "lastdate": b.isoformat()})
        url = f"{self.cfg['base_url']}?{query}"
        try:
            return parse_payload(self.http_get(url, {"Accept": "application/json"}), sid, redact(url))
        except ProviderError as exc:
            raise type(exc)(scrub(str(exc), token)) from None

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        token = read_token(self.cfg)
        out: dict[str, dict[str, float]] = {}
        cache: dict[str, dict[str, float]] = {}
        today = today_santiago()
        past_end = min(end, today)
        for r in requests:
            sid = r.source_series_id
            if sid not in cache:
                obs: dict[str, float] = {}
                if start <= past_end:
                    for a, b in self.chunks(start, past_end):
                        obs.update(self._get(token, sid, a, b))
                if end > today:
                    try:
                        obs.update(self._get(token, sid, max(start, today + timedelta(days=1)), end))
                    except ProviderError as exc:
                        log.warning("BCCh: sin vigencias futuras para %s (%s); se completan en la próxima corrida", sid, exc)
                cache[sid] = obs
            daily = cache[sid]
            mode = r.opt("index", "vigencia")
            if mode == "vigencia":
                rows = daily
            elif mode == "market_date":
                rows = market_dates_from_observations(daily)
            else:
                raise ContractError(f"BCCh: opción index desconocida {mode!r}")
            out[r.series_key] = {k: v for k, v in rows.items() if start <= date.fromisoformat(k) <= end}
        return out


def parse_payload(payload: object, series_id: str, where: str = "") -> dict[str, float]:
    """{fecha ISO: valor} con las observaciones `statusCode == "OK"` de la serie pedida."""
    if not isinstance(payload, dict) or "Codigo" not in payload:
        raise ContractError(f"BCCh: respuesta sin 'Codigo' {where}".strip())
    code, desc = payload.get("Codigo"), str(payload.get("Descripcion", ""))
    if str(code).strip() != "0":  # la documentación muestra 0 numérico; se tolera "0"
        hint = ""
        if any(w in desc.lower() for w in ("token", "usuario", "user", "password", "contrase", "credencial", "invalid")):
            hint = (" Revisa el secreto BCCH_TOKEN: el token de la BDE vence al año y se renueva en "
                    "si3.bcentral.cl > Mi Cuenta > Apikey Token.")
        raise ProviderError(f"BCCh: la API respondió Codigo={code} ({desc[:160]}).{hint} {where}".strip())
    series = payload.get("Series")
    if isinstance(series, list):  # tolera una lista de un solo elemento
        if len(series) != 1:
            raise ContractError(f"BCCh: se esperaba 1 serie y llegaron {len(series)}")
        series = series[0]
    if not isinstance(series, dict) or not isinstance(series.get("Obs"), list):
        raise ContractError("BCCh: falta 'Series.Obs' en la respuesta")
    got_id = series.get("seriesId")
    if got_id and got_id != series_id:
        raise ContractError(f"BCCh: se pidió {series_id} y llegó {got_id}")
    out: dict[str, float] = {}
    for o in series["Obs"]:
        if not isinstance(o, dict) or not {"indexDateString", "value", "statusCode"} <= o.keys():
            raise ContractError("BCCh: observación sin indexDateString, value o statusCode")
        try:
            iso = datetime.strptime(str(o["indexDateString"]), "%d-%m-%Y").date().isoformat()
        except ValueError as exc:
            raise ContractError(f"BCCh: fecha inesperada {o['indexDateString']!r} (se esperaba DD-MM-AAAA)") from exc
        status = o["statusCode"]
        if status == "ND":
            continue  # "NaN": día sin valor (fin de semana o feriado bancario)
        if status != "OK":
            raise ContractError(f"BCCh: statusCode desconocido {status!r} el {iso}")
        try:
            out[iso] = float(str(o["value"]).replace(",", "."))
        except ValueError as exc:
            raise ContractError(f"BCCh: valor no numérico {o['value']!r} el {iso}") from exc
    return out


def market_dates_from_observations(daily: dict[str, float]) -> dict[str, float]:
    """Día de mercado = fecha de la observación oficial anterior.

    El dólar observado vigente el día D se calcula con el mercado del día hábil
    bancario inmediatamente anterior, que es justamente la observación previa de
    la serie. Si el salto entre dos observaciones supera MAX_GAP_DAYS no se
    asigna día de mercado (sería adivinar): el pipeline deja ese hueco visible.
    """
    keys = sorted(daily)
    out: dict[str, float] = {}
    for prev, cur in zip(keys, keys[1:]):
        p, c = date.fromisoformat(prev), date.fromisoformat(cur)
        if (c - p).days > MAX_GAP_DAYS:
            continue
        if p.weekday() >= 5:
            raise ContractError(f"BCCh: día de mercado {p} cae en fin de semana; revisar la regla de vigencia")
        out[prev] = daily[cur]
    return out
