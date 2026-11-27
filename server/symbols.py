"""종목 이름 검색.

한국투자증권이 공개하는 종목 마스터(키 불필요)를 받아 data/symbols.json 에 캐시합니다.
- 국내: KOSPI·KOSDAQ (kospi_code.mst / kosdaq_code.mst, 고정 길이)
- 미국: NASDAQ·NYSE·AMEX (nasmst.cod / nysmst.cod / amsmst.cod, 탭 구분)
- 지수: 국내 업종코드(idxcode.mst), 해외지수 마스터(frgn_code.mst)의 미국지수
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
from itertools import chain
from pathlib import Path

import httpx

from .codes import DOMESTIC_RE, EXCHANGES, OVERSEAS_RE, is_index, is_us_index, is_yahoo, normalize, ticker, yahoo_candidate
from .yahoo_lookup import YahooLookup
from .yahoo_symbols import YAHOO_SYMBOLS, YahooInstrument

log = logging.getLogger("symbols")

MASTER_BASE = "https://new.real.download.dws.co.kr/common/master/"
MAX_ZIP_BYTES = 10 * 1024 * 1024   # 내려받는 zip 크기 상한
MAX_MST_BYTES = 30 * 1024 * 1024   # 압축을 푼 마스터 파일 크기 상한 (압축 폭탄 방지)
MIN_ROWS = 100                     # 이보다 적게 읽히면 형식이 바뀐 것으로 보고 그 시장은 이전 목록을 유지합니다
REFRESH_SEC = 12 * 3600
RETRY_SEC = 10 * 60
NAME_MAX = 40

# 국내 그룹코드 -> 표시 이름. ELW·신주인수권 등은 검색 목록에서 뺍니다.
KINDS = {"ST": "주식", "EF": "ETF", "FE": "ETF", "EN": "ETN", "RT": "리츠", "MF": "투자회사", "IF": "인프라",
         "DR": "DR", "FS": "외국주", "BC": "수익증권", "SC": "선박"}
# 해외 주식 마스터의 Security type. 지수는 별도 지수 마스터에서 조회합니다.
OVERSEAS_KINDS = {"2": "주식", "3": "ETF"}
KIND_ORDER = {"지수": -1, "주식": 0, "ETF": 1}

# 마스터를 받기 전이거나 받지 못했을 때 쓰는 목록 (코드, 이름, 시장, 종류, 규모, 영문명)
BUILTIN = [
    ("005930", "삼성전자", "KOSPI", "주식"), ("000660", "SK하이닉스", "KOSPI", "주식"),
    ("373220", "LG에너지솔루션", "KOSPI", "주식"), ("207940", "삼성바이오로직스", "KOSPI", "주식"),
    ("005380", "현대차", "KOSPI", "주식"), ("000270", "기아", "KOSPI", "주식"),
    ("068270", "셀트리온", "KOSPI", "주식"), ("035420", "NAVER", "KOSPI", "주식"),
    ("035720", "카카오", "KOSPI", "주식"), ("005490", "POSCO홀딩스", "KOSPI", "주식"),
    ("105560", "KB금융", "KOSPI", "주식"), ("006400", "삼성SDI", "KOSPI", "주식"),
    ("051910", "LG화학", "KOSPI", "주식"), ("012450", "한화에어로스페이스", "KOSPI", "주식"),
    ("069500", "KODEX 200", "KOSPI", "ETF"), ("122630", "KODEX 레버리지", "KOSPI", "ETF"),
    ("NAS:AAPL", "애플", "NASDAQ", "주식", 9, "APPLE INC"),
    ("NAS:MSFT", "마이크로소프트", "NASDAQ", "주식", 9, "MICROSOFT CORP"),
    ("NAS:NVDA", "엔비디아", "NASDAQ", "주식", 9, "NVIDIA CORP"),
    ("NAS:TSLA", "테슬라", "NASDAQ", "주식", 9, "TESLA INC"),
    ("NAS:AMZN", "아마존닷컴", "NASDAQ", "주식", 9, "AMAZON.COM INC"),
    ("NAS:GOOGL", "알파벳 A", "NASDAQ", "주식", 9, "ALPHABET INC"),
    ("NAS:META", "메타 플랫폼스", "NASDAQ", "주식", 9, "META PLATFORMS INC"),
    ("NAS:QQQ", "인베스코 QQQ", "NASDAQ", "ETF", 9, "INVESCO QQQ TRUST"),
    ("AMS:SPY", "SPDR S&P 500", "AMEX", "ETF", 9, "SPDR S&P 500 ETF TRUST"),
    ("NYS:KO", "코카콜라", "NYSE", "주식", 9, "COCA-COLA CO"),
]
# 첫 실행·오프라인에서 제공하는 지수. 대표명, 영문명, 검색 별칭 순서입니다.
BENCHMARKS = {
    "INDEX:0001": ("코스피", "KOSPI", ("코스피 지수", "코스피 종합", "종합주가지수", "KS11", "^KS11")),
    "INDEX:1001": ("코스닥", "KOSDAQ", ("코스닥 지수", "코스닥 종합", "KQ11", "^KQ11")),
    "INDEX:2001": ("코스피 200", "KOSPI 200", ("코스피200 지수", "K200", "KS200", "^KS200")),
    "INDEX:3003": ("코스닥 150", "KOSDAQ 150", ("코스닥150 지수", "KSQ150", "KQ150")),
    "INDEX:.DJI": ("다우존스 30", "Dow Jones Industrial Average",
                   ("다우", "다우 30", "다우존스", "다우존스산업평균지수", "Dow 30", "DJI", "DJIA", "^DJI")),
    "INDEX:COMP": ("나스닥 종합", "NASDAQ Composite",
                   ("나스닥", "나스닥 지수", "나스닥 종합지수", "NASDAQ", "IXIC", "^IXIC")),
    "INDEX:NDX": ("나스닥 100", "NASDAQ 100", ("나스닥100 지수", "NASDAQ-100", "^NDX")),
    "INDEX:SPX": ("S&P 500", "S&P 500 Index", ("S&P", "SP500", "SNP500", "에스앤피500", "GSPC", "^GSPC")),
    "INDEX:VIX": ("VIX 변동성 지수", "Cboe Volatility Index",
                  ("VIX", "VIX 지수", "VIX 현물", "VIX spot", "Cboe VIX", "빅스", "빅스 지수", "공포지수", "변동성",
                   "미국 변동성 지수", "Fear Index", "한투 VIX", "KIS VIX")),
}
POPULAR = frozenset(row[0] for row in BUILTIN) | BENCHMARKS.keys()

CHOSUNG = "ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ"
_CHOSUNG_SET = set(CHOSUNG)


def clean_name(text) -> str:
    """외부에서 온 이름에서 제어 문자를 지우고 공백을 정리해 길이를 제한합니다."""
    name = "".join(ch if ch.isprintable() else " " for ch in str(text or ""))
    return " ".join(name.split())[:NAME_MAX]


def _key(text: str) -> str:
    return "".join(text.split()).casefold()


_vix_name, _vix_english, _vix_aliases = BENCHMARKS["INDEX:VIX"]
_VIX_QUERIES = frozenset(_key(name) for name in (_vix_name, _vix_english, *_vix_aliases))


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
    names: tuple = field(repr=False, compare=False)  # 검색용으로 정리한 이름들 (한글명, 영문명)
    ticker_key: str = field(repr=False, compare=False)  # 검색용 코드·티커
    initials: str = field(repr=False, compare=False)
    size: int = field(default=9, repr=False, compare=False)  # 시가총액 규모 1 대형 · 2 중형 · 3 소형 · 9 모름
    english: str = field(default="", repr=False, compare=False)
    precision: int | None = field(default=None, repr=False, compare=False)

    @classmethod
    def make(cls, code: str, name: str, market: str = "", kind: str = "", size: int = 9,
             english: str = "", instrument: YahooInstrument | None = None) -> "Symbol":
        aliases = [name, english]
        instrument = instrument or YAHOO_SYMBOLS.get(code)
        if instrument:
            aliases.extend((kind, market, *instrument.aliases))
        elif kind == "지수":
            aliases.extend(("지수", "index", "미국지수" if is_us_index(code) else "국내지수"))
            if code in BENCHMARKS:
                aliases.extend(BENCHMARKS[code][2])
        names = tuple(_key(n) for n in aliases if n)
        return cls(code, name, market, kind, names, ticker(code).casefold(), _initials(name), size, english,
                   instrument.precision if instrument else None)

    def row(self) -> tuple:
        return (self.code, self.name, self.market, self.kind, self.size, self.english)

    def as_dict(self) -> dict:
        item = {"code": self.code, "symbol": ticker(self.code), "name": self.name,
                "market": self.market, "kind": self.kind}
        if self.precision is not None:
            item.update(source="yahoo", precision=self.precision)
        elif is_index(self.code):
            item["source"] = "kis"
        return item


def parse_master(data: bytes, market: str, tail: int) -> list[tuple]:
    """국내 종목 마스터(.mst, cp949 고정 길이)를 (코드, 이름, 시장, 종류, 규모) 목록으로 바꿉니다.

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
        if DOMESTIC_RE.match(code) and kind and name:
            rows.append((code, name, market, kind, size))
    return rows


