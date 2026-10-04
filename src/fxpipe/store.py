"""Persistencia en /data. Formato estable: una observación por línea para
que los diffs de git muestren exactamente qué cambió cada día."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

SCHEMA_VERSION = "1.0.0"


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
