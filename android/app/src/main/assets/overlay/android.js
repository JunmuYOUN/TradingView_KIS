// 데스크톱 화면(web/)을 고치지 않고 안드로이드에서 쓰기 좋게 보정합니다.
// MainActivity 가 차트 페이지를 열 때마다 페이지 스크립트보다 먼저 넣습니다.
//
//  1. 좁은 화면용 스타일 (android.css)
//  2. "계정" 버튼: 데스크톱 창의 계정 메뉴(한투 API 로그인, 로그아웃) 자리
//  3. 터치로 선 그리기: 데스크톱의 선긋기 도구는 마우스 이벤트만 받으므로 터치를 마우스로 바꿔 줍니다
//  4. 뒤로 버튼: 열린 창 닫기 → 설정 탭에서 차트로
//  5. 얼러트를 휴대폰 알림(진동)으로 전달, 설정 탭에 진동 켜기/끄기
//
// 데스크톱 화면의 구조가 바뀌어 찾는 요소가 없으면 해당 보정만 조용히 빠집니다.
// 필요한 요소 목록은 tools/check_compat.py 가 빌드 전에 검사합니다.
(() => {
  if (window.__kisAndroid) return;

  const TAP_MOVE = 10;      // 이보다 적게 움직이면 탭으로 봅니다 (px)
  const TAP_TIME = 600;     // 이보다 짧게 눌러야 탭입니다 (ms)
  const FINGER_REACH = 16;  // 손가락이 선에서 이만큼 떨어져도 그 선을 누른 것으로 봅니다 (px)
  const DRAG_POINT = 12;    // 이만큼 끌고 떼면 뗀 자리에 다음 점을 찍습니다 (px)

  const ALERT_STORE = 'kis.localPriceAlerts.v1';      // 데스크톱(chart-actions.js)이 얼러트를 저장하는 곳
  const VIBRATE_KEY = 'kis.android.alertVibrate';     // 설정 → 얼러트 알림 → 진동 (기본 켬)

  // 앱(MainActivity)으로 보내는 메시지. type: account | alert | watch | vibrate | notification-settings
  const send = (type, data = {}) => {
    try { window.KisAndroidBridge.postMessage(JSON.stringify({ type, ...data })); }
    catch (error) { /* 앱 밖(브라우저)에서는 통로가 없습니다 */ }
  };
  const readFlag = (key, fallback) => {
    try { const saved = localStorage.getItem(key); return saved === null ? fallback : saved === 'true'; }
    catch (error) { return fallback; }
  };
  const vibrateOn = () => readFlag(VIBRATE_KEY, true);

  window.__kisAndroid = {
    // 화면이 뒤로 버튼을 처리했으면 true. false 면 앱이 뒤로 물러납니다.
    back() {
      const dialogs = document.querySelectorAll('dialog[open]');
      if (dialogs.length) { dialogs[dialogs.length - 1].close(); return true; }
      const popup = document.querySelector('#suggest:not([hidden]), #tool-menu:not([hidden]), details[open]');
      if (popup) {
        if (popup.tagName === 'DETAILS') popup.open = false;
        else { popup.hidden = true; document.activeElement?.blur(); }
        return true;
      }
      const chartTab = document.getElementById('chart-tab');
      if (chartTab && chartTab.getAttribute('aria-selected') === 'false') { chartTab.click(); return true; }
      return false;
    },
  };

  const whenReady = (run) => {
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run, { once: true });
    else run();
  };

  whenReady(() => {
    document.documentElement.classList.add('kis-android');
    const style = document.createElement('style');
    style.id = 'kis-android-style';
    style.textContent = window.__kisAndroidCss || '';
    document.head.appendChild(style);
    addAccountButton();
    bindChartTouch();
    bindAlerts();
    addAlertSettings();
  });

  // ------------------------------------------------------------ 계정 버튼
  function addAccountButton() {
    const button = document.createElement('button');
    button.id = 'kis-account';
    button.type = 'button';
    button.textContent = '계정';
    button.addEventListener('click', () => send('account'));
    const tabs = document.getElementById('view-tabs');
    if (tabs) tabs.appendChild(button);
    else { button.classList.add('floating'); document.body.appendChild(button); }
  }

  // ------------------------------------------------------------ 얼러트 → 휴대폰 알림
  // 데스크톱의 얼러트는 조건이 맞으면 화면 안의 #alert-notice 에 문구를 띄웁니다. 그 문구를 앱으로 보내
  // 시스템 알림(진동)으로도 알립니다. 얼러트 판정 자체는 데스크톱 코드가 그대로 합니다.
  function bindAlerts() {
    const notice = document.getElementById('alert-notice');
    if (!notice) return;
    // 문구가 바뀌는 것을 지켜보는 방식(MutationObserver)은 쓰지 않습니다. 같은 얼러트가 다시 울리면 문구가
    // 똑같아서 브라우저가 아무 변화도 알리지 않기 때문입니다. 대신 문구를 쓰는 순간을 직접 받습니다.
    const text = Object.getOwnPropertyDescriptor(Node.prototype, 'textContent');
    Object.defineProperty(notice, 'textContent', {
      configurable: true,
      get() { return text.get.call(this); },
      set(value) {
        text.set.call(this, value);
        const message = String(value ?? '').trim();
        if (message) send('alert', { text: message, vibrate: vibrateOn() });
      },
    });

    // 지금 열어 둔 종목에 걸린 얼러트 수를 앱에 알립니다. 하나라도 있으면 앱이 화면에서 벗어나도
    // 시세 갱신과 얼러트 감시를 계속합니다 (없으면 배터리를 아끼려고 멈춥니다).
    let reported = -1;
    const watched = () => {
      let rules;
      try { rules = JSON.parse(localStorage.getItem(ALERT_STORE) || '[]'); } catch (error) { return 0; }
      if (!Array.isArray(rules)) return 0;
      const code = window.__app?.state?.code;
      return rules.filter((rule) => rule && typeof rule.key === 'string' && (!code || rule.key.endsWith('/' + code))).length;
    };
    const report = () => {
      const count = watched();
      if (count !== reported) { reported = count; send('watch', { count }); }
    };
    report();
    setInterval(report, 2000);
  }

  // ------------------------------------------------------------ 설정 → 얼러트 알림
  function addAlertSettings() {
    const view = document.getElementById('settings-view');
    if (!view) return;
    const section = document.createElement('section');
    section.id = 'kis-alert-settings';
    section.innerHTML = `
      <h2>얼러트 알림</h2>
      <label class="kis-toggle"><input id="kis-alert-vibrate" type="checkbox"> 진동</label>
      <p class="kis-note">얼러트가 울리면 휴대폰 알림으로 알려 줍니다. 진동을 끄면 알림만 표시합니다.
        얼러트를 걸어 둔 종목을 열어 두면 앱이 화면에 없어도 계속 감시합니다.</p>
      <button id="kis-notification-settings" type="button">휴대폰 알림 설정 열기</button>`;
    const license = document.getElementById('show-license');
    if (license && license.parentNode === view) view.insertBefore(section, license);
    else view.appendChild(section);

    const vibrate = section.querySelector('#kis-alert-vibrate');
    vibrate.checked = vibrateOn();
    vibrate.addEventListener('change', () => {
      try { localStorage.setItem(VIBRATE_KEY, String(vibrate.checked)); } catch (error) { /* 이번 실행에서만 유지 */ }
      send('vibrate', { on: vibrate.checked });  // 켰을 때 짧게 한 번 울려 확인시켜 줍니다
    });
    section.querySelector('#kis-notification-settings').addEventListener('click', () => send('notification-settings'));
  }

  // ------------------------------------------------------------ 터치로 선 그리기
  // 선긋기 도구(drawings.js)는 #chart 에서 mousedown / mousemove / mouseup 을 받습니다.
  //   - 도구를 골랐을 때: 손가락을 댄 곳에 점을 찍고, 끌면 미리보기, 끌다 떼면 그 자리에 다음 점
  //   - 선택한 선: 끌어서 이동 (선택하지 않은 선은 끌어도 차트가 움직입니다. 실수로 선을 옮기지 않게)
  //   - 탭: 선 선택 / 빈 곳은 선택 해제
  // 그 밖의 터치(차트 이동, 두 손가락 확대)는 차트 라이브러리가 그대로 처리합니다.
  function bindChartTouch() {
    const chart = document.getElementById('chart');
    if (!chart) return;
    const manager = () => window.__app?.drawings || null;

    let held = null;  // 마우스로 바꿔 전달 중인 터치 { id, dx, dy, startX, startY }
    let tap = null;   // 차트에 맡긴 터치. 탭으로 끝나면 선택 처리에 씁니다 { id, x, y, time }

    const mouse = (type, x, y, { bubbles = true } = {}) => {
      const event = new MouseEvent(type, {
        bubbles, cancelable: true, view: window, button: 0, buttons: type === 'mouseup' ? 0 : 1,
        clientX: x, clientY: y,
      });
      chart.dispatchEvent(event);
      return event;
    };
    // mousedown 은 #chart 에서 멈춥니다. 위로 올려 보내면 차트 라이브러리 바깥의 다른 처리와 겹칩니다.
    const press = (x, y) => mouse('mousedown', x, y, { bubbles: false });

    // 손가락 주변에서 가장 가까운 선을 찾습니다. 데스크톱의 판정 범위(6px)는 손가락에는 너무 좁습니다.
    const reach = (dm, clientX, clientY) => {
      if (typeof dm.hitTest !== 'function') return null;
      const rect = chart.getBoundingClientRect();
      const x = clientX - rect.left, y = clientY - rect.top;
      for (const radius of [0, 6, 11, FINGER_REACH]) {
        const steps = radius ? 8 : 1;
        for (let i = 0; i < steps; i++) {
          const angle = (i / steps) * 2 * Math.PI;
          const dx = Math.round(radius * Math.cos(angle)), dy = Math.round(radius * Math.sin(angle));
          const hit = dm.hitTest(x + dx, y + dy);
          if (hit) return { hit, dx, dy };
        }
      }
      return null;
    };

    const find = (touches, id) => Array.prototype.find.call(touches, (t) => t.identifier === id);
    const swallow = (event) => { event.preventDefault(); event.stopPropagation(); };

    chart.addEventListener('touchstart', (event) => {
      if (held) { swallow(event); return; }  // 선을 다루는 중에 둘째 손가락은 무시합니다
      tap = null;
      if (event.touches.length !== 1) return;
      const touch = event.changedTouches[0];
      const dm = manager();
      let dx = 0, dy = 0;

      if (dm && dm.tool === 'cursor') {
        const near = reach(dm, touch.clientX, touch.clientY);
        if (!near || near.hit.id !== dm.selectedId) {
          // 선택한 선이 아니면 차트가 움직이게 두고, 탭으로 끝나면 touchend 에서 선택을 처리합니다.
          tap = { id: touch.identifier, x: touch.clientX, y: touch.clientY, time: event.timeStamp };
          return;
        }
        ({ dx, dy } = near);
      }
      // 도구를 골랐거나(점 찍기) 선택한 선을 잡았으면 선긋기 도구가 이벤트를 가져갑니다.
      // (선긋기 도구를 찾지 못한 경우에도 한 번 물어보고, 받지 않으면 차트에 맡깁니다.)
      const down = press(touch.clientX + dx, touch.clientY + dy);
      if (!down.defaultPrevented) return;
      held = { id: touch.identifier, dx, dy, startX: touch.clientX, startY: touch.clientY };
      swallow(event);
    }, { capture: true, passive: false });

    chart.addEventListener('touchmove', (event) => {
      if (!held) return;
      swallow(event);
      const touch = find(event.changedTouches, held.id);
      if (touch) mouse('mousemove', touch.clientX + held.dx, touch.clientY + held.dy);
    }, { capture: true, passive: false });

    const release = (event) => {
      if (held) {
        const touch = find(event.changedTouches, held.id);
        if (!touch) { swallow(event); return; }
        const { dx, dy, startX, startY } = held;
        held = null;
        swallow(event);
        const x = touch.clientX + dx, y = touch.clientY + dy;
        mouse('mouseup', x, y);
        // 점을 찍고 끌다가 뗐으면 뗀 자리에 다음 점을 찍습니다 (추세선을 한 번에 긋기).
        const dm = manager();
        const dragged = Math.hypot(touch.clientX - startX, touch.clientY - startY) >= DRAG_POINT;
        if (event.type === 'touchend' && dragged && dm && dm.draft && !dm.brushing) {
          press(x, y);
          mouse('mouseup', x, y);
        }
        return;
      }
      if (!tap) return;
      const touch = find(event.changedTouches, tap.id);
      if (!touch) return;
      const { x, y, time } = tap;
      tap = null;
      const still = Math.hypot(touch.clientX - x, touch.clientY - y) < TAP_MOVE;
      if (event.type !== 'touchend' || !still || event.timeStamp - time > TAP_TIME) return;
      // 탭: 가까운 선을 선택하거나, 빈 곳이면 선택을 풉니다. 브라우저가 뒤이어 만드는 마우스 이벤트는 막습니다.
      const dm = manager();
      const near = dm ? reach(dm, touch.clientX, touch.clientY) : null;
      const px = touch.clientX + (near ? near.dx : 0), py = touch.clientY + (near ? near.dy : 0);
      event.preventDefault();
      press(px, py);
      mouse('mouseup', px, py);
    };
    chart.addEventListener('touchend', release, { capture: true, passive: false });
    chart.addEventListener('touchcancel', release, { capture: true, passive: false });
  }
})();