def parse_overseas_master(data: bytes, excd: str) -> list[tuple]:
    """미국 종목 마스터(.cod, cp949 탭 구분)를 (코드, 이름, 시장, 종류, 규모, 영문명) 목록으로 바꿉니다.

    열: 국가, 거래소ID, 거래소코드, 거래소명, 심볼(4), 실시간심볼, 한글명(6), 영문명(7), 종류(8), ...
    """
    text = data.decode("cp949", errors="replace")
    rows = []
    for line in text.split("\n"):
        cols = line.rstrip("\r").split("\t")
        if len(cols) < 9:
            continue
        kind = OVERSEAS_KINDS.get(cols[8].strip())
        code = f"{excd}:{cols[4].strip().upper()}"
        english = clean_name(cols[7])
        name = clean_name(cols[6]) or english
        if kind and name and OVERSEAS_RE.match(code):
            rows.append((code, name, EXCHANGES[excd], kind, 9, english))
    return rows


def parse_domestic_indices(data: bytes) -> list[tuple]:
    """idxcode.mst의 주가지수 계열. 시장구분 1바이트, 업종코드 4바이트, 한글명 40바이트."""
    rows = []
    for line in data.splitlines():
        group, number = line[:1], line[1:5]
        if group not in (b"0", b"1", b"2", b"3", b"4") or len(number) != 4 or not number.isdigit():
            continue
        name = clean_name(line[5:45].decode("cp949", errors="replace"))
        if not name or "선물" in name or "F-" in name:
            continue
        # 코스피·코스닥은 같은 업종명을 사용하므로 결과에서도 시장 이름을 함께 보여 줍니다.
        prefix = {b"0": "코스피", b"1": "코스닥"}.get(group)
        if prefix and not name.upper().startswith((prefix, "KOSPI", "KOSDAQ", "VKOSPI")):
            name = f"{prefix} {name}"
        rows.append((f"INDEX:{number.decode('ascii')}", name, "KRX", "지수"))
    return rows


