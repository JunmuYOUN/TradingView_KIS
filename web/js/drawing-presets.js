import { TOOLS } from './drawings.js';
import { mountStyleFields } from './style-fields.js';

const STORAGE_KEY = 'kis.drawingPresets.v1';
const byId = id => document.getElementById(id);
const available = Object.entries(TOOLS).filter(([id, meta]) => id !== 'cursor' && !meta.temporary);

function loadPresets() {
  const raw = localStorage.getItem(STORAGE_KEY);
  let items;
  try { items = JSON.parse(raw || '[]'); } catch { return []; }
  if (!Array.isArray(items)) return [];
  return items.filter(item => item && typeof item.id === 'string' && typeof item.name === 'string' &&
    available.some(([id]) => id === item.type) && item.style && typeof item.style === 'object');
}

export function setupDrawingPresets(drawings, rail, icon) {
  const form = byId('preset-form');
  const types = byId('preset-type');
  const name = byId('preset-name');
  const list = byId('preset-list');
  const status = byId('preset-status');
  const fields = mountStyleFields(byId('preset-style-fields'), 'preset-');
  let editingId = null;
  let items = [];
  for (const [id, meta] of available) types.add(new Option(meta.label, id));

  function message(text, error = false) {
    status.textContent = text;
    status.dataset.error = String(error);
    if (error && !byId('chart-view').hidden) byId('saved').textContent = text;
  }

  function edit(preset) {
    editingId = preset?.id || null;
    types.value = preset?.type || 'rectangle';
    name.value = preset?.name || '';
    name.placeholder = TOOLS[types.value].label;
    fields.show(types.value, preset?.style || drawings.toolStyle(types.value, null));
    byId('save-preset').textContent = preset ? '변경 저장' : '도구막대에 추가';
    byId('cancel-preset').hidden = !preset;
  }

  function syncSelection() {
    for (const button of rail.children) {
      button.setAttribute('aria-pressed', String(button.dataset.preset === drawings.activePreset?.id));
      button.disabled = drawings.locked || drawings.hidden;
    }
  }

  function render() {
    rail.replaceChildren();
    list.replaceChildren();
    for (const item of items) {
      const shortcut = document.createElement('button');
      shortcut.type = 'button';
      shortcut.dataset.preset = item.id;
      shortcut.title = item.name + ' · ' + TOOLS[item.type].label;
      shortcut.setAttribute('aria-label', shortcut.title);
      const symbol = icon(item.type);
      symbol.style.color = item.style.color || TOOLS[item.type].color;
      const caption = document.createElement('span');
      caption.textContent = item.name;
      shortcut.append(symbol, caption);
      shortcut.onclick = () => drawings.setTool(item.type, item);
      rail.append(shortcut);

      const row = document.createElement('li');
      const label = document.createElement('strong');
      label.textContent = item.name;
      const kind = document.createElement('span');
      kind.textContent = TOOLS[item.type].label;
      const modify = document.createElement('button');
      modify.type = 'button';
      modify.textContent = '수정';
      modify.setAttribute('aria-label', item.name + ' 수정');
      modify.onclick = () => { edit(item); message(''); name.focus(); };
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '제거';
      remove.setAttribute('aria-label', item.name + ' 도구막대에서 제거');
      remove.onclick = () => {
        if (save(all => all.filter(entry => entry.id !== item.id))) {
          if (editingId === item.id) edit(null);
          message('도구막대에서 제거했습니다.');
        }
      };
      row.append(symbol.cloneNode(true), label, kind, modify, remove);
      list.append(row);
    }
    syncSelection();
  }

  function apply(next, refreshStyle = true) {
    items = next;
    const active = drawings.activePreset;
    if (active) {
      const updated = items.find(item => item.id === active.id);
      if (!updated || active.type !== updated.type) drawings.setTool('cursor');
      else {
        drawings.activePreset = updated;
        if (refreshStyle) drawings.onSelect(drawings.selected);
        drawings.update();
      }
    }
    render();
  }

  function save(transform, refreshStyle = true) {
    try {
      const next = transform(loadPresets());
      if (!next) { message('이 도구가 제거되었습니다. 새 도구로 추가하세요.', true); edit(null); return false; }
      localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
      apply(next, refreshStyle);
      return true;
    } catch {
      message('그리기 도구를 저장하지 못했습니다.', true);
      return false;
    }
  }

  types.onchange = () => {
    name.placeholder = TOOLS[types.value].label;
    fields.show(types.value, drawings.toolStyle(types.value, null));
  };
  form.onsubmit = event => {
    event.preventDefault();
    const item = { id: editingId || Array.from(crypto.getRandomValues(new Uint32Array(4))).join('-'), type: types.value,
      name: name.value.trim() || TOOLS[types.value].label, style: fields.read() };
    const editing = !!editingId;
    if (save(all => {
      if (!editing) return [...all, item];
      if (!all.some(entry => entry.id === item.id)) return null;
      return all.map(entry => entry.id === item.id ? item : entry);
    })) {
      edit(null);
      message(editing ? '변경을 저장했습니다.' : '도구막대에 추가했습니다.');
    }
  };
  byId('cancel-preset').onclick = () => { edit(null); message(''); };
  drawings.onPresetStyleChange = (preset, patch) => {
    if (!save(all => all.map(entry => entry.id === preset.id
      ? { ...entry, style: { ...entry.style, ...patch } } : entry), false)) drawings.onSelect(drawings.selected);
  };
  window.addEventListener('storage', event => {
    if (event.key === STORAGE_KEY || event.key === null) {
      try { apply(loadPresets()); } catch { message('그리기 도구를 불러오지 못했습니다.', true); }
    }
  });
  try { items = loadPresets(); } catch { message('그리기 도구를 불러오지 못했습니다.', true); }
  render();
  edit(null);
  return { syncSelection, startNew() { edit(null); message(''); } };
}
