"""Versioned writes to the existing per-symbol drawing files."""
import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile

from . import codes
from .drawing_merge import DrawingSet


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


def _load(path: Path):
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return DrawingSet(), "0"
    document = json.loads(raw)
    if not isinstance(document, dict) or not valid_drawings(document.get("drawings")):
        raise ValueError("저장된 그림을 읽을 수 없습니다.")
    if "sync" in document:
        state = DrawingSet.parse(document["sync"])
    else:
        state = DrawingSet.legacy(document["drawings"])
    return state, hashlib.sha256(raw).hexdigest()


def read_drawings(path: Path, code: str) -> dict:
    state, revision = _load(path)
    return {"code": code, "drawings": state.drawings(), "revision": revision}


def valid_drawings(value) -> bool:
    return isinstance(value, list) and all(isinstance(drawing, dict) for drawing in value)


def _replace_file(path, raw):
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


def _save(path, code, state):
    drawings = state.drawings()
    document = {"code": code, "drawings": drawings, "sync": state.packet()}
    raw = json.dumps(document, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    _replace_file(path, raw)
    return {"code": code, "drawings": drawings, "revision": hashlib.sha256(raw).hexdigest()}


def _writer(directory):
    identity = directory / ".drawing-writer"
    try:
        return uuid.UUID(hex=identity.read_text(encoding="ascii")).hex
    except FileNotFoundError:
        writer = uuid.uuid4().hex
        _replace_file(identity, writer.encode("ascii"))
        return writer


def write_drawings(path: Path, code: str, drawings: list, expected: str | None) -> tuple[dict, bool]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with _write_lock(path.parent):
        state, revision = _load(path)
        current = {"code": code, "drawings": state.drawings(), "revision": revision}
        # A write can complete before a page reload receives its acknowledgment.
        if current["drawings"] == drawings:
            return current, True
        if expected is not None and current["revision"] != expected:
            return current, False
        state.replace(drawings, _writer(path.parent))
        return _save(path, code, state), True


def export_sync(directory: Path) -> dict:
    symbols = {}
    if directory.exists():
        with _write_lock(directory):
            for path in directory.glob("*.json"):
                code = codes.normalize(path.stem.replace("_", ":"))
                if code and path.stem == codes.file_stem(code):
                    state, _ = _load(path)
                    if state.clock:
                        symbols[code] = state.packet()
    return {"version": 1, "symbols": symbols}


def merge_sync(directory: Path, payload: dict) -> bool:
    if (not isinstance(payload, dict) or type(payload.get("version")) is not int
            or payload["version"] != 1 or not isinstance(payload.get("symbols"), dict)):
        raise ValueError("그림 동기화 버전을 확인할 수 없습니다.")
    incoming = {}
    for code, packet in payload["symbols"].items():
        if not isinstance(code, str) or not code or codes.normalize(code) != code:
            raise ValueError("올바르지 않은 종목 코드입니다.")
        incoming[code] = DrawingSet.parse(packet)
    directory.mkdir(parents=True, exist_ok=True)
    changed = False
    with _write_lock(directory):
        for code, remote in incoming.items():
            path = directory / (codes.file_stem(code) + ".json")
            state, _ = _load(path)
            merged = state.merge(remote)
            if merged != state:
                _save(path, code, merged)
                changed = True
    return changed