def parse_us_indices(data: bytes) -> list[tuple]:
    """frgn_code.mst의 P(미국지수), 501(미국) 레코드. 필드 너비는 바이트 기준입니다."""
    rows = []
    for line in data.splitlines():
        if len(line) < 104 or line[:1] != b"P" or line[101:104] != b"501":
            continue
        code = "INDEX:" + line[1:11].decode("ascii", errors="replace").strip()
        if not is_us_index(code):
            continue
        english = clean_name(line[11:50].decode("cp949", errors="replace"))
        name = clean_name(line[50:90].decode("cp949", errors="replace")) or english
        if name:
            rows.append((code, name, "미국", "지수", 9, english))
    return rows


def _domestic(name: str, market: str, tail: int):
    return name, market, f"{name}_code.mst.zip", f"{name}_code.mst", lambda data: parse_master(data, market, tail)


def _overseas(name: str, excd: str):
    return (name, EXCHANGES[excd], f"{name}mst.cod.zip", f"{name}mst.cod",
            lambda data: parse_overseas_master(data, excd))


# (이름, 시장, zip 파일, zip 안의 파일, 해석 함수). 고정 길이 글자 수는 공식 예제 field_specs 의 합입니다.
SOURCES = [
    _domestic("kospi", "KOSPI", 227), _domestic("kosdaq", "KOSDAQ", 221),
    _overseas("nas", "NAS"), _overseas("nys", "NYS"), _overseas("ams", "AMS"),
    ("idxcode", "KRX", "idxcode.mst.zip", "idxcode.mst", parse_domestic_indices),
    ("frgn_code", "미국", "frgn_code.mst.zip", "frgn_code.mst", parse_us_indices),
]


