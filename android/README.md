# KIS TradingView 안드로이드 앱

상위 폴더에 있는 데스크톱 앱과 같은 차트 서버와 화면을 안드로이드 기기 안에서 실행합니다.

데스크톱의 `server/`(Python FastAPI)와 `web/`(차트 화면)을 다시 만들지 않고, 빌드할 때마다 데스크톱 프로젝트에서 그대로 가져와 APK에 넣습니다. 그래서 데스크톱 앱을 고치면 다음 빌드에서 안드로이드 앱도 똑같이 바뀝니다.

| | 데스크톱 | 안드로이드 |
|---|---|---|
| 차트 서버 | `server/` (PC의 Python) | **같은 `server/`** (앱에 넣은 Python 3.13, [Chaquopy](https://chaquo.com/chaquopy/)) |
| 화면 | `web/` (WebView2 창) | **같은 `web/`** (Android WebView) + 모바일 보정 |
| 로그인·로그아웃 로직 | `desktop_login.py` | **같은 `desktop_login.py`** |
| 로그인 창, 계정 메뉴 | Windows 대화상자, 창 메뉴 | 안드로이드 대화상자, 화면 오른쪽 위 **계정** 버튼 |
| 키·토큰 암호화 | Windows DPAPI | Android Keystore (AES-256-GCM) |
| 저장 위치 | 실행 파일 옆 `data/` | 앱 전용 저장소 |

## 데스크톱을 고친 뒤 안드로이드에 반영하기

```powershell
.\build-android.ps1
```

이 한 줄이 아래를 차례로 합니다.

1. **호환성 검사** — 데스크톱 코드를 안드로이드와 같은 Python 버전·패키지로 PC에서 실행해 봅니다(`tools/check_compat.py`). 안드로이드에서 깨질 변경이면 여기서 멈추고 무엇이 문제인지 알려 줍니다.
2. **빌드** — 데스크톱의 `server/`, `web/`, `desktop_login.py`를 가져와 APK를 만듭니다.
3. **결과물** — 이 폴더에 `TradingView.apk`가 생깁니다.
4. **설치** — USB 디버깅으로 연결된 기기가 하나 있으면 기존 앱 위에 바로 설치합니다(데이터 유지). 없으면 APK 파일을 폰으로 옮겨 설치하세요.

데스크톱의 `build-desktop.ps1`도 `TradingView.exe`를 만든 뒤 이 스크립트를 이어서 실행합니다. 데스크톱만 빌드하려면 `build-desktop.ps1 -SkipAndroid`를 쓰세요.

옵션: `-NoInstall`(설치 안 함), `-SkipCheck`(호환성 검사 생략), `-DeviceTest`(빌드 전에 기기 테스트 실행, 아래 참고), `-DebugBuild`(PC의 `chrome://inspect`에서 화면을 들여다볼 수 있는 디버그 APK. 정식 앱과 별개의 앱 `com.kistradingview.app.debug`로 설치되고 데이터도 따로입니다).

## 가상 기기(에뮬레이터)에서 시험하기

```powershell
.\tools\start-emulator.ps1          # 가상 폰을 켜고 부팅을 기다립니다 (-ShowWindow 로 창 표시, -Stop 으로 종료)
.\build-android.ps1 -DeviceTest     # 기기 테스트 → 빌드 → 가상 폰에 설치
```

- **기기 테스트**(`app/src/androidTest`)는 PC의 호환성 검사가 볼 수 없는 부분을 실제 안드로이드에서 확인합니다: Android Keystore 암호화와 변조 거부, 앱 안의 Python 서버 시작, 데스크톱 화면 제공, 세션 쿠키 접근 제한, Python `vault`가 Keystore로 저장하는지, 얼러트가 진동 설정에 맞는 채널의 알림으로 나가는지. 디버그 앱에서 돌기 때문에 폰에 설치된 정식 앱과 그 데이터는 건드리지 않습니다. USB로 연결한 실제 폰에서도 돌릴 수 있습니다.
- 에뮬레이터는 **하드웨어 가속이 있어야** 돕니다. Hyper-V를 쓰지 않는 PC에서는 Android Emulator Hypervisor Driver를 관리자 권한으로 한 번 설치해야 합니다(`tools/start-emulator.ps1` 맨 위에 방법이 있습니다). 제거는 같은 폴더의 `silent_install.bat -u`입니다.
- 화면을 보려면 `-ShowWindow`로 켜거나 `adb exec-out screencap -p > shot.png`로 캡처합니다. 로그인 창은 보안상 캡처에 검게 나옵니다.

앱의 **계정 → 앱 정보**에서 빌드 시각과, 어느 데스크톱 커밋으로 만들었는지 볼 수 있습니다(커밋하지 않은 변경이 들어 있으면 `+local`).

## 처음 한 번: 빌드 도구 설치

```powershell
.\tools\setup-toolchain.ps1 -AcceptLicenses
```

JDK 17, Android SDK, 빌드용 Python 3.13을 `C:\Users\<사용자>\AndroidDev`에 설치하고 그 위치를 `local.properties`에 적습니다. 관리자 권한이 필요 없고 시스템 PATH·환경 변수를 바꾸지 않습니다. Gradle·Android 캐시도 그 폴더 안에 두므로, 폴더를 지우면 전부 사라집니다. `-AcceptLicenses`는 [Android SDK 라이선스](https://developer.android.com/studio/terms)에 동의한다는 뜻입니다.

기존 설치에서 쓰던 SDK와 APK 서명을 사용하려면 `build-android.ps1 -ConfigDirectory <기존 Android 프로젝트 폴더>`로 빌드하세요. 기존 두 폴더가 원래 위치에 있으면 자동으로 인식합니다.

## 서명 키

`build-android.ps1`을 처음 실행하면 `signing\release.jks`와 그 비밀번호(`signing\signing.properties`)를 만듭니다. 안드로이드는 **같은 키로 서명한 APK만 기존 앱 위에 설치**해 주므로, 이 폴더를 잃어버리면 앱을 지우고 다시 설치해야 하고 그때 저장한 한투 키와 그린 선이 지워집니다. **`signing` 폴더를 백업해 두세요.** git에는 올라가지 않습니다.

## 데스크톱과 다른 점

- **데이터는 기기마다 따로입니다.** 한투 키, 그린 선, 검색 기록, 즐겨찾기는 PC와 폰이 공유하지 않습니다. 폰에서도 **계정 → 한투 API 로그인**으로 키를 한 번 입력해야 합니다.
- **터치 조작.** 도구를 고른 뒤 차트를 누르면 점이 찍히고, 누른 채 끌다 떼면 뗀 자리에 다음 점이 찍힙니다(추세선은 한 번 끌어서 긋기). 선을 탭하면 선택되고, **선택한 선만** 끌어서 옮길 수 있습니다. 선택하지 않은 선 위를 끌면 차트가 움직입니다.
- **키보드 단축키**(V, T, Delete, Ctrl+Z 등)는 쓸 수 없습니다. 삭제는 선을 선택하면 나오는 **삭제** 버튼으로 합니다.
- **얼러트는 휴대폰 알림(진동)으로도 옵니다.** 아래 "얼러트 알림"을 보세요.
- 감시할 얼러트가 없으면, 앱이 화면에 보이지 않는 동안 시세 갱신을 멈춥니다.

## 얼러트 알림

데스크톱에서는 얼러트가 울리면 앱 화면 안에 문구가 뜹니다. 안드로이드에서는 같은 문구를 **휴대폰 알림**으로도 보내고 진동으로 알립니다. 얼러트를 만들고 판정하는 것은 데스크톱 코드 그대로입니다.

- **진동 켜기/끄기**: 앱의 **설정 → 얼러트 알림 → 진동**. 끄면 알림만 표시합니다. 소리와 방해 금지 모드는 휴대폰의 알림 설정을 따르며, 같은 화면의 **휴대폰 알림 설정 열기**로 바로 갈 수 있습니다.
- **알림 권한**: 얼러트를 처음 만들 때 한 번 묻습니다. 거절하면 얼러트는 앱 화면 안에만 표시됩니다.
- **백그라운드 감시**: 얼러트를 걸어 둔 종목을 열어 둔 채 다른 앱으로 가거나 화면을 꺼도 감시를 계속하고, 그동안 "얼러트 n개 감시 중" 알림이 떠 있습니다. 얼러트가 다 울려서 없어지거나 앱으로 돌아오면 그 알림은 사라집니다.
- **한계**
  - 데스크톱과 마찬가지로 **지금 열어 둔 종목**의 얼러트만 감시합니다. 다른 종목에 걸어 둔 얼러트는 그 종목을 열어야 작동합니다.
  - 최근 앱 목록에서 앱을 밀어 닫으면 감시도 끝납니다.
  - 감시하는 동안에는 5초마다 시세를 조회하므로 배터리와 데이터를 씁니다.
  - 휴대폰을 오래 가만히 두면 안드로이드의 절전(Doze)이 네트워크를 막아 알림이 늦어질 수 있습니다. 제조사의 배터리 최적화가 앱을 종료시키는 기기에서는 이 앱을 최적화 대상에서 빼야 합니다.
- Python 패키지 버전이 데스크톱과 다릅니다(`requirements-android.txt`에 이유가 적혀 있습니다). 그 차이 때문에 생길 수 있는 문제는 호환성 검사가 잡습니다.

## 구조

```
build-android.ps1            데스크톱 → 안드로이드 파이프라인 (검사, 빌드, 설치)
requirements-android.txt     앱에 넣는 Python 패키지 (전부 순수 Python)
tools/
  setup-toolchain.ps1        빌드 도구 설치
  start-emulator.ps1         시험용 가상 폰 켜기·끄기
  check_compat.py            데스크톱 코드가 안드로이드 구성에서 도는지 PC에서 검사
app/
  build.gradle.kts           syncDesktop 작업이 데스크톱의 server/, web/, desktop_login.py 를 가져옵니다
  src/main/
    python/android_app/
      bootstrap.py           데스크톱 서버를 앱 저장소·Keystore 에 연결해 실행 (데스크톱 코드와 맞닿는 유일한 곳)
      keystore_vault.py      server/vault.py 의 DPAPI 자리에 들어가는 Keystore 암호화
    java/.../MainActivity.kt 서버 시작, WebView, 계정·로그인 대화상자
    java/.../ChartServer.kt  Kotlin → Python 호출
    java/.../SecretBox.kt    Android Keystore AES-256-GCM
    java/.../AlertNotifier.kt       얼러트 알림 (진동 채널 / 진동 없는 채널)
    java/.../AlertWatchService.kt   백그라운드에서 얼러트 감시가 끊기지 않게 하는 서비스
    java/.../ChartWebView.kt        감시 중에는 화면이 가려져도 페이지를 멈추지 않는 WebView
    assets/overlay/
      android.css            좁은 화면·손가락에 맞춘 스타일
      android.js             계정 버튼, 터치로 선 그리기, 뒤로 버튼, 얼러트 알림 전달, 진동 설정
  src/androidTest/.../OnDeviceTest.kt   기기 테스트 (Keystore, 서버 시작, 접근 제한)
```

데스크톱의 `server/`와 `web/`는 `app/build/generated/desktop/`에 복사되며 git에 들어가지 않습니다. 이 저장소에는 안드로이드 전용 코드만 있습니다.

### 데스크톱 코드에 기대는 부분

데스크톱을 크게 바꾸면 아래가 어긋날 수 있고, 그때는 호환성 검사가 어느 항목인지 알려 줍니다.

- `server/config.py`의 `APP_DIR`, `DATA_DIR`, `ENV_FILE`, `WEB_DIR`, `CREDENTIALS_FILE` — 저장 위치를 앱 저장소로 돌립니다.
- `server/vault.py`의 `AVAILABLE`, `protect`, `unprotect` — Keystore 암호화로 바꿉니다.
- `server/guard.py`의 `LocalGuard`, `desktop_login.py`의 `load_desktop_settings`, `connect_kis`, `logout_kis`.
- 화면의 `#chart`, `#view-tabs`, `#chart-tab`, `#settings-tab`, `#top`, `#tools`, `#symbol`, 그리고 `window.__app.drawings` — 모바일 보정이 씁니다.
- 얼러트: 울릴 때 `#alert-notice`의 `textContent`에 문구를 쓰는 것, 얼러트를 `localStorage`의 `kis.localPriceAlerts.v1`에 저장하는 것, `window.__app.state.code`(열어 둔 종목) — 알림 전달과 백그라운드 감시가 씁니다. `#settings-view` — 진동 설정을 넣는 곳입니다.
- `server/`와 `desktop_login.py`가 파일 맨 위에서 Windows 전용 모듈을 불러오지 않을 것(함수 안에서 불러오는 것은 괜찮습니다).

### 보안

- 앱 안의 서버는 `127.0.0.1`에서만 듣고, 데스크톱과 같은 `LocalGuard`로 이 앱의 WebView에만 세션 쿠키를 줍니다. 같은 기기의 다른 앱은 API와 웹소켓에 붙을 수 없습니다.
- 암호화하지 않은 HTTP는 `127.0.0.1`에만 허용합니다. 한투 API는 HTTPS로만 호출합니다.
- 한투 키와 토큰은 Android Keystore 키로 암호화해 앱 전용 저장소에 둡니다. 백업과 기기 간 이동에서 제외되고, 로그인 창은 화면 캡처에 남지 않습니다.
- WebView는 앱 안의 차트 주소만 엽니다. 다른 링크는 기기의 브라우저로 넘깁니다.
