"""종목 이름 검색.

한국투자증권이 공개하는 종목 마스터(KOSPI·KOSDAQ, 키 불필요)를 받아 data/symbols.json 에 캐시합니다.
주소와 파일 형식은 공식 예제(koreainvestment/open-trading-api 의 stocks_info)와 같습니다.
받지 못하면 내장 목록과 검색 기록에 남은 이름으로 검색합니다. SYMBOL_DOWNLOAD=off 이면 받지 않습니다.
"""
from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import secrets
import time
import zipfile
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

import httpx

log = logging.getLogger("symbols")

MASTER_URL = "https://new.real.download.dws.co.kr/common/master/{name}_code.mst.zip"
# 각 줄 끝의 고정 길이 필드 글자 수 (공식 예제 field_specs 의 합: KOSPI 227, KOSDAQ 221)
MASTERS = {"kospi": ("KOSPI", 227), "kosdaq": ("KOSDAQ", 221)}
MAX_ZIP_BYTES = 10 * 1024 * 1024   # 내려받는 zip 크기 상한
MAX_MST_BYTES = 30 * 1024 * 1024   # 압축을 푼 마스터 파일 크기 상한 (압축 폭탄 방지)
MIN_ROWS = 100                     # 이보다 적게 읽히면 형식이 바뀐 것으로 보고 기존 목록을 유지합니다
REFRESH_SEC = 12 * 3600
RETRY_SEC = 10 * 60
NAME_MAX = 40

CODE_RE = re.compile(r"^[0-9A-Z]{6}$")
QUERY_CODE_RE = re.compile(r"^[0-9A-Z]{1,6}$")
# 그룹코드 -> 표시 이름. ELW·신주인수권 등은 검색 목록에서 뺍니다.
KINDS = {"ST": "주식", "EF": "ETF", "FE": "ETF", "EN": "ETN", "RT": "리츠", "MF": "투자회사", "IF": "인프라",
         "DR": "DR", "FS": "외국주", "BC": "수익증권", "SC": "선박"}
KIND_ORDER = {"주식": 0, "ETF": 1}

# 마스터를 받기 전이거나 받지 못했을 때 쓰는 목록
BUILTIN = [
    ("005930", "삼성전자", "KOSPI", "주식"), ("000660", "SK하이닉스", "KOSPI", "주식"),
    ("373220", "LG에너지솔루션", "KOSPI", "주식"), ("207940", "삼성바이오로직스", "KOSPI", "주식"),
    ("005380", "현대차", "KOSPI", "주식"), ("000270", "기아", "KOSPI", "주식"),
    ("068270", "셀트리온", "KOSPI", "주식"), ("035420", "NAVER", "KOSPI", "주식"),
    ("035720", "카카오", "KOSPI", "주식"), ("005490", "POSCO홀딩스", "KOSPI", "주식"),
    ("105560", "KB금융", "KOSPI", "주식"), ("006400", "삼성SDI", "KOSPI", "주식"),
    ("051910", "LG화학", "KOSPI", "주식"), ("012450", "한화에어로스페이스", "KOSPI", "주식"),
    ("069500", "KODEX 200", "KOSPI", "ETF"), ("122630", "KODEX 레버리지", "KOSPI", "ETF"),
]

POPULAR = frozenset(code for code, *_ in BUILTIN)  # 같은 수준으로 맞는 종목끼리는 이 종목들을 먼저 보여 줍니다

CHOSUNG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_CHOSUNG_SET = set(CHOSUNG)


def clean_name(text) -> str:
    """외부에서 온 이름에서 제어 문자를 지우고 공백을 정리해 길이를 제한합니다."""
    name = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return " ".join(name.split())[:NAME_MAX]


def _key(text: str) -> str:
    return "".join(text.split()).casefold()


