"""종목 검색 기록 (data/search_history.json). 최근에 연 종목이 앞에 옵니다."""
from __future__ import annotations

import json
import os
import secrets
import time
from pathlib import Path

from .codes import normalize
from .symbols import clean_name

MAX_ITEMS = 20


class SearchHistory:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        items = data.get("items") if isinstance(data, dict) else None
        out, seen = [], set()
        for it in items if isinstance(items, list) else []:
            if not isinstance(it, dict):
                continue
            code = it.get("code")
            if not isinstance(code, str) or normalize(code) != code or code in seen:
                continue
            at = it.get("at")
            seen.add(code)
            out.append({"code": code, "name": clean_name(it.get("name", "")),
                        "at": int(at) if isinstance(at, (int, float)) else 0})
        return out[:MAX_ITEMS]

    def add(self, code: str, name: str) -> list[dict]:
        items = [it for it in self.load() if it["code"] != code]
        items.insert(0, {"code": code, "name": clean_name(name), "at": int(time.time())})
        return self._save(items[:MAX_ITEMS])

    def remove(self, code: str) -> list[dict]:
        return self._save([it for it in self.load() if it["code"] != code])

    def clear(self) -> list[dict]:
        return self._save([])

    def _save(self, items: list[dict]) -> list[dict]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        pending = self.path.with_name(f"{self.path.name}.{secrets.token_hex(4)}.tmp")
        try:
            pending.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(pending, self.path)
        finally:
            pending.unlink(missing_ok=True)
        return items
