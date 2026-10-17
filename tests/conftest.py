"""Guardas que valen para todas las pruebas.

Ninguna prueba debe esperar ni salir a la red de verdad. La corrida #12 de CI tardó
10 min 27 s porque una prueba sin ruta simulada para una fuente nueva hizo los
reintentos de verdad (esperas reales de hasta 30 s). Con esta guarda, ese mismo error
falla en segundos y dice por qué, aunque el pipeline se trague la excepción.

Una prueba que necesite simular las esperas (los reintentos de un conector) lo hace
con `monkeypatch.setattr(<módulo>.time, "sleep", lambda s: None)`, como ya hacen
las pruebas del BCRP y de `base.http_get`; el parche de la prueba prevalece sobre
esta guarda.
"""
import socket
import time

import pytest


@pytest.fixture(autouse=True)
def sin_esperas_ni_red_reales(monkeypatch):
    violaciones: list[str] = []

    def sleep_prohibido(seconds):
        if seconds and seconds > 0:
            violaciones.append(f"time.sleep({seconds}): espera real en una prueba")
            raise AssertionError(violaciones[-1] + "; simula `time.sleep` en esa prueba")

    def red_prohibida(*args, **kwargs):
        violaciones.append(f"conexión de red real en una prueba: {args[-1] if args else ''}")
        raise AssertionError(violaciones[-1] + "; usa un `http_get` simulado")

    monkeypatch.setattr(time, "sleep", sleep_prohibido)
    monkeypatch.setattr(socket.socket, "connect", red_prohibida)
    monkeypatch.setattr(socket.socket, "connect_ex", red_prohibida)
    monkeypatch.setattr(socket, "getaddrinfo", red_prohibida)
    yield violaciones  # la prueba de la propia guarda la pide por nombre y la vacía
    assert not violaciones, "La prueba esperó o usó la red de verdad: " + "; ".join(violaciones)
