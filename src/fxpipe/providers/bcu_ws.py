"""Conector para el web service de cotizaciones del Banco Central del Uruguay (BCU).

Especificación: "Web Service de Cotizaciones · Web Service de Monedas · Web Service de
               Último Cierre", versión 3 (28-sep-2020, actualizada 12-12-2022), publicada por el BCU.
WSDL:          https://cotizaciones.bcu.gub.uy/wscotizaciones/servlet/awsbcucotizaciones?wsdl
Transporte:    SOAP 1.1 document/literal por POST; SOAPAction "Cotizaaction/AWSBCUCOTIZACIONES.Execute".
Auth:          no requerida.
Entrada:       wsbcucotizacionesin { Moneda[item], FechaDesde, FechaHasta (AAAA-MM-DD), Grupo }.
               Grupo 2 = Cotizaciones locales. La especificación limita el rango a 31 días.
Salida:        respuestastatus { status (1 = OK, 0 = con errores), codigoerror, mensaje } y
               datoscotizaciones.dato { Fecha, Moneda, Nombre, CodigoISO, Emisor, TCC, TCV, ArbAct,
               FormaArbitrar }. Respuestas reales en tests/fixtures/bcu_*_real.xml (2026-10-15).
Códigos:       100 = no hay cotización para esas fechas (feriado, fin de semana o futuro):
               se trata como ventana vacía. 101-105 = consulta inválida (cambio de contrato).
               106-107 = servicio no disponible (transitorio).

Serie usada: 2225 «DLS. USA BILLETE», la cotización interbancaria del dólar (pesos uruguayos
por 1 USD, tres decimales). Es el promedio de las operaciones interbancarias del día en BEVSA
(verificado: 6-sep-2021 = 42,572 en el BCU y «promedio del interbancario 42,57» según Bevsa).
Desde 2020, «DLS. USA CABLE» (2224) y «DLS.PROMED.FONDO» (2230) publican el mismo valor
(verificado en 1.688 días; solo difieren 15 días de enero de 2020). TCC = TCV en todo el período.

Fecha = día de mercado de las operaciones (date_basis = determinacion), como FIX, PTAX y A 3500:
el cierre del día D se publica el mismo día D (página «Cierre del mercado de cambios» del BCU).
Prueba empírica (1.571-1.661 días): la variación diaria correlaciona 0,47 con la PTAX, 0,41 con
el FIX y 0,33 con la TRM y el observado de Chile por día de mercado, el mismo día; con un día
de desfase en cualquier sentido la correlación cae a ~0,02.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone

from .base import ContractError, FetchRequest, Provider, ProviderError

NS = {"soap": "http://schemas.xmlsoap.org/soap/envelope/", "c": "Cotiza"}
SOAP_ACTION = "Cotizaaction/AWSBCUCOTIZACIONES.Execute"
EMPTY = 100                      # "No existe cotización para la fecha indicada"
TRANSIENT = {106, 107}           # actualización de datos / servicio no disponible


def today_montevideo() -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/Montevideo")).date()
    except Exception:
        return (datetime.now(timezone.utc) - timedelta(hours=3)).date()


def soap_body(codes: list[int], start: date, end: date, group: int) -> bytes:
    items = "".join(f"<cot:item>{int(c)}</cot:item>" for c in codes)
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:cot="Cotiza">'
            '<soapenv:Header/><soapenv:Body><cot:wsbcucotizaciones.Execute><cot:Entrada>'
            f'<cot:Moneda>{items}</cot:Moneda><cot:FechaDesde>{start.isoformat()}</cot:FechaDesde>'
            f'<cot:FechaHasta>{end.isoformat()}</cot:FechaHasta><cot:Grupo>{int(group)}</cot:Grupo>'
            '</cot:Entrada></cot:wsbcucotizaciones.Execute></soapenv:Body></soapenv:Envelope>').encode("utf-8")


class BcuWsProvider(Provider):
    kind = "bcu_ws"

    def fetch(self, requests: list[FetchRequest], start: date, end: date) -> dict[str, dict[str, float]]:
        end = min(end, today_montevideo())   # no hay cotizaciones futuras
        out: dict[str, dict[str, float]] = {r.series_key: {} for r in requests}
        if start > end:
            return out
        group = int(self.cfg.get("group", 2))
        by_code: dict[int, list[FetchRequest]] = {}
        for r in requests:
            try:
                by_code.setdefault(int(r.source_series_id), []).append(r)
            except ValueError as exc:
                raise ContractError(f"BCU: código de moneda inválido {r.source_series_id!r}") from exc
        codes = sorted(by_code)
        headers = {"Accept": "text/xml", "Content-Type": "text/xml; charset=utf-8", "SOAPAction": SOAP_ACTION}
        for a, b in self.chunks(start, end):
            payload = self.http_get(self.cfg["base_url"], headers, data=soap_body(codes, a, b, group))
            rows = parse_payload(payload, set(codes))
            for code, reqs in by_code.items():
                for r in reqs:
                    field = r.opt("field", "TCV")
                    if field not in ("TCV", "TCC"):
                        raise ContractError(f"BCU: campo desconocido {field!r}")
                    out[r.series_key].update({d: v[field] for d, v in rows.get(code, {}).items()})
        return out


def parse_payload(payload: object, codes: set[int]) -> dict[int, dict[str, dict]]:
    """{código: {fecha ISO: {"TCC", "TCV"}}} con las cotizaciones de los códigos pedidos."""
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8")
    if not isinstance(payload, str):
        raise ContractError("BCU: se esperaba XML (texto)")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ContractError(f"BCU: XML inválido ({exc})") from exc
    salida = root.find(".//c:Salida", NS)
    status = root.find(".//c:respuestastatus", NS)
    if salida is None or status is None:
        fault = root.find(".//faultstring")
        raise ContractError("BCU: respuesta sin Salida/respuestastatus"
                            + (f" (SOAP Fault: {fault.text})" if fault is not None else ""))
    try:
        st = int(status.findtext("c:status", default="", namespaces=NS))
        code = int(status.findtext("c:codigoerror", default="", namespaces=NS))
    except ValueError as exc:
        raise ContractError("BCU: status o codigoerror no numéricos") from exc
    msg = (status.findtext("c:mensaje", default="", namespaces=NS) or "").strip()
    if code == EMPTY:
        return {}
    if code in TRANSIENT:
        raise ProviderError(f"BCU: servicio no disponible (codigoerror {code}: {msg})")
    if code != 0 or st != 1:
        raise ContractError(f"BCU: la consulta fue rechazada (status {st}, codigoerror {code}: {msg})")
    out: dict[int, dict[str, dict]] = {}
    for d in root.findall(".//c:datoscotizaciones.dato", NS):
        try:
            m = int(d.findtext("c:Moneda", default="", namespaces=NS))
            fecha = (d.findtext("c:Fecha", default="", namespaces=NS) or "").strip()
            iso = datetime.strptime(fecha, "%Y-%m-%d").date().isoformat()
            tcc = float(d.findtext("c:TCC", default="", namespaces=NS))
            tcv = float(d.findtext("c:TCV", default="", namespaces=NS))
            forma = int(d.findtext("c:FormaArbitrar", default="0", namespaces=NS))
        except (TypeError, ValueError) as exc:
            raise ContractError("BCU: dato con Fecha, Moneda, TCC, TCV o FormaArbitrar inválidos") from exc
        if m not in codes:
            raise ContractError(f"BCU: llegó la moneda {m} sin haberla pedido ({sorted(codes)})")
        if forma != 0:
            raise ContractError(f"BCU: FormaArbitrar {forma} en {m}: se esperaba pesos uruguayos por moneda (0)")
        if tcv <= 0 or tcc <= 0:
            raise ContractError(f"BCU: cotización no positiva el {iso} ({tcc}/{tcv})")
        if abs(tcc - tcv) > 1e-9:
            # Desde 2020 el interbancario publica un único promedio (TCC = TCV). Si eso cambia,
            # se revisa la metodología antes de elegir compra, venta o promedio.
            raise ContractError(f"BCU: compra y venta distintas el {iso} ({tcc} vs {tcv}); revisar metodología")
        out.setdefault(m, {})[iso] = {"TCC": tcc, "TCV": tcv}
    return out
