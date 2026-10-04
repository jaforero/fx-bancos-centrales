"""Orquestación de una corrida diaria.

Principio de diseño: una fuente caída nunca borra datos buenos. Si un
proveedor falla, sus series conservan el histórico previo, quedan marcadas
con status != "ok" y el comando `check` hace fallar el workflow para alertar.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import store, transform
from .providers import ConfigError, ContractError, FetchRequest, ProviderError, build_provider

log = logging.getLogger("fxpipe")


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    for sid, s in cfg["series"].items():
        if s["provider"] not in cfg["providers"]:
            raise ConfigError(f"Serie {sid}: proveedor '{s['provider']}' no declarado")
    for did, dv in cfg.get("derived", {}).items():
        for ref in (dv["numerator"], dv["denominator"]):
            if ref not in cfg["series"]:
                raise ConfigError(f"Derivada {did}: referencia '{ref}' no existe en series")
        bases = {cfg["series"][r]["date_basis"] for r in (dv["numerator"], dv["denominator"])} | {dv["date_basis"]}
        if len(bases) != 1:
            raise ConfigError(f"Derivada {did}: insumos con date_basis distinto {sorted(bases)}; desalinea días de mercado")
        if did in cfg["series"]:
            raise ConfigError(f"Id duplicado entre series y derivadas: {did}")
    return cfg


def today_in(tz_name: str) -> date:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo(tz_name)).date()
    except Exception:  # sin tzdata: UTC-5 aproxima Bogotá
        return (datetime.now(timezone.utc) - timedelta(hours=5)).date()


def _source_meta(cfg: dict, s: dict) -> dict:
    p = cfg["providers"][s["provider"]]
    return {"provider": s["provider"], "institution": p["institution"], "country": p["country"],
            "series_id": s["source_series_id"], "docs_url": p["docs_url"]}


def run(cfg: dict, data_dir: Path, as_of: date | None = None, *,
        backfill_start: date | None = None, http_get=None, dry_run: bool = False) -> dict:
    as_of = as_of or today_in(cfg.get("reference_timezone", "America/Bogota"))
    lookback = int(cfg.get("incremental_lookback_days", 15))
    default_start = date.fromisoformat(cfg["backfill_start"])
    fetch_end = as_of + timedelta(days=7)  # la TRM se publica con vigencia futura

    status: dict[str, str] = {}
    warnings: dict[str, list[str]] = defaultdict(list)
    errors: dict[str, str] = {}
    official: dict[str, dict[str, float]] = {}

    # 1) Cargar histórico y decidir ventana por proveedor
    by_provider: dict[str, list[str]] = defaultdict(list)
    for sid, s in cfg["series"].items():
        official[sid] = store.load_official(data_dir, sid)
        by_provider[s["provider"]].append(sid)

    # 2) Descargar por proveedor (una llamada agrupa sus series)
    for pname, sids in by_provider.items():
        starts = []
        for sid in sids:
            if backfill_start:
                starts.append(backfill_start)
            elif official[sid]:
                starts.append(date.fromisoformat(max(official[sid])) - timedelta(days=lookback))
            else:
                starts.append(default_start)
        start = min(starts)
        try:
            provider = build_provider(pname, cfg["providers"][pname], http_get=http_get)
            reqs = [FetchRequest(sid, cfg["series"][sid]["source_series_id"],
                                 tuple(sorted(cfg["series"][sid].get("source_options", {}).items())))
                    for sid in sids]
            fetched = provider.fetch(reqs, start, fetch_end)
            log.info("%s: %s", pname, {k: len(v) for k, v in fetched.items()})
        except (ProviderError, ConfigError) as exc:
            kind = "contract" if isinstance(exc, ContractError) else "provider"
            for sid in sids:
                status[sid] = f"error:{kind}"
                errors[sid] = str(exc)
            log.error("%s falló (%s): %s", pname, kind, exc)
            continue

        for sid in sids:
            s = cfg["series"][sid]
            new = fetched.get(sid, {})
            bad = transform.out_of_range(new, *s["plausible_range"])
            if bad:
                status[sid] = "error:validation"
                errors[sid] = f"valores fuera de rango plausible {s['plausible_range']}: {bad[:5]}"
                continue
            old_in_window = {k: v for k, v in official[sid].items() if date.fromisoformat(k) >= start}
            if old_in_window and not new:
                warnings[sid].append("la fuente devolvió 0 registros en una ventana que antes tenía datos")
            for k, v in new.items():
                if k in official[sid] and abs(official[sid][k] - v) > 1e-9:
                    warnings[sid].append(f"revisión {k}: {official[sid][k]} -> {v}")
            official[sid].update(new)
            status.setdefault(sid, "ok")

    # 3) Series diarias con relleno controlado
    daily: dict[str, transform.Daily] = {}
    meta: dict[str, dict] = {}
    for sid, s in cfg["series"].items():
        daily[sid] = transform.fill_forward(official[sid], as_of, int(s["max_fill_days"]))
        meta[sid] = {k: s[k] for k in ("pair", "label", "quote_unit", "date_basis", "primary", "decimals")}
        meta[sid]["source"] = _source_meta(cfg, s)
        meta[sid]["method"] = "official"

    # 4) Derivadas (tasas cruzadas)
    for did, dv in cfg.get("derived", {}).items():
        num, den = dv["numerator"], dv["denominator"]
        daily[did] = transform.cross_rate(daily[num], daily[den], int(dv["decimals"]))
        meta[did] = {k: dv[k] for k in ("pair", "label", "quote_unit", "date_basis", "primary", "decimals")}
        meta[did]["method"] = f"cross_via_usd: {num} / {den}"
        meta[did]["inputs"] = [num, den]
        bad = transform.out_of_range({k: v["value"] for k, v in daily[did].items()}, *dv["plausible_range"])
        if bad:
            warnings[did].append(f"valores fuera de rango plausible: {bad[:5]}")
        broken = [(ref, status[ref]) for ref in (num, den) if status.get(ref, "ok") != "ok"]
        if broken:
            status[did] = broken[0][1]
            errors[did] = "insumo degradado: " + ", ".join(f"{r} ({st})" for r, st in broken)
        else:
            status[did] = "ok"

    # 5) Validaciones de continuidad y frescura
    for sid, series in daily.items():
        spec = cfg["series"].get(sid) or cfg["derived"][sid]
        warnings[sid].extend(transform.jump_warnings(series, float(spec["max_daily_change_pct"])))
        lo = transform.last_official(series)
        stale_after = int(spec["stale_after_days"])
        if lo is None and status.get(sid, "ok") == "ok":
            status[sid] = "error:empty"
        elif lo is not None and (as_of - date.fromisoformat(lo)).days > stale_after and status.get(sid, "ok") == "ok":
            status[sid] = "stale"
        status.setdefault(sid, "ok")

    # 6) Construir salidas
    texts = build_outputs(cfg, daily, meta, status, warnings, errors, as_of)
    data_hash = store.content_hash(texts)
    manifest_path = data_dir / "manifest.json"
    prev = store.load_json(manifest_path) or {}
    changed = prev.get("data_hash") != data_hash

    overall = "ok" if all(v == "ok" for v in status.values()) else "degraded"
    manifest = {
        "schema_version": store.SCHEMA_VERSION,
        "config_version": cfg.get("config_version"),
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "as_of": as_of.isoformat(),
        "overall_status": overall,
        "data_hash": data_hash,
        "series_status": dict(sorted(status.items())),
        "errors": dict(sorted(errors.items())),
        "warnings": {k: v[-20:] for k, v in sorted(warnings.items()) if v},
        "files": sorted(texts),
    }
    if not dry_run and (changed or prev.get("overall_status") != overall or prev.get("errors") != manifest["errors"]):
        for rel, text in texts.items():
            store.atomic_write(data_dir / rel, text)
        store.atomic_write(manifest_path, store.render(manifest))
    return {"changed": changed, "overall_status": overall, "manifest": manifest}


def build_outputs(cfg, daily, meta, status, warnings, errors, as_of) -> dict[str, str]:
    texts: dict[str, str] = {}
    order = list(cfg["series"]) + list(cfg.get("derived", {}))

    # data/series/<id>.json — histórico completo por serie
    for sid in order:
        obs = [{"date": k, "value": v["value"], "filled": v["filled"]} for k, v in sorted(daily[sid].items())]
        doc = {
            "schema_version": store.SCHEMA_VERSION,
            "id": sid,
            **meta[sid],
            "first_date": obs[0]["date"] if obs else None,
            "last_date": obs[-1]["date"] if obs else None,
            "last_official_date": transform.last_official(daily[sid]),
            "observations": obs,
        }
        texts[f"series/{sid}.json"] = store.render(doc, rows_key="observations")

    # data/rates_daily.json — formato ancho: todas las tasas, una fila por día
    all_dates = sorted(set().union(*[daily[s].keys() for s in order])) if order else []
    rows = []
    for k in all_dates:
        row: dict = {"date": k}
        filled = []
        for sid in order:
            v = daily[sid].get(k)
            row[sid] = v["value"] if v else None
            if v and v["filled"]:
                filled.append(sid)
        if filled:
            row["filled"] = filled
        rows.append(row)
    wide = {
        "schema_version": store.SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "series": {sid: meta[sid] for sid in order},
        "rows": rows,
    }
    texts["rates_daily.json"] = store.render(wide, rows_key="rows")

    # data/latest.json — tarjetas KPI del dashboard
    latest = {
        "schema_version": store.SCHEMA_VERSION,
        "as_of": as_of.isoformat(),
        "overall_status": "ok" if all(status.get(s, "ok") == "ok" for s in order) else "degraded",
        "series": {},
    }
    for sid in order:
        entry = {**meta[sid], **transform.snapshot(daily[sid], as_of), "status": status.get(sid, "ok")}
        if sid in errors:
            entry["error"] = errors[sid]
        latest["series"][sid] = entry
    texts["latest.json"] = store.render(latest)
    return texts
