"""Conector para la API de series estadísticas del Banco Central de Reserva del Perú (BCRPData).

Documentación: https://estadisticas.bcrp.gob.pe/estadisticas/series/ayuda/api
Condiciones:   https://estadisticas.bcrp.gob.pe/estadisticas/series/ayuda/condiciones-de-uso
               (la información puede reproducirse sin autorización si se cita la fuente)
Endpoint:      GET {base}/{código}/json/{AAAA-MM-DD}/{AAAA-MM-DD}/{esp|ing}
Auth:          no requerida.
Respuesta:     {"config": {"title": "...", "series": [{"name": "...", "dec": "3"}]},
                "periods": [{"name": "05.Oct.26", "values": ["3.42714285714286"]},
                            {"name": "08.Oct.26", "values": ["n.d."]}]}
               Fechas «DD.Mmm.AA» con el mes abreviado en español (Ene, Feb, Mar, Abr, May, Jun, Jul,
               Ago, Set, Oct, Nov, Dic). Verificado el 2026-10-16: con `ing` el BCRP escribe igual
               «Set» para septiembre, así que el conector pide `esp` y acepta ambas grafías.
               Los días sin dato (feriados peruanos, hoy y el futuro) llegan como «n.d.».
               Respuesta real en tests/fixtures/bcrp_interbancario_venta_real.json.

Serie usada: PD04638PD «Tipo de cambio - TC Interbancario (S/ por US$) - Venta» (soles por 1 USD).
El código de Compra es PD04637PD; el de Venta es el 04638 (comprobado con el nombre que devuelve
la propia API: el conector exige ese nombre en `source_options.expected_name`, de modo que un
código equivocado pero válido no produce datos falsos). El BCRP declara 3 decimales (`dec`);
la API entrega el valor del día con hasta 14 (en la mayoría de los días son múltiplos de 1/7,
lo que sugiere un promedio de siete cotizaciones; el BCRP no documenta el cálculo). Se guarda
tal como llega, sin redondear. La página del BCRP cita como fuentes «BCRP, Datatec, Reuters, SBS».

Fecha = día de mercado de las operaciones (date_basis = determinacion), como FIX, PTAX y A 3500.
Prueba empírica (1.693 días, 2020-2026): la variación diaria correlaciona 0,43 con el observado de
Chile por día de mercado, 0,38 con el FIX, 0,36 con la TRM por día de mercado, 0,35 con la PTAX y
0,26 con el interbancario de Uruguay, el mismo día; con un día de desfase en cualquier sentido
la correlación cae a entre -0,03 y 0,07.

Firewall: estadisticas.bcrp.gob.pe está detrás de Imperva Incapsula. Una consulta mal formada
(código inexistente, rango invertido, sin idioma) y, de forma intermitente, una consulta válida
devuelven HTTP 200 con una página HTML de verificación en lugar de JSON (capturas reales en
tests/fixtures/bcrp_desafio_incapsula_real.html). Es una falla operativa, no un cambio de
contrato: se reintenta con espera creciente y, si persiste, se informa como ProviderError.
Si ocurre solo desde GitHub Actions, el firewall está bloqueando al ejecutor.
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta, timezone

from .base import ConfigError, ContractError, FetchRequest, Provider, ProviderError

MONTHS = {"ene": 1, "jan": 1, "feb": 2, "mar": 3, "abr": 4, "apr": 4, "may": 5, "jun": 6, "jul": 7,
          "ago": 8, "aug": 8, "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12, "dec": 12}
PERIOD = re.compile(r"^(\d{2})\.([A-Za-z]{3})\.(\d{2})$")
SERIES_CODE = re.compile(r"^[A-Z]{2}\d{5}[A-Z]{2}$")  # p. ej. PD04638PD
NO_DATA = "n.d."
ATTEMPTS = 4


def today_lima() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Lima")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


class BcrpSeriesProvider(Provider):
    kind = "bcrp_series"

    def __init__(self, name: str, cfg: dict, http_get=None):
        super().__init__(name, cfg, http_get=http_get)
        self.sleep = time.sleep  # sustituible en pruebas

    def _request(self, url: str) -> str:
        """Texto de la respuesta; reintenta si el firewall devuelve su página de verificación."""
        last = ""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                # Sin "json" en Accept, el transporte entrega el texto y aquí se decide qué es.
                raw = self.http_get(url, {"Accept": "text/plain, */*;q=0.8"})
            except ContractError:
                raise
            except ProviderError as exc:
                if "HTTP 403" not in str(exc):
                    raise
                last = "HTTP 403 del firewall"
            else:
                text = raw if isinstance(raw, str) else json.dumps(raw)
                if text.lstrip().startswith("{"):
                    return text
                last = ("página de verificación del firewall Imperva Incapsula"
                        if "Incapsula" in text else f"respuesta no JSON ({text[:80]!r})")
            if attempt < ATTEMPTS:
                self.sleep(min(2 ** attempt * 2, 30))
        raise ProviderError(
            f"BCRP: {last} en lugar de JSON tras {ATTEMPTS} intentos en {url}. Si ocurre solo desde "
            "GitHub Actions, el firewall del BCRP está bloqueando al ejecutor; si ocurre siempre, "
            "revisa el código de serie y el rango de fechas de la consulta.")

    def _get(self, code: str, a: date, b: date, expected_name: str | None) -> dict[str, float]:
        url = f"{self.cfg['base_url']}/{code}/json/{a.isoformat()}/{b.isoformat()}/esp"
        return parse_payload(self._request(url), expected_name, url)

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        end = min(end, today_lima())  # el futuro solo trae «n.d.»: no se consulta
        out: dict[str, dict[str, float]] = {}
        cache: dict[str, dict[str, float]] = {}
        for r in requests:
            code = r.source_series_id
            if not SERIES_CODE.match(code):
                raise ConfigError(f"BCRP: código de serie con forma inesperada {code!r} (se espera, p. ej., PD04638PD)")
            if code not in cache:
                obs: dict[str, float] = {}
                if start <= end:
                    for a, b in self.chunks(start, end):
                        obs.update(self._get(code, a, b, r.opt("expected_name")))
                cache[code] = obs
            out[r.series_key] = {k: v for k, v in cache[code].items() if start <= date.fromisoformat(k) <= end}
        return out


def parse_payload(text: str, expected_name: str | None = None, where: str = "") -> dict[str, float]:
    """{fecha ISO: valor} con los días que tienen dato; «n.d.» se omite."""
    try:
        doc = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(f"BCRP: la respuesta no es JSON válido {where}: {text[:120]!r}".strip()) from exc
    if not isinstance(doc, dict) or not isinstance(doc.get("config"), dict) or not isinstance(doc.get("periods"), list):
        raise ContractError("BCRP: falta 'config' o 'periods' en la respuesta")
    series = doc["config"].get("series")
    if not isinstance(series, list) or len(series) != 1 or not isinstance(series[0], dict):
        raise ContractError("BCRP: se esperaba exactamente 1 serie en 'config.series'")
    name = str(series[0].get("name", ""))
    if expected_name and expected_name not in name:
        raise ContractError(f"BCRP: se esperaba la serie «{expected_name}» y llegó «{name}»")
    out: dict[str, float] = {}
    for p in doc["periods"]:
        if not isinstance(p, dict) or not isinstance(p.get("name"), str) or not isinstance(p.get("values"), list) \
                or len(p["values"]) != 1:
            raise ContractError("BCRP: periodo sin 'name' o sin un único valor")
        m = PERIOD.match(p["name"])
        if not m or m.group(2).lower() not in MONTHS:
            raise ContractError(f"BCRP: fecha inesperada {p['name']!r} (se esperaba DD.Mmm.AA, p. ej. 05.Oct.26)")
        try:
            day = date(2000 + int(m.group(3)), MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError as exc:
            raise ContractError(f"BCRP: fecha imposible {p['name']!r}") from exc
        raw = p["values"][0]
        if raw == NO_DATA:
            continue
        try:
            value = float(str(raw))
        except ValueError as exc:
            raise ContractError(f"BCRP: valor no numérico {raw!r} el {day}") from exc
        if not value > 0:  # también atrapa NaN
            raise ContractError(f"BCRP: valor no positivo {raw!r} el {day}")
        if day.weekday() >= 5:
            raise ContractError(f"BCRP: valor en fin de semana ({day}); revisar la base de fecha de la serie")
        if day.isoformat() in out:
            raise ContractError(f"BCRP: fecha repetida {day}")
        out[day.isoformat()] = value
    return out
