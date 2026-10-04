"""Contrato común para todos los conectores de bancos centrales.

Cada proveedor convierte la respuesta de su API en observaciones diarias
normalizadas: {fecha ISO -> valor float}. Todo lo específico de la API
(autenticación, formato de fechas, paginación) vive dentro del conector.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from typing import Callable

USER_AGENT = "fx-bancos-centrales/1.0 (+https://github.com/jaforero)"


class ProviderError(RuntimeError):
    """Fallo operativo: red, HTTP, credenciales. Suele ser transitorio."""


class ContractError(ProviderError):
    """La respuesta ya no tiene la forma esperada: posible cambio de API."""


class ConfigError(RuntimeError):
    """Configuración inválida o secreto faltante."""


@dataclass(frozen=True)
class FetchRequest:
    series_key: str          # id interno, p. ej. "usd_mxn_fix"
    source_series_id: str    # id en la fuente, p. ej. "SF43718"
    options: tuple = ()      # pares (clave, valor) de config "source_options"

    def opt(self, key: str, default=None):
        return dict(self.options).get(key, default)


# Tipo del transporte HTTP: permite inyectar respuestas falsas en pruebas.
HttpGet = Callable[[str, dict], object]


def default_http_get(url: str, headers: dict, *, retries: int = 4,
                     timeout: int = 45) -> object:
    """GET con reintentos exponenciales para 429/5xx y errores de red."""
    last_exc: Exception | None = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
            try:
                return json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ContractError(f"Respuesta no es JSON válido desde {redact(url)}: {raw[:200]!r}") from exc
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ProviderError(f"HTTP {exc.code} (credenciales o permisos) en {redact(url)}") from exc
            if exc.code == 404:
                raise ContractError(f"HTTP 404: el endpoint pudo cambiar: {redact(url)}") from exc
            last_exc = exc
            if exc.code not in (429, 500, 502, 503, 504):
                try:
                    detail = exc.read().decode("utf-8", "replace")[:300]
                except Exception:
                    detail = ""
                raise ProviderError(f"HTTP {exc.code} en {redact(url)}: {detail}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_exc = exc
        time.sleep(min(2 ** attempt * 2, 30))
    raise ProviderError(f"Fallo tras {retries} intentos en {redact(url)}: {last_exc}")


def redact(url: str) -> str:
    """Oculta cualquier token que viaje en la URL antes de registrarla."""
    parts = urllib.parse.urlsplit(url)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    safe = [(k, "***" if "token" in k.lower() else v) for k, v in query]
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(safe)))


def read_token(cfg: dict) -> str | None:
    env = cfg.get("token_env")
    token = os.environ.get(env, "").strip() if env else ""
    if cfg.get("token_required") and not token:
        raise ConfigError(f"Falta el secreto {env}. Configúralo en GitHub > Settings > Secrets and variables > Actions.")
    return token or None


class Provider:
    """Clase base. Las subclases implementan `fetch`."""

    kind: str = "base"

    def __init__(self, name: str, cfg: dict, http_get: HttpGet | None = None):
        self.name = name
        self.cfg = cfg
        self.http_get = http_get or default_http_get

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        """Devuelve {series_key: {"YYYY-MM-DD": valor}} con datos oficiales.

        Solo fechas con valor publicado o vigente según la fuente; el relleno
        de días sin dato lo hace el pipeline, nunca el conector.
        """
        raise NotImplementedError

    def chunks(self, start: date, end: date):
        """Parte rangos largos para respetar límites de la API."""
        from datetime import timedelta
        step = int(self.cfg.get("max_days_per_request", 366))
        cur = start
        while cur <= end:
            stop = min(cur + timedelta(days=step - 1), end)
            yield cur, stop
            cur = stop + timedelta(days=1)
