import { STUDY_PARAMETERS, studyParameters } from './study-data.js';

const key = 'kis.studySettings.v1';
const validColor = value => typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value);

function savedSettings() {
  try {
    const value = JSON.parse(localStorage.getItem(key) || '{}');
    if (value && typeof value === 'object' && !Array.isArray(value)) return value;
  } catch { /* Restore built-in values when stored settings cannot be read. */ }
  return {};
}

export function getStudySettings(meta) {
  const saved = savedSettings()[meta.id];
  return {
    parameters: studyParameters(meta.id, saved?.parameters),
    colors: meta.lines.map((line, index) => validColor(saved?.colors?.[index]) ? saved.colors[index] : line.color),
    falling: validColor(saved?.falling) ? saved.falling : '#ef5350',
  };
}

// Build only the controls used by this study. The running series is changed after confirmation.
export function editStudySettings(meta, current, commit, openAlert) {
  if (document.getElementById('study-settings')) return;
  const dialog = document.createElement('dialog');
  dialog.id = 'study-settings';
  dialog.setAttribute('aria-labelledby', 'study-settings-title');
  const heading = document.createElement('h2');
  heading.id = 'study-settings-title';
  heading.textContent = meta.name + ' 설정';
  const form = document.createElement('form');
  const parameterInputs = [];
  const colorInputs = [];

  function inputRow(labelText, type, name, value) {
    const label = document.createElement('label');
    label.textContent = labelText;
    const input = document.createElement('input');
    input.type = type;
    input.name = name;
    input.value = value;
    label.append(input);
    form.append(label);
    return input;
  }
  for (const field of STUDY_PARAMETERS[meta.id] || []) {
    const input = inputRow(field.label, 'number', field.name, current.parameters[field.name]);
    input.min = field.min; input.max = field.max; input.step = field.step; input.required = true;
    parameterInputs.push(input);
  }
  meta.lines.forEach((line, i) => colorInputs.push(inputRow(
    line.label + (line.signed ? ' 상승 색' : ' 색'), 'color', 'color-' + i, current.colors[i],
  )));
  const fallingInput = meta.lines.some(line => line.signed)
    ? inputRow('히스토그램 하락 색', 'color', 'color-down', current.falling) : null;

  const status = document.createElement('div');
  status.className = 'study-settings-status';
  status.setAttribute('role', 'status');
  const actions = document.createElement('footer');
  if (openAlert) {
    const alert = document.createElement('button');
    alert.type = 'button';
    alert.className = 'study-alert';
    alert.textContent = '얼러트';
    alert.onclick = openAlert;
    actions.append(alert);
  }
  const cancel = document.createElement('button');
  cancel.type = 'button';
  cancel.textContent = '취소';
  cancel.onclick = () => dialog.close();
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.textContent = '확인';
  actions.append(cancel, submit);
  form.append(status, actions);
  form.onsubmit = e => {
    e.preventDefault();
    const parameters = Object.fromEntries(parameterInputs.map(input => [input.name, input.valueAsNumber]));
    if (meta.id === 'macd' && parameters.fast >= parameters.slow) {
      status.textContent = '장기 기간은 단기 기간보다 크게 입력하세요.';
      form.elements.slow.focus();
      return;
    }
    const next = { parameters, colors: colorInputs.map(input => input.value),
      falling: fallingInput?.value || current.falling };
    try { localStorage.setItem(key, JSON.stringify({ ...savedSettings(), [meta.id]: next })); }
    catch { status.textContent = '설정을 저장할 수 없습니다.'; return; }
    commit(next);
    dialog.close();
  };
  dialog.append(heading, form);
  document.body.append(dialog);
  dialog.addEventListener('close', () => dialog.remove(), { once: true });
  dialog.showModal();
}
