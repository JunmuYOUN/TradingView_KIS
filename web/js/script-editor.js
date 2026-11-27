import { savePersonalScript } from './script-store.js';

const DEFAULT_EXPRESSION = 'plot(ta.sma(close, 20))';

export function createScriptEditor(onSave) {
  const dialog = document.createElement('dialog');
  dialog.id = 'personal-script-editor';
  dialog.setAttribute('aria-labelledby', 'personal-script-title');
  // Only fixed UI markup is inserted here. Script names and expressions use form values and textContent.
  dialog.innerHTML = `<form>
    <header><h2 id="personal-script-title"></h2><button class="ps-close" type="button" aria-label="닫기">×</button></header>
    <label for="ps-name">이름</label><input type="text" id="ps-name" maxlength="64" required autocomplete="off" placeholder="내 이동평균선">
    <label for="ps-expression">지표 수식</label>
    <textarea id="ps-expression" required maxlength="512" spellcheck="false" autocapitalize="off" autocomplete="off" aria-describedby="ps-help"></textarea>
    <details id="ps-help"><summary>지원 문법</summary>
      <p>한 개의 <code>plot(수식)</code>으로 선을 그립니다. 시가·고가·저가·종가·거래량에는
      <code>open</code>, <code>high</code>, <code>low</code>, <code>close</code>, <code>volume</code>을 사용하세요.</p>
      <p><code>hl2</code>, <code>hlc3</code>, <code>ohlc4</code>, 숫자, 사칙연산, 괄호와
      <code>ta.sma(값, 기간)</code>, <code>ta.ema(값, 기간)</code>, <code>ta.rsi(값, 기간)</code>를 사용할 수 있습니다. 기간은 1~500 사이 정수입니다.</p>
      <p>예시 <code>plot(ta.rsi(close, 14))</code></p>
    </details>
    <div class="ps-options">
      <label>선 색 <input id="ps-color" type="color"></label>
      <label><input id="ps-overlay" type="checkbox"> 가격 차트 위에 표시</label>
    </div>
    <p class="ps-status" role="status"></p>
    <footer><button class="study-alert" type="button" hidden>얼러트</button><button class="ps-cancel" type="button">취소</button><button class="ps-save" type="submit">이 기기에 저장</button></footer>
  </form>`;
  document.body.append(dialog);
  const form = dialog.querySelector('form');
  const name = dialog.querySelector('#ps-name');
  const expression = dialog.querySelector('#ps-expression');
  const color = dialog.querySelector('#ps-color');
  const overlay = dialog.querySelector('#ps-overlay');
  const status = dialog.querySelector('.ps-status');
  let editingId = null;

  function open(script, openAlert) {
    if (dialog.open) return;
    editingId = script?.id || null;
    const alert = dialog.querySelector('.study-alert');
    alert.hidden = !openAlert;
    alert.onclick = openAlert || null;
    dialog.querySelector('h2').textContent = script ? '스크립트 수정' : '새 스크립트';
    dialog.querySelector('#ps-help').open = false;
    name.value = script?.name || '';
    expression.value = script?.expression || DEFAULT_EXPRESSION;
    color.value = script?.color || '#5792ff';
    overlay.checked = script?.overlay ?? true;
    status.textContent = '';
    dialog.showModal();
    name.focus();
  }
  dialog.querySelector('.ps-close').onclick = dialog.querySelector('.ps-cancel').onclick = () => dialog.close();
  dialog.addEventListener('keydown', event => {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      form.requestSubmit();
    }
  });
  form.onsubmit = event => {
    event.preventDefault();
    let script;
    try {
      script = savePersonalScript(editingId, { name: name.value, expression: expression.value,
        color: color.value, overlay: overlay.checked });
    } catch (error) {
      status.textContent = error.message || '스크립트를 저장할 수 없습니다.';
      return;
    }
    dialog.close();
    onSave(script);
  };
  return { open };
}
