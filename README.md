# KIS Tradingview

한국투자증권(KIS) Open API 시세로 차트를 띄우고, TradingView처럼 차트 위에 선을 긋는 개인용 웹 도구입니다.

- 차트: [Lightweight Charts](https://github.com/tradingview/lightweight-charts) v5 (Apache-2.0, `web/vendor`에 포함)
- 서버: Python FastAPI (KIS 키를 브라우저에 노출하지 않기 위해 서버를 거칩니다)
- KIS 키가 없으면 **MOCK(가짜 시세)** 로 실행되므로 바로 화면을 확인할 수 있습니다.

## 실행

### Windows

**데스크톱 앱:** `TradingView.exe`를 더블클릭하면 차트가 독립된 창에서 열립니다. Microsoft Edge WebView2 런타임이 필요합니다. **설정 → 한투 API 연결**에서 KIS Developers의 App Key와 App Secret을 입력하고 투자 환경과 시장을 선택하세요. 인증이 끝나면 앱이 키를 암호화해 저장하고 차트를 새로 불러옵니다(아래 [키와 토큰 보관](#키와-토큰-보관)). 프로젝트 폴더에서 실행하면 `data/`에 저장한 선도 이어서 쓸 수 있습니다.

실행 파일을 다시 만들려면 Python 3.10 이상으로 `python -m venv .venv`를 실행한 뒤 PowerShell에서 `./build-desktop.ps1`을 실행합니다.

**브라우저:** `run.bat`을 더블클릭합니다. 처음 실행할 때 `.venv` 가상환경을 만들고 패키지를 설치하며, 이후에는 서버가 켜지고 브라우저가 열립니다.

### macOS / Linux

```bash
./run.sh
```

### 직접 실행

```bash
python -m venv .venv
.venv\Scripts\activate          # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
python -m server.main           # http://127.0.0.1:8000
```

## KIS 키 연결

1. [KIS Developers](https://apiportal.koreainvestment.com)에서 API를 신청하고 App Key와 App Secret을 발급받습니다. 모의투자 키로도 시세를 받을 수 있습니다.
2. Windows에서는 데스크톱 앱의 **설정 → 한투 API 연결**로 키를 넣는 방법을 권합니다. 키가 암호화되어 저장되고, `.env`에 키가 없으면 `run.bat`도 그 키를 씁니다. 다른 운영체제이거나 `.env`로 설정하려면 `.env.example`을 `.env`로 복사하고 값을 채웁니다. 이 경우 키는 `.env`에 평문으로 남습니다.

   ```
   KIS_APP_KEY=발급받은키
   KIS_APP_SECRET=발급받은시크릿
   KIS_ENV=demo        # 실전 키라면 real
   KIS_MARKET=J        # KRX만: J / KRX+NXT 통합: UN
   ```
3. 서버를 다시 실행합니다. 상단 배지가 `MOCK 데이터`에서 `KIS 모의 · J`로 바뀌면 연결된 것입니다.

`.env`와 `data/` 폴더(암호화한 키·토큰, 분봉 캐시, 그린 선)는 `.gitignore`에 들어 있어서 git에 올라가지 않습니다.

## 키와 토큰 보관

- App Key·App Secret(`data/kis_credentials.bin`), 접근토큰(`data/kis_token_*.bin`), 웹소켓 접속키(`data/kis_approval_*.bin`)는 Windows DPAPI로 **현재 Windows 사용자 계정**에 묶어 암호화합니다. 파일을 다른 계정이나 다른 PC로 복사하면 풀리지 않으므로, 옮긴 뒤에는 키를 다시 연결해야 합니다.
- 예전 버전이 `.env`에 평문으로 저장한 키는 데스크톱 앱을 처음 켤 때 암호화 저장소로 옮기고 `.env`에서는 값을 지웁니다. 평문 토큰 캐시(`data/kis_token_*.json`, `data/kis_approval_*.json`)는 서버를 켤 때 지웁니다.
- Windows가 아닌 운영체제에서는 토큰을 디스크에 남기지 않고 메모리에만 둡니다. 그래서 서버를 다시 켜면 토큰을 새로 발급받습니다.

## 실시간 연결 보안

- **KIS ↔ 앱:** KIS 실시간 서버(`ops.koreainvestment.com:21000/31000`)는 `wss://`(TLS)를 지원하지 않습니다(두 포트 모두 TLS 핸드셰이크에 응답하지 않음을 확인했습니다). 그래서 이 구간의 시세와 웹소켓 접속키는 암호화되지 않습니다. 접속키는 실시간 시세 구독에만 쓰이고 주문이나 REST 조회에는 쓸 수 없습니다. 앱은 받은 메시지를 그대로 믿지 않고 TR·종목·가격·시각·건수·크기를 검사해 맞지 않는 틱은 버립니다. 재접속할 때는 대기 시간을 무작위로 흩뜨리고, 접속키 강제 재발급은 1분에 한 번으로 제한합니다. REST(토큰·차트)는 HTTPS 인증서를 검증하고 리다이렉트를 따라가지 않습니다.
- **앱 내부(창 ↔ 로컬 서버):** 로컬 서버는 다른 웹사이트가 보낸 요청(Origin·Sec-Fetch-Site 검사)과 DNS 리바인딩(Host 검사)을 거부합니다. 데스크톱 앱은 실행할 때마다 새 일회용 토큰으로 앱 창에만 세션 쿠키(HttpOnly, SameSite=Strict)를 주므로, 같은 PC의 다른 사용자나 프로그램은 실시간 웹소켓과 API에 붙을 수 없습니다.

## 사용법

### 종목 검색

- 왼쪽 위 검색칸에 **종목명 일부**(`삼성`, `전자`), **초성**(`ㅅㅅㅈㅈ`), **코드 앞자리**(`0059`)를 입력하면 맞는 종목이 바로 아래에 나옵니다. 띄어쓰기와 영문 대소문자는 가리지 않습니다(`kodex200` → KODEX 200).
- `↑`/`↓`로 고르고 `Enter`로 엽니다. 결과가 뜨기 전에 `Enter`를 누르면 첫 번째 결과가 열리고, 6자리 코드는 그대로 열립니다. `Esc`는 닫기입니다.
- 순서: 이름이 그대로 같은 종목 → 이름이 그 글자로 시작하는 종목 → 코드 → 이름 중간에 들어 있는 종목 → 초성. 같은 수준에서는 전에 찾아본 종목, 주요 종목, 주식(ETF·ETN보다 먼저), 시가총액이 큰 종목 순서입니다.
- 종목 목록은 한국투자증권이 공개하는 종목 마스터(KOSPI·KOSDAQ, 키 불필요)를 처음 실행할 때 받아 `data/symbols.json`에 저장하고 12시간마다 새로 받습니다. 받지 못하면 주요 종목과 검색 기록에 있는 종목만 이름으로 찾을 수 있고, 코드로는 언제든 열 수 있습니다. 내려받지 않으려면 `.env`에 `SYMBOL_DOWNLOAD=off`를 넣으세요.

### 검색 기록

- 검색해서 연 종목은 `data/search_history.json`에 최근 20개까지 저장되어, 앱을 다시 켜도 남습니다. 데스크톱 앱과 `run.bat`이 같은 기록을 씁니다.
- 검색칸을 누르면 **최근 검색**이 나옵니다. 항목 오른쪽 `×`로 하나씩, **전체 삭제**로 한꺼번에 지울 수 있습니다.

### 선긋기 도구

| 도구 | 단축키 | 점 개수 |
|---|---|---|
| 선택 / 이동 | V | |
| 추세선 | T | 2 |
| 연장선(오른쪽으로 무한) | R | 2 |
| 수평선 | H | 1 |
| 피보나치 되돌림 | F | 2 (시작점 = 1, 끝점 = 0) |
| 평행 채널 | C | 3 (기준선 2점 + 폭 1점) |
| 엘리엇 충격파 | E | 6 (0, 1, 2, 3, 4, 5) |
| 엘리엇 조정파 | A | 4 (0, A, B, C) |

- 도구를 고른 뒤 차트를 클릭해 점을 찍습니다. 점을 다 찍으면 자동으로 선택 도구로 돌아갑니다.
- 선을 클릭하면 선택되고, 끌면 통째로 이동합니다. 선택한 선의 동그란 점을 끌면 그 점만 움직입니다.
- `Delete` 삭제, `Ctrl+Z` 되돌리기, `Esc` 그리기 취소입니다.
- **자석**을 켜 두면 점이 가장 가까운 시가·고가·저가·종가에 붙습니다.
- 충격파는 기본 규칙(2파가 1파 시작점을 넘지 않음, 3파가 가장 짧지 않음, 4파가 1파 영역과 겹치지 않음)을 검사해서 어긋나면 경고를 표시합니다.
- 선은 (시각, 가격)으로 저장되므로 타임프레임을 바꿔도 같은 자리에 남습니다. 종목별로 `data/drawings/<종목코드>.json`에 자동 저장됩니다.

## 다른 컴퓨터에서 쓰기

```bash
git clone <이 저장소 주소> KIS_Tradingview
cd KIS_Tradingview
copy .env.example .env      # 키 입력 (macOS/Linux: cp)
run.bat                     # macOS/Linux: ./run.sh
```

그린 선까지 컴퓨터 사이에서 공유하고 싶다면 `.gitignore`에 `!data/drawings/` 한 줄을 추가하고 커밋하면 됩니다.

## 구조

```
server/
  main.py          FastAPI 앱: /api/candles, /api/drawings, /api/symbols, /api/history, /ws/ticks, 정적 파일
  symbols.py       종목 마스터 내려받기·캐시, 종목명·초성·코드 검색
  history.py       검색 기록 저장 (data/search_history.json)
  kis_source.py    KIS REST(일·주·월봉, 당일·과거 분봉) + WebSocket 실시간 체결
  mock_source.py   키 없이 확인하기 위한 가짜 시세 (24시간 생성)
  bars.py          KST 시간 처리, 타임프레임 집계
  hub.py           실시간 틱을 여러 브라우저 연결에 나눠 주는 허브
  config.py        .env 설정, 암호화 저장소의 연결 정보
  vault.py         Windows DPAPI 암호화 저장 (키·토큰)
  guard.py         로컬 서버 접근 제한 (Host·Origin 검사, 데스크톱 세션 쿠키)
web/
  index.html, css/style.css
  js/app.js        차트, 데이터 로드, 실시간 갱신, 툴바, 종목 검색·검색 기록
  js/drawings.js   선긋기 도구 (Lightweight Charts series primitive)
  js/timemap.js    시각 <-> 봉 번호 변환 (미래 영역 포함)
  js/timeframe.js  타임프레임 규칙
```

### 사용하는 KIS API

| 용도 | TR | 1회 최대 |
|---|---|---|
| 일·주·월봉 | FHKST03010100 `inquire-daily-itemchartprice` | 100건 |
| 당일 분봉 | FHKST03010200 `inquire-time-itemchartprice` | 30건 |
| 과거 분봉 (최대 1년) | FHKST03010230 `inquire-time-dailychartprice` | 120건 |
| 실시간 체결 | H0STCNT0 / H0UNCNT0 / H0NXCNT0 (WebSocket) | 세션당 약 40종목 |

- 접근토큰은 하루 동안 유효하고 1분에 1회만 발급되므로 `data/kis_token_*.bin`에 암호화해 캐시합니다.
- 초당 호출 제한(실전 20건, 모의 5건)을 넘지 않도록 서버가 호출 간격을 조절합니다.
- 지난 날짜의 분봉은 `data/cache/`에 저장해 두므로, 같은 종목은 두 번째부터 빠르게 열립니다.
- 3·5·15·30·60분봉은 1분봉을 서버에서 묶어서 만듭니다. 분봉의 기본 조회 기간은 `server/main.py`의 `DEFAULT_DAYS`에서 바꿀 수 있습니다.

## 알려진 제한

- KIS 연동 코드는 공식 예제(koreainvestment/open-trading-api)의 파라미터를 기준으로 작성했고, KIS 응답 형식을 흉내 낸 가짜 서버로 검증했습니다. 실제 키로는 아직 확인하지 않았으므로 첫 실행 때 서버 로그를 확인해 주세요.
- 미래 영역에 찍은 분봉 점은 장 마감과 휴장 시간을 고려하지 않고 연속 시간으로 계산합니다. 일봉은 주말을 건너뜁니다.
- 지표(이동평균, RSI 등)는 아직 없습니다.
- 종목 마스터 해석은 공식 예제의 파일 형식을 기준으로 작성했고, 그 형식으로 만든 테스트 데이터로 검증했습니다. 실제 파일로는 아직 확인하지 않았으므로, 검색 결과가 이상하면 `data/symbols.json`을 지우고 다시 실행한 뒤 로그를 확인해 주세요.
- KONEX 종목과 ELW는 검색 목록에 없습니다(코드로는 열 수 있습니다).
