"""Uso:
  python -m fxpipe update                 # corrida incremental diaria
  python -m fxpipe backfill --start 2020-01-01
  python -m fxpipe check                  # exit 1 si alguna serie no está "ok"
  python -m fxpipe healthcheck --report reporte.md   # prueba de contrato en vivo
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

from . import pipeline, store
from .providers import ConfigError, ContractError, FetchRequest, ProviderError, build_provider

ROOT = Path(__file__).resolve().parents[2]


def _common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--config", type=Path, default=ROOT / "config" / "sources.json")
    p.add_argument("--data-dir", type=Path, default=ROOT / "data")
    p.add_argument("--as-of", type=date.fromisoformat, default=None, help="Fecha de referencia AAAA-MM-DD")


def cmd_update(args, backfill: date | None = None) -> int:
    cfg = pipeline.load_config(args.config)
    res = pipeline.run(cfg, args.data_dir, args.as_of, backfill_start=backfill, dry_run=args.dry_run)
    m = res["manifest"]
    print(f"estado={m['overall_status']} cambios={'sí' if res['changed'] else 'no'} as_of={m['as_of']}")
    for sid, st in m["series_status"].items():
        print(f"  {sid:<16} {st}")
    for sid, err in m["errors"].items():
        print(f"  ERROR {sid}: {err}")
    return 0  # los fallos se señalan con `check`, después de confirmar lo que sí se actualizó


def cmd_check(args) -> int:
    m = store.load_json(args.data_dir / "manifest.json")
    if not m:
        print("No existe data/manifest.json: aún no hay corridas.")
        return 1
    bad = {k: v for k, v in m["series_status"].items() if v != "ok"}
    if bad:
        print("Series con problemas:")
        for k, v in bad.items():
            print(f"  {k}: {v} {m['errors'].get(k, '')}")
        return 1
    print("Todas las series en estado ok.")
    return 0


def cmd_healthcheck(args) -> int:
    """Consulta los últimos 20 días de cada fuente y verifica el contrato."""
    cfg = pipeline.load_config(args.config)
    end = args.as_of or pipeline.today_in(cfg.get("reference_timezone", "America/Bogota"))
    start = end - timedelta(days=20)
    lines = [f"# Healthcheck de fuentes ({end.isoformat()})", ""]
    ok = True
    by_provider: dict[str, list[str]] = {}
    for sid, s in cfg["series"].items():
        by_provider.setdefault(s["provider"], []).append(sid)
    for pname, sids in by_provider.items():
        pcfg = cfg["providers"][pname]
        try:
            prov = build_provider(pname, pcfg)
            got = prov.fetch([FetchRequest(s, cfg["series"][s]["source_series_id"],
                                           tuple(sorted(cfg["series"][s].get("source_options", {}).items())))
                              for s in sids], start, end)
            for sid in sids:
                n = len(got.get(sid, {}))
                last = max(got[sid]) if got.get(sid) else "—"
                flag = "✅" if n else "⚠️"
                ok &= bool(n)
                lines.append(f"- {flag} `{pname}` / `{sid}`: {n} registros, último {last}")
        except ContractError as exc:
            ok = False
            lines.append(f"- ❌ `{pname}`: **posible cambio de API** — {exc}")
        except (ProviderError, ConfigError) as exc:
            ok = False
            lines.append(f"- ❌ `{pname}`: error operativo — {exc}")
    m = store.load_json(args.data_dir / "manifest.json")
    if m:
        lines += ["", f"Último manifest: as_of={m['as_of']} estado={m['overall_status']}"]
        if m["overall_status"] != "ok":
            ok = False
            lines += [f"- `{k}`: {v}" for k, v in m["series_status"].items() if v != "ok"]
    report = "\n".join(lines) + "\n"
    print(report)
    if args.report:
        args.report.write_text(report, encoding="utf-8")
    return 0 if ok else 1


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser(prog="fxpipe")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("update", "backfill", "check", "healthcheck"):
        sp = sub.add_parser(name)
        _common(sp)
        if name in ("update", "backfill"):
            sp.add_argument("--dry-run", action="store_true")
        if name == "backfill":
            sp.add_argument("--start", type=date.fromisoformat, required=True)
        if name == "healthcheck":
            sp.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    if args.cmd == "update":
        return cmd_update(args)
    if args.cmd == "backfill":
        return cmd_update(args, backfill=args.start)
    if args.cmd == "check":
        return cmd_check(args)
    return cmd_healthcheck(args)


if __name__ == "__main__":
    sys.exit(main())
