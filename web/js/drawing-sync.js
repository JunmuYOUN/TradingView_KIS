// Pairing controls call only the session-protected API inside this device.
export function setupDrawingSync(role, api, refreshDrawing) {
  const el = id => document.getElementById(id);
  const phone = role === 'phone';
  const form = el('sync-pair');
  const controls = el('drawing-sync');
  const post = (action, data = {}) => api('/api/drawing-sync/' + action, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data),
  });
  let current, reading = false, working = false, generation = 0, expires = 0;
  controls.hidden = false;

  function hideInvitation() {
    expires = 0;
    el('sync-invitation').hidden = true;
    el('sync-code').textContent = '';
    el('sync-qr').width = 0;
  }

  function render(state) {
    current = state;
    if (state.paired || Date.now() >= expires) hideInvitation();
    form.hidden = !phone || state.paired;
    el('sync-invite').hidden = phone || state.paired;
    el('sync-unlink').hidden = !state.paired;
    const last = state.last_sync ? new Date(state.last_sync * 1000).toLocaleTimeString('ko-KR') : '';
    el('sync-status').textContent = state.error || (state.paired
      ? (last ? '마지막 동기화 · ' + last : '등록됨 · 첫 동기화 대기 중')
      : '같은 와이파이의 PC와 휴대폰을 연결하면 그림이 자동으로 동기화됩니다.');
    if (state.paired) form.reset();
  }

  async function check() {
    if (reading || working || document.hidden) return;
    reading = true;
    try {
      const state = await api('/api/drawing-sync', { cache: 'no-store' });
      if (working) return;
      render(state);
      if (generation !== state.generation && await refreshDrawing()) generation = state.generation;
    } catch {
      if (!working) el('sync-status').textContent = '동기화 상태를 확인하지 못했습니다.';
    } finally { reading = false; }
  }

  async function action(work) {
    if (working) return;
    working = true;
    for (const button of controls.querySelectorAll('button')) button.disabled = true;
    el('sync-error').textContent = '';
    try { await work(); }
    catch (error) { el('sync-error').textContent = error.message; }
    finally {
      working = false;
      for (const button of controls.querySelectorAll('button')) button.disabled = false;
      await check();
    }
  }

  el('sync-invite').onclick = () => action(async () => {
    const invite = await post('invite');
    expires = Date.now() + invite.expires_in * 1000;
    const canvas = el('sync-qr'), matrix = invite.qr;
    canvas.width = canvas.height = matrix.length * 6;
    const ctx = canvas.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
    ctx.fillStyle = '#000';
    matrix.forEach((row, y) => row.forEach((on, x) => {
      if (on) ctx.fillRect(x * 6, y * 6, 6, 6);
    }));
    el('sync-code').textContent = 'PC 주소 ' + invite.address + ' · 연결 코드 ' + invite.code + ' (5분간 유효)';
    el('sync-invitation').hidden = false;
  });
  const pair = (address, code) => action(async () => {
    el('sync-status').textContent = 'PC에 연결하는 중…';
    render(await post('pair', { address, code }));
  });
  form.onsubmit = event => {
    event.preventDefault();
    pair(form.elements.address.value, form.elements.code.value);
  };
  el('sync-scan').onclick = () => {
    if (window.KisAndroidBridge) {
      window.KisAndroidBridge.postMessage(JSON.stringify({ type: 'scan-drawing-link' }));
    } else el('sync-manual').open = true;
  };
  window.addEventListener('drawing-sync-scan', event => {
    if (!phone || current?.paired) return;
    const parts = /^KIS-DRAW-1 ([0-9.]{7,15}) ([A-Z2-7-]{19})$/.exec(event.detail);
    if (parts) pair(parts[1], parts[2]);
    else el('sync-error').textContent = 'PC의 설정에서 발급한 그림 동기화 QR을 스캔하세요.';
  });
  el('sync-unlink').onclick = () => {
    if (confirm('이 기기의 연결을 해제할까요? 이미 저장된 그림은 유지됩니다.')) {
      action(async () => render(await post('unlink')));
    }
  };
  el('settings-tab').addEventListener('click', check);
  document.addEventListener('visibilitychange', check);
  window.setInterval(() => {
    if (current?.paired || expires || !current || !el('settings-view').hidden) check();
  }, 3000);
  check();
}
