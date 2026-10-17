"""Persistencia en /data. Formato estable: una observación por línea para
que los diffs de git muestren exactamente qué cambió cada día."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

# 1.1.0: agrega rates_daily_compact.json (compatible con 1.0.x)
# 1.2.0: agrega semantic_model.json y semantic_model.yaml, el modelo semántico (compatible con 1.1.x)
SCHEMA_VERSION = "1.2.0"


def load_official(data_dir: Path, series_id: str) -> dict[str, float]:
    """Solo valores oficiales: los rellenados se recalculan en cada corrida."""
    path = data_dir / "series" / f"{series_id}.json"
    if not path.exists():
        return {}
    doc = json.loads(path.read_text(encoding="utf-8"))
    return {o["date"]: float(o["value"]) for o in doc.get("observations", []) if not o.get("filled")}


def load_json(path: Path) -> dict | None:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _dump_rows(rows: list[dict]) -> str:
    if not rows:
        return "[]"
    lines = ",\n    ".join(json.dumps(r, ensure_ascii=False, separators=(", ", ": ")) for r in rows)
    return "[\n    " + lines + "\n  ]"


def render(doc: dict, rows_key: str | None = None) -> str:
    """JSON con indentación, salvo la lista larga `rows_key` (1 fila/línea)."""
    if rows_key is None or rows_key not in doc:
        return json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    head = {k: v for k, v in doc.items() if k != rows_key}
    body = json.dumps(head, ensure_ascii=False, indent=2)
    body = body[:-2] + f',\n  "{rows_key}": {_dump_rows(doc[rows_key])}\n}}' if head else \
        f'{{\n  "{rows_key}": {_dump_rows(doc[rows_key])}\n}}'
    json.loads(body)  # verificación defensiva
    return body + "\n"


def render_columnar(doc: dict) -> str:
    """JSON del formato en columnas: sin espacios en las listas largas y una
    columna por línea, para que el archivo pese lo mínimo (lo descarga cada
    visitante del dashboard). Los diffs diarios de git viven en `series/` y
    `rates_daily.json`; este archivo es una vista derivada de ellos."""
    def compact(x) -> str:
        return json.dumps(x, ensure_ascii=False, separators=(",", ":"))

    head = {k: v for k, v in doc.items() if k not in ("dates", "columns", "series")}
    parts = [f"  {compact(k)}: {compact(v)}" for k, v in head.items()]
    parts.append('  "series": ' + json.dumps(doc["series"], ensure_ascii=False, indent=2).replace("\n", "\n  "))
    parts.append(f'  "dates": {compact(doc["dates"])}')
    cols = ",\n".join(f"    {compact(k)}: {compact(v)}" for k, v in doc["columns"].items())
    parts.append('  "columns": {\n' + cols + "\n  }")
    body = "{\n" + ",\n".join(parts) + "\n}"
    assert json.loads(body) == json.loads(json.dumps(doc)), "render_columnar alteró el documento"
    return body + "\n"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".json")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def content_hash(texts: dict[str, str]) -> str:
    h = hashlib.sha256()
    for name in sorted(texts):
        h.update(name.encode())
        h.update(texts[name].encode())
    return h.hexdigest()