def _initials(text: str) -> str:
    """'삼성전자' -> 'ㅅㅅㅈㅈ'. 한글이 아닌 글자는 그대로 둡니다 ('SK하이닉스' -> 'skㅎㅇㄴㅅ')."""
    out = []
    for ch in _key(text):
        if "가" <= ch <= "힣":
            out.append(CHOSUNG[(ord(ch) - 0xAC00) // 588])
        else:
            out.append(ch)
    return "".join(out)


@dataclass(frozen=True)
class Symbol:
    code: str
    name: str
    market: str
    kind: str
    key: str = field(repr=False, compare=False)
    initials: str = field(repr=False, compare=False)
    size: int = field(default=9, repr=False, compare=False)  # 시가총액 규모 1 대형 · 2 중형 · 3 소형 · 9 모름

    @classmethod
    def make(cls, code: str, name: str, market: str = "", kind: str = "", size: int = 9) -> "Symbol":
        return cls(code, name, market, kind, _key(name), _initials(name), size)

    def as_dict(self) -> dict:
        return {"code": self.code, "name": self.name, "market": self.market, "kind": self.kind}


def parse_master(data: bytes, market: str, tail: int) -> list[tuple[str, str, str, str, int]]:
    """KIS 종목 마스터(.mst, cp949 고정 길이)를 (코드, 이름, 시장, 종류, 규모) 목록으로 바꿉니다.

    한 줄: 단축코드(9) + 표준코드(12) + 한글명(가변) + 고정 길이 필드(tail).
    고정 길이 필드는 그룹코드(2) + 시가총액 규모(1) 로 시작합니다.
    """
    text = data.decode("cp949", errors="replace")
    rows = []
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if len(line) <= 21 + tail:
            continue
        code = line[:9].strip()
        fixed = line[-tail:]
        kind = KINDS.get(fixed[:2])
        size = int(fixed[2]) if fixed[2] in "123" else 9
        name = clean_name(line[21:-tail])
        if CODE_RE.match(code) and kind and name:
            rows.append((code, name, market, kind, size))
    return rows


def _unzip_master(blob: bytes, member: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir() and i.filename.lower().endswith(".mst")]
        info = next((i for i in infos if i.filename.lower() == member), infos[0] if infos else None)
        if info is None:
            raise ValueError("zip 안에 .mst 파일이 없습니다")
        if info.file_size > MAX_MST_BYTES:
            raise ValueError("마스터 파일이 너무 큽니다")
        with zf.open(info) as f:
            data = f.read(MAX_MST_BYTES + 1)  # 헤더의 크기가 틀려도 상한을 넘겨 읽지 않습니다
    if len(data) > MAX_MST_BYTES:
        raise ValueError("마스터 파일이 너무 큽니다")
    return data


async def _fetch(client: httpx.AsyncClient, url: str) -> bytes:
    async with client.stream("GET", url) as r:
        if r.status_code != 200:
            raise ValueError(f"HTTP {r.status_code}")
        chunks, size = [], 0
        async for chunk in r.aiter_bytes():
            size += len(chunk)
            if size > MAX_ZIP_BYTES:
                raise ValueError("내려받는 파일이 너무 큽니다")
            chunks.append(chunk)
    return b"".join(chunks)


class SymbolBook:
    """종목 목록과 검색. 마스터는 백그라운드에서 받고, 받는 동안에는 내장 목록으로 검색합니다."""

    def __init__(self, cache_file: Path, download: bool = True, transport: httpx.AsyncBaseTransport | None = None):
        self.cache_file = cache_file
        self.download = download
        self._transport = transport  # 테스트용
        self._items: list[Symbol] = []
        self._by_code: dict[str, Symbol] = {}
        self._known: dict[str, Symbol] = {}  # 검색 기록 등에서 알게 된 이름 (목록에 없을 때만 씀)
        self._master = False
        self._fetched_at = 0.0
        self._failed_at = 0.0
        self._task: asyncio.Task | None = None
        self._set(BUILTIN)
        self._load_cache()

    @property
    def ready(self) -> bool:
        """전체 종목 마스터를 쓰고 있는지."""
        return self._master

    def _set(self, rows) -> None:
        items, by_code = [], {}
        for code, name, market, kind, *rest in rows:
            if code in by_code:
                continue
            size = rest[0] if rest and rest[0] in (1, 2, 3) else 9
            s = Symbol.make(code, name, market, kind, size)
            items.append(s)
            by_code[code] = s
        self._items, self._by_code = items, by_code

    # ---- 캐시
    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            rows = [(row[0], clean_name(row[1]), str(row[2]), str(row[3]), *row[4:5]) for row in data["items"]
                    if isinstance(row, list) and len(row) >= 4
                    and isinstance(row[0], str) and CODE_RE.match(row[0]) and isinstance(row[1], str)]
            fetched_at = float(data["fetched_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        if len(rows) >= MIN_ROWS:
            self._set(rows)
            self._master, self._fetched_at = True, fetched_at

    def _save_cache(self, rows) -> None:
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        pending = self.cache_file.with_name(f"{self.cache_file.name}.{secrets.token_hex(4)}.tmp")
        try:
            pending.write_text(json.dumps({"fetched_at": self._fetched_at, "items": rows}, ensure_ascii=False,
                                          separators=(",", ":")), encoding="utf-8")
            os.replace(pending, self.cache_file)
        finally:
            pending.unlink(missing_ok=True)

    # ---- 내려받기
    def refresh_if_stale(self) -> None:
        if not self.download or (self._task and not self._task.done()):
            return
        now = time.time()
        if self._master and now - self._fetched_at < REFRESH_SEC:
            return
        if now - self._failed_at < RETRY_SEC:
            return
        self._task = asyncio.create_task(self._refresh())

    async def _refresh(self) -> None:
        try:
            rows = []
            async with httpx.AsyncClient(timeout=20.0, verify=True, follow_redirects=False,
                                         transport=self._transport) as client:
                for name, (market, tail) in MASTERS.items():
                    blob = await _fetch(client, MASTER_URL.format(name=name))
                    part = parse_master(_unzip_master(blob, f"{name}_code.mst"), market, tail)
                    if len(part) < MIN_ROWS:
                        raise ValueError(f"{name} 마스터에서 종목을 {len(part)}개만 읽었습니다")
                    rows += part
            self._set(rows)
            self._master, self._fetched_at = True, time.time()
            try:
                self._save_cache(rows)
            except OSError as e:
                log.warning("종목 목록 캐시를 저장하지 못했습니다 (%s)", type(e).__name__)
            log.info("종목 마스터 %d개를 받았습니다", len(rows))
        except Exception as e:
            self._failed_at = time.time()
            log.warning("종목 마스터를 받지 못했습니다 (%s: %s), 내장 목록으로 검색합니다",
                        type(e).__name__, clean_name(e)[:200])

    async def wait_ready(self, timeout: float) -> None:
        """처음 받는 중이면 잠시 기다립니다 (이미 목록이 있으면 바로 돌아갑니다)."""
        self.refresh_if_stale()
        task = self._task
        if not self._master and task and not task.done():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout)

    async def close(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task

    # ---- 조회
    def remember(self, code: str, name: str) -> None:
        """목록에 없는 종목의 이름을 기억해 둡니다 (마스터를 못 받았을 때 검색 기록의 이름)."""
        name = clean_name(name)
        if CODE_RE.match(code) and name and code not in self._by_code:
            self._known[code] = Symbol.make(code, name)

    def get(self, code: str) -> Symbol | None:
        return self._by_code.get(code) or self._known.get(code)

    def search(self, query: str, limit: int = 20, prefer=frozenset()) -> list[Symbol]:
        """맞는 정도(이름 전체 > 앞부분 > 코드 > 이름 중간 > 초성)로 먼저 나누고, 같은 수준에서는
        검색 기록(prefer) > 주요 종목 > 주식 > 시가총액 규모 > 짧은 이름 순서로 보여 줍니다."""
        q = _key(clean_name(query))
        if not q:
            return []
        q_code = q.upper() if QUERY_CODE_RE.match(q.upper()) else ""
        q_initials = q if any(ch in _CHOSUNG_SET for ch in q) and not any("가" <= ch <= "힣" for ch in q) else ""
        scored = []
        for s in [*self._items, *(k for c, k in self._known.items() if c not in self._by_code)]:
            if (q_code and s.code == q_code) or s.key == q:
                rank = 0
            elif s.key.startswith(q):
                rank = 1
            elif q_code and s.code.startswith(q_code):
                rank = 2
            elif q in s.key:
                rank = 3
            elif q_initials and s.initials.startswith(q_initials):
                rank = 4
            elif q_initials and q_initials in s.initials:
                rank = 5
            else:
                continue
            scored.append((rank, s.code not in prefer, s.code not in POPULAR, KIND_ORDER.get(s.kind, 2), s.size,
                           len(s.name), s.name, s.code, s))
        scored.sort(key=lambda x: x[:8])
        return [x[-1] for x in scored[:limit]]
