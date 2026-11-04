import json
import os
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

_locks: dict[str, threading.RLock] = {}
_registry_lock = threading.Lock()


def lock_for(path: Path) -> threading.RLock:
    key = str(Path(path).resolve())
    with _registry_lock:
        return _locks.setdefault(key, threading.RLock())


def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    with lock_for(path):
        if not path.is_file():
            return default
        return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    payload = json.dumps(data, ensure_ascii=False)  # serialise first so a bad value never touches the file
    with lock_for(path):
        _atomic_write(path, payload)


def update_json(path: Path, fn: Callable[[Any], Any], default: Any = None) -> Any:
    """Read-modify-write under the file's lock; `fn` returns the new content."""
    path = Path(path)
    with lock_for(path):
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else default
        updated = fn(current)
        _atomic_write(path, json.dumps(updated, ensure_ascii=False))
        return updated


def append_jsonl(path: Path, record: dict) -> None:
    path = Path(path)
    line = json.dumps(record, ensure_ascii=False)
    if "\n" in line:  # json.dumps escapes newlines inside strings; this guards the invariant
        raise ValueError("record serialised across several lines")
    with lock_for(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def read_jsonl(path: Path) -> list[dict]:
    path = Path(path)
    with lock_for(path):
        if not path.is_file():
            return []
        text = path.read_text(encoding="utf-8")
    records = []
    for line in text.splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a torn trailing line from an interrupted append
    return records


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
