"""Lógica pura (sin red ni disco): fácil de probar y de auditar."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

# Una serie diaria: {"YYYY-MM-DD": {"value": float, "filled": bool}}
Daily = dict[str, dict]


def d(s: str) -> date:
    return date.fromisoformat(s)


def round_js(x: float, decimals: int) -> float:
    """Redondea igual que `Number(x.toFixed(decimals))` de JavaScript.

    Opera sobre el valor binario exacto de `x` y, solo en un empate exacto
    (p. ej. 18.03125 con 4 decimales), va al número mayor en magnitud. `round`
    de Python resuelve ese empate al par: es la única diferencia entre los dos.
    El dashboard recalcula las cruzadas en el navegador a partir de
    `rates_daily_compact.json`; usar esta función aquí hace que ambos lados den
    exactamente el mismo número.
    """
    return float(Decimal(x).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP))


def fill_forward(official: dict[str, float], as_of: date, max_fill_days: int) -> Daily:
    """Convierte observaciones oficiales en serie de días calendario.

    Los días sin dato oficial heredan el último valor oficial, marcados
    filled=True, hasta `max_fill_days` días; después queda un hueco. Nunca
    se rellena más allá de `as_of` (no se inventa el futuro).
    """
    if not official:
        return {}
    dates = sorted(official)
    first, last_official = d(dates[0]), d(dates[-1])
    end = max(last_official, as_of)
    out: Daily = {}
    last_val, last_date = None, None
    cur = first
    while cur <= end:
        key = cur.isoformat()
        if key in official:
            last_val, last_date = official[key], cur
            out[key] = {"value": last_val, "filled": False}
        elif last_val is not None and (cur - last_date).days <= max_fill_days and cur <= as_of:
            out[key] = {"value": last_val, "filled": True}
        cur += timedelta(days=1)
    return out


def cross_rate(num: Daily, den: Daily, decimals: int) -> Daily:
    """Tasa cruzada vía USD: (COP/USD) / (MXN/USD) = COP/MXN.

    El redondeo es `round_js`: el navegador recalcula este mismo cociente.
    """
    out: Daily = {}
    for key in sorted(set(num) & set(den)):
        a, b = num[key], den[key]
        if b["value"] == 0:
            continue
        out[key] = {
            "value": round_js(a["value"] / b["value"], decimals),
            "filled": bool(a["filled"] or b["filled"]),
        }
    return out


def out_of_range(official: dict[str, float], lo: float, hi: float) -> list[str]:
    return [f"{k}={v}" for k, v in sorted(official.items()) if not (lo <= v <= hi)]


def jump_warnings(series: Daily, max_pct: float) -> list[str]:
    """Saltos entre observaciones oficiales consecutivas mayores al umbral."""
    pts = [(k, v["value"]) for k, v in sorted(series.items()) if not v["filled"]]
    warns = []
    for (k0, v0), (k1, v1) in zip(pts, pts[1:]):
        if v0 and abs(v1 / v0 - 1) * 100 > max_pct:
            warns.append(f"salto {k0}->{k1}: {v0} -> {v1} ({(v1 / v0 - 1) * 100:+.2f}%)")
    return warns


def compact_columns(order: list[str], meta: dict[str, dict]) -> list[str]:
    """Series que viajan como columna en `rates_daily_compact.json`.

    Son las oficiales visibles (`primary`) más las oficiales que son insumo de
    una cruzada visible, aunque estén ocultas (p. ej. la TRM por día de mercado).
    Las cruzadas no viajan: se recalculan con `meta[id]["inputs"]`. Se deduce de
    los metadatos, sin listas fijas.
    """
    needed = {i for s in order if meta[s]["primary"] for i in meta[s].get("inputs", [])}
    for i in sorted(needed):
        if meta[i].get("inputs"):
            raise ValueError(f"El insumo {i} es una cruzada: el archivo compacto solo admite insumos oficiales")
    return [s for s in order if not meta[s].get("inputs") and (meta[s]["primary"] or s in needed)]


def compact_document(order: list[str], meta: dict[str, dict], daily: dict[str, Daily],
                     as_of: str, schema_version: str) -> dict:
    """Formato en columnas: una lista de valores por serie, alineada con `dates`.

    - `dates`: unión de las fechas de las columnas, en orden.
    - `columns[id].values[i]`: valor del día `dates[i]` (oficial o rellenado), o null si no hay.
    - `columns[id].filled`: índices de `dates` donde el valor es relleno (no oficial).
    - `series`: metadatos de las columnas y de las cruzadas visibles; cada cruzada trae
      `inputs` [numerador, denominador] y `decimals`. El cliente la calcula como
      `Number((num / den).toFixed(decimals))` y marca relleno si algún insumo lo está.
    """
    cols = compact_columns(order, meta)
    visible = [s for s in order if s in cols or meta[s]["primary"]]
    dates = sorted(set().union(*[daily[s].keys() for s in cols])) if cols else []
    columns = {}
    for sid in cols:
        vals, filled = [], []
        for i, k in enumerate(dates):
            v = daily[sid].get(k)
            vals.append(v["value"] if v else None)
            if v and v["filled"]:
                filled.append(i)
        columns[sid] = {"filled": filled, "values": vals}
    return {
        "schema_version": schema_version,
        "layout": "columnar",
        "as_of": as_of,
        "series": {s: meta[s] for s in visible},
        "dates": dates,
        "columns": columns,
    }


def last_official(series: Daily) -> str | None:
    keys = [k for k, v in series.items() if not v["filled"]]
    return max(keys) if keys else None


def _run_start(official_keys: list[str], idx: int, series: Daily) -> int:
    """Inicio del tramo de días consecutivos con el mismo valor oficial
    (p. ej., una TRM vigente viernes-lunes cuenta como un solo registro)."""
    i = idx
    while i > 0:
        a, b = official_keys[i - 1], official_keys[i]
        if (d(b) - d(a)).days == 1 and series[a]["value"] == series[b]["value"]:
            i -= 1
        else:
            break
    return i


def snapshot(series: Daily, as_of: date) -> dict:
    """Último valor oficial a `as_of`, variación vs. el registro oficial
    anterior y, si existe, el próximo valor oficial ya publicado."""
    official = sorted(k for k, v in series.items() if not v["filled"])
    upto = [k for k in official if d(k) <= as_of]
    if not upto:
        return {"value": None, "date": None}
    k = upto[-1]
    val = series[k]["value"]
    snap = {"value": val, "date": k, "days_since_official": (as_of - d(k)).days}
    start = _run_start(upto, len(upto) - 1, series)
    if start > 0:
        pk = upto[start - 1]
        prev = series[pk]["value"]
        snap.update({
            "prev_value": prev,
            "prev_date": pk,
            "change_abs": round(val - prev, 6),
            "change_pct": round((val / prev - 1) * 100, 4) if prev else None,
        })
    prev_key, nxt = k, None
    for x in (x for x in official if d(x) > as_of):
        same_run = (d(x) - d(prev_key)).days == 1 and series[x]["value"] == series[prev_key]["value"]
        if not same_run:
            nxt = x
            break
        prev_key = x
    if nxt:
        snap["next"] = {"date": nxt, "value": series[nxt]["value"]}
    return snap