def _unzip_master(blob: bytes, member: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        infos = [i for i in zf.infolist() if not i.is_dir()
                 and i.filename.lower().endswith((".mst", ".cod"))]
        info = next((i for i in infos if i.filename.lower() == member), infos[0] if infos else None)
        if info is None:
            raise ValueError("zip 안에 마스터 파일이 없습니다")
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

    def __init__(self, cache_file: Path, download: bool = True, transport: httpx.AsyncBaseTransport | None = None,
                 yahoo_transport: httpx.AsyncBaseTransport | None = None):
        self.cache_file = cache_file
        self.download = download
        self._transport = transport  # 테스트용
        self._yahoo = YahooLookup(cache_file.with_name("yahoo-instruments.json"), yahoo_transport)
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
        """종목 마스터를 쓰고 있는지 (내장 목록만 쓰는 중이면 False)."""
        return self._master

    def _set(self, rows) -> None:
        items, by_code = [], {}
        benchmarks = ((code, name, "미국" if is_us_index(code) else "KRX", "지수", 1, english)
                      for code, (name, english, _) in BENCHMARKS.items())
        yahoo = ((code, instrument.name, instrument.market, instrument.kind, 1, instrument.english)
                 for code, instrument in YAHOO_SYMBOLS.items())
        for code, name, market, kind, *rest in chain(benchmarks, yahoo, rows):
            if code in by_code:
                continue
            size = rest[0] if rest and rest[0] in (1, 2, 3) else 9
            english = rest[1] if len(rest) > 1 and isinstance(rest[1], str) else ""
            s = Symbol.make(code, name, market, kind, size, english)
            items.append(s)
            by_code[code] = s
        self._items, self._by_code = items, by_code

    # ---- 캐시
    def _load_cache(self) -> None:
        try:
            data = json.loads(self.cache_file.read_text(encoding="utf-8"))
            rows = [(row[0], clean_name(row[1]), str(row[2]), str(row[3]), *row[4:5],
                     *(clean_name(x) for x in row[5:6])) for row in data["items"]
                    if isinstance(row, list) and len(row) >= 4 and normalize(row[0]) == row[0] and not is_yahoo(row[0])
                    and isinstance(row[1], str)]
            fetched_at = float(data["fetched_at"])
        except (OSError, ValueError, KeyError, TypeError):
            return
        if len(rows) >= MIN_ROWS:
            self._set(rows)
            self._master = True
            # 이전 버전 캐시는 현재 검색에 사용하면서 지수 마스터도 곧바로 갱신합니다.
            self._fetched_at = fetched_at if any(is_index(row[0]) for row in rows) else 0.0

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
        rows, failed = [], []
        try:
            async with httpx.AsyncClient(timeout=20.0, verify=True, follow_redirects=False,
                                         transport=self._transport) as client:
                for name, market, zip_name, member, parse in SOURCES:
                    try:
                        part = parse(_unzip_master(await _fetch(client, MASTER_BASE + zip_name), member))
                        min_rows = 10 if market in ("KRX", "미국") else MIN_ROWS
                        if len(part) < min_rows:
                            raise ValueError(f"종목을 {len(part)}개만 읽었습니다")
                    except Exception as e:
                        failed.append(name)
                        log.warning("%s 종목 마스터를 받지 못했습니다 (%s: %s)", name, type(e).__name__,
                                    clean_name(e)[:200])
                        # 그 시장은 지금 쓰는 목록(전에 받은 마스터, 없으면 내장 목록)을 계속 씁니다
                        part = [s.row() for s in self._items if s.market == market]
                    rows += part
        except Exception as e:  # 연결 자체를 만들지 못한 경우
            failed = [s[0] for s in SOURCES]
            log.warning("종목 마스터를 받지 못했습니다 (%s)", type(e).__name__)
        now = time.time()
        if len(failed) == len(SOURCES) or len(rows) < MIN_ROWS:
            self._failed_at = now
            log.warning("종목 마스터를 받지 못해 내장 목록으로 검색합니다")
            return
        self._set(rows)
        self._master = True
        if failed:
            self._failed_at = now  # 일부 시장만 받았으면 잠시 뒤 다시 시도합니다
        else:
            self._fetched_at = now
        try:
            self._save_cache(rows)
        except OSError as e:
            log.warning("종목 목록 캐시를 저장하지 못했습니다 (%s)", type(e).__name__)
        log.info("종목 마스터 %d개를 받았습니다%s", len(rows), f" (실패: {', '.join(failed)})" if failed else "")

    async def wait_ready(self, timeout: float) -> None:
        """처음 받는 중이면 잠시 기다립니다 (이미 목록이 있으면 바로 돌아갑니다)."""
        self.refresh_if_stale()
        task = self._task
        if not self._master and task and not task.done():
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.shield(task), timeout)

    async def close(self) -> None:
        await self._yahoo.close()
        if self._task and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await self._task

    # ---- 조회
    def remember(self, code: str, name: str) -> None:
        """목록에 없는 종목의 이름을 기억해 둡니다 (마스터를 못 받았을 때 검색 기록의 이름)."""
        name = clean_name(name)
        if normalize(code) == code and not is_index(code) and not is_yahoo(code) and name and code not in self._by_code:
            self._known[code] = Symbol.make(code, name)

    def get(self, code: str) -> Symbol | None:
        if item := self._yahoo.get(code):
            return self._yahoo_symbol(code, item)
        return self._by_code.get(code) or self._known.get(code)

    @staticmethod
    def _yahoo_symbol(code: str, item: YahooInstrument) -> Symbol:
        return Symbol.make(code, item.name, item.market, item.kind, english=item.english, instrument=item)

    async def resolve_yahoo(self, code_or_query: str) -> Symbol | None:
        code = yahoo_candidate(code_or_query)
        if not code:
            return None
        if code in YAHOO_SYMBOLS:
            return self._by_code[code]
        item = await self._yahoo.resolve(code)
        return self._yahoo_symbol(code, item) if item else None

    def search(self, query: str, limit: int = 20, prefer=frozenset()) -> list[Symbol]:
        """맞는 정도(이름·코드·티커 전체 > 이름 앞부분 > 코드·티커 앞부분 > 이름 중간 > 초성)로 먼저 나누고,
        같은 수준에서는 검색 기록(prefer) > 주요 종목 > 주식 > 시가총액 규모 > 짧은 이름 순서로 보여 줍니다."""
        q = _key(clean_name(query))
        if not q:
            return []
        if ":" in q and (code := normalize(q)):
            symbol = self.get(code)
            return [symbol] if symbol else []
        q_initials = q if any(ch in _CHOSUNG_SET for ch in q) and not any("가" <= ch <= "힣" for ch in q) else ""
        scored = []
        exact_yahoo = yahoo_candidate(query)
        dynamic = (self._yahoo_symbol(code, item) for code, item in self._yahoo.items())
        known = (k for c, k in self._known.items() if c not in self._by_code)
        for s in chain(self._items, dynamic, known):
            if s.code == exact_yahoo:
                rank = -1  # An exact Yahoo ticker takes precedence over another provider's alias.
            elif s.code == "INDEX:VIX" and q in _VIX_QUERIES:
                rank = -1  # Bare VIX names select KIS even after the user previously viewed ^VIX.
            elif s.ticker_key == q or q in s.names:
                rank = 0
            elif any(n.startswith(q) for n in s.names):
                rank = 1
            elif s.ticker_key.startswith(q):
                rank = 2
            elif any(q in n for n in s.names):
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
