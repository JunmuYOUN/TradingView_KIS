import { compilePersonalScript } from './study-expression.js';

export const PERSONAL_SCRIPT_KEY = 'kis.personalScripts.v1';

function stored() {
  const raw = localStorage.getItem(PERSONAL_SCRIPT_KEY);
  if (!raw) return [];
  const entries = JSON.parse(raw);
  if (!Array.isArray(entries)) throw new Error('내 스크립트 목록을 읽을 수 없습니다.');
  return entries.filter(item => item && typeof item.id === 'string' && item.id.startsWith('local:') &&
    typeof item.name === 'string' && item.name.trim() && typeof item.expression === 'string' &&
    typeof item.overlay === 'boolean' && /^#[0-9a-f]{6}$/i.test(item.color));
}

export function readPersonalScripts() {
  try { return stored(); } catch { return []; }
}

export function savePersonalScript(id, values) {
  const name = values.name.trim();
  if (!name) throw new Error('스크립트 이름을 입력하세요.');
  if (name.length > 64) throw new Error('스크립트 이름은 64자까지 입력할 수 있습니다.');
  const expression = values.expression.trim();
  compilePersonalScript(expression);
  const color = values.color.toLowerCase();
  if (!/^#[0-9a-f]{6}$/.test(color)) throw new Error('선 색을 선택하세요.');
  const scripts = stored();
  if (id && !scripts.some(item => item.id === id)) throw new Error('이 스크립트가 이미 삭제되었습니다.');
  const script = { id: id || 'local:' + Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-'),
    name, expression, color, overlay: !!values.overlay };
  const next = id ? scripts.map(item => item.id === id ? script : item) : [...scripts, script];
  try { localStorage.setItem(PERSONAL_SCRIPT_KEY, JSON.stringify(next)); }
  catch { throw new Error('이 기기에 스크립트를 저장하지 못했습니다.'); }
  return script;
}

export function deletePersonalScript(id) {
  const scripts = stored();
  const next = scripts.filter(item => item.id !== id);
  try { localStorage.setItem(PERSONAL_SCRIPT_KEY, JSON.stringify(next)); }
  catch { throw new Error('이 기기에서 스크립트를 삭제하지 못했습니다.'); }
}
