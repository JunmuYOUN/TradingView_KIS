"""Versioned writes to the existing per-symbol drawing files."""
import hashlib
import json
import os
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile


@contextmanager
def _write_lock(directory: Path):
    # The desktop EXE and browser server can share one data directory.
    with (directory / ".write.lock").open("a+b") as file:
        if os.name == "nt":
            import msvcrt

            file.seek(0)
            msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                file.seek(0)
                msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(file, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(file, fcntl.LOCK_UN)


def read_drawings(path: Path, code: str) -> dict:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {"code": code, "drawings": [], "revision": "0"}
    document = json.loads(raw)
    if not isinstance(document, dict) or not valid_drawings(document.get("drawings")):
        raise ValueError("저장된 그림을 읽을 수 없습니다.")
    return {"code": code, "drawings": document["drawings"], "revision": hashlib.sha256(raw).hexdigest()}


def valid_drawings(value) -> bool:
    return isinstance(value, list) and all(isinstance(drawing, dict) for drawing in value)


def write_drawings(path: Path, code: str, drawings: list, expected: str | None) -> tuple[dict, bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock(path.parent):
        current = read_drawings(path, code)
        # A write can complete before a page reload receives its acknowledgment.
        if current["drawings"] == drawings:
            return current, True
        if expected is not None and current["revision"] != expected:
            return current, False
        raw = json.dumps({"code": code, "drawings": drawings}, ensure_ascii=False, indent=1).encode("utf-8")
        with NamedTemporaryFile(dir=path.parent, delete=False) as file:
            pending = Path(file.name)
            try:
                file.write(raw)
                file.flush()
                os.fsync(file.fileno())
            except BaseException:
                file.close()
                pending.unlink(missing_ok=True)
                raise
        try:
            os.replace(pending, path)
        finally:
            pending.unlink(missing_ok=True)
        return {"code": code, "drawings": drawings, "revision": hashlib.sha256(raw).hexdigest()}, True
