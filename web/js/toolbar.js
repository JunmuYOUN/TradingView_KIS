import { TOOLS } from './drawings.js';
import { setupDrawingPresets } from './drawing-presets.js';
import { mountStyleFields } from './style-fields.js';

const $ = (id) => document.getElementById(id);
const groups = [
  ['cursor'], ['trend', 'ray', 'hline', 'vline', 'channel'], ['fib'], ['elliott5', 'elliottABC'],
  ['range'], ['brush', 'arrow', 'rectangle', 'ellipse', 'triangle'], ['text'], ['marker'],
];

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  const use = document.createElementNS(svg.namespaceURI, 'use');
  use.setAttribute('href', '/toolbar-icons.svg#' + name);
  svg.setAttribute('aria-hidden', 'true');
  svg.append(use);
  return svg;
}

function nameButton(button, label) {
  button.title = label;
  button.setAttribute('aria-label', label);
}

function button(name, label, action) {
  const b = document.createElement('button');
  b.type = 'button';
  nameButton(b, label);
  b.append(icon(name));
  b.addEventListener('click', action);
  return b;
}

export function setupDrawingToolbar(drawings, chart, openDrawingAlert) {
  const rail = $('tools');
  const flyout = $('tool-menu');
  const rows = [];
  const favorites = document.createElement('div');
  favorites.id = 'drawing-favorites';
  const favoriteKey = 'kis.favoriteDrawingTools.v1';
  let favoriteIds = [];
  try {
    const saved = JSON.parse(localStorage.getItem(favoriteKey) || '[]');
    if (Array.isArray(saved)) favoriteIds = [...new Set(saved.filter(id => Object.hasOwn(TOOLS, id)))];
  } catch { /* 즐겨찾기 없이 시작 */ }
  let opener = null;

  function updateFavorites() {
    favorites.replaceChildren();
    for (const id of favoriteIds) {
      const shortcut = button(id, TOOLS[id].label + ' · 즐겨찾기', () => drawings.setTool(id));
      shortcut.dataset.favoriteTool = id;
      shortcut.setAttribute('aria-pressed', String(!drawings.activePreset && drawings.tool === id));
      shortcut.disabled = drawings.hidden || drawings.locked;
      favorites.append(shortcut);
    }
  }
  function dismiss() {
    if (opener) opener.setAttribute('aria-expanded', 'false');
    flyout.hidden = true;
    opener = null;
  }

  function openGroup(trigger, ids) {
    const alreadyOpen = opener === trigger;
    dismiss();
    if (alreadyOpen) return;
    opener = trigger;
    trigger.setAttribute('aria-expanded', 'true');
    flyout.replaceChildren();
    for (const id of ids) {
      const item = button(id, TOOLS[id].label, () => {
        dismiss();
        drawings.setTool(id);
        trigger.previousElementSibling.focus();
      });
      item.dataset.tool = id;
      const shortcut = document.createElement('kbd');
      shortcut.textContent = TOOLS[id].key?.toUpperCase() || '';
      item.append(TOOLS[id].label, shortcut);
      const row = document.createElement('div');
      row.className = 'tool-option';
      const star = button('favorite', TOOLS[id].label + ' 즐겨찾기', () => {
        const next = favoriteIds.includes(id) ? favoriteIds.filter(value => value !== id) : [...favoriteIds, id];
        favoriteIds = next;
        try { localStorage.setItem(favoriteKey, JSON.stringify(next)); } catch { /* 현재 도구막대에 적용 */ }
        star.setAttribute('aria-pressed', String(next.includes(id)));
        updateFavorites();
      });
      star.dataset.favoriteTool = id;
      star.className = 'tool-favorite';
      star.setAttribute('aria-pressed', String(favoriteIds.includes(id)));
      row.append(item, star);
      flyout.append(row);
    }
    flyout.hidden = false;
    const rect = trigger.getBoundingClientRect();
    flyout.style.left = (rail.getBoundingClientRect().right + 5) + 'px';
    flyout.style.top = Math.max(4, Math.min(rect.top, window.innerHeight - flyout.offsetHeight - 6)) + 'px';
    flyout.querySelector('button').focus();
  }

  function addTool(ids) {
    const row = document.createElement('div');
    row.className = 'tool-row';
    const primary = button(ids[0], TOOLS[ids[0]].label, () => { dismiss(); drawings.setTool(primary.dataset.tool); });
    primary.dataset.tool = ids[0];
    row.append(primary);
    let arrow;
    if (!['cursor', 'ruler', 'zoom'].includes(ids[0])) {
      arrow = button('more', TOOLS[ids[0]].label + ' 도구 목록', () => openGroup(arrow, ids));
      arrow.className = 'tool-more';
      arrow.setAttribute('aria-expanded', 'false');
      arrow.setAttribute('aria-controls', 'tool-menu');
      row.append(arrow);
    }
    rail.append(row);
    rows.push({ ids, primary, arrow });
  }

  function action(id, label, callback) {
    const b = button(id, label, callback);
    b.id = id;
    rail.append(b);
    return b;
  }

  function toggle(id, label, key) {
    const b = action(id, label, () => {
      drawings[key] = !drawings[key];
      b.setAttribute('aria-pressed', String(drawings[key]));
      if (key === 'hidden' || key === 'locked') {
        drawings.setTool('cursor');
        dismiss();
      }
    });
    b.setAttribute('aria-pressed', String(drawings[key]));
  }

  groups.forEach(addTool);
  rail.append(favorites);
  updateFavorites();
  const customTools = document.createElement('div');
  customTools.id = 'preset-tools';
  rail.append(customTools);
  const presetDialog = $('drawing-presets');
  const addPreset = action('plus', '그리기 도구 추가', () => {
    dismiss();
    presets.startNew();
    presetDialog.showModal();
    $('preset-type').focus();
  });
  addPreset.setAttribute('aria-haspopup', 'dialog');
  addPreset.setAttribute('aria-controls', 'drawing-presets');
  $('settings-tab').addEventListener('click', () => {
    if (presetDialog.open) presetDialog.close();
  });
  const presets = setupDrawingPresets(drawings, customTools, icon);
  rail.append(document.createElement('hr'));
  action('fit', '차트 전체 보기', () => chart.timeScale().fitContent());
  addTool(['ruler']);
  addTool(['zoom']);
  rail.append(document.createElement('hr'));
  toggle('magnet', '자석 모드', 'magnet');
  toggle('repeat', '계속 그리기', 'repeat');
  toggle('locked', '그리기 잠금', 'locked');
  toggle('hidden', '그리기 숨기기', 'hidden');
  rail.append(document.createElement('hr'));
  action('undo', '되돌리기 (Ctrl+Z)', () => drawings.undo());
  action('clear', '모두 지우기', () => {
    if (drawings.drawings.length && confirm('이 종목에 그린 항목을 모두 지울까요?')) drawings.clearAll();
  });

  document.addEventListener('pointerdown', (event) => {
    if (!flyout.contains(event.target) && !event.target.closest('.tool-more')) dismiss();
  });
  document.addEventListener('keydown', (event) => {
    if (flyout.hidden) return;
    if (event.key === 'Escape') {
      event.preventDefault();
      opener.focus();
      dismiss();
    } else if (['ArrowUp', 'ArrowDown'].includes(event.key)) {
      event.preventDefault();
      const entries = [...flyout.querySelectorAll('[data-tool]')];
      entries[(entries.indexOf(document.activeElement) + (event.key === 'ArrowUp' ? -1 : 1) + entries.length) % entries.length].focus();
    }
  });
  rail.addEventListener('scroll', dismiss);
  window.addEventListener('resize', dismiss);

  const styleFields = mountStyleFields($('chart-style-fields'), '', patch => drawings.changeStyle(patch));
  const alertButton = document.createElement('button');
  alertButton.type = 'button';
  alertButton.id = 'drawing-alert';
  alertButton.textContent = '얼러트';
  alertButton.setAttribute('aria-haspopup', 'dialog');
  alertButton.onclick = () => {
    const target = drawings.alertTarget();
    if (target) openDrawingAlert(target);
  };
  $('del').before(alertButton);
  function refreshStyle() {
    const selected = drawings.selected;
    const type = selected?.type || drawings.tool;
    const panel = $('style');
    panel.hidden = type === 'cursor' || !!TOOLS[type].temporary;
    if (panel.hidden) return;
    const style = selected ? (selected.style || {}) : drawings.toolStyle(type);
    const name = selected?.toolName || drawings.activePreset?.name || TOOLS[type].label;
    $('style-name').textContent = name + (selected ? ' 편집' : '');
    styleFields.show(type, style);
    alertButton.hidden = !openDrawingAlert || !drawings.alertTarget();
    if (!alertButton.hidden) {
      alertButton.title = type === 'trend' ? '두 점 사이에서 가격이 추세선을 돌파하면 알립니다'
        : type === 'ray' ? '시작점에서 뻗은 연장선을 가격이 돌파하면 알립니다'
        : '가격이 수평선을 돌파하면 알립니다';
    }
    $('del').hidden = !selected;
  }

  $('del').onclick = () => drawings.deleteSelected();
  $('close-style').onclick = () => drawings.setTool('cursor');
  drawings.onSelect = refreshStyle;
  drawings.onToolChange = (tool) => {
    const blocked = drawings.locked || drawings.hidden;
    for (const { ids, primary, arrow } of rows) {
      if (!drawings.activePreset && ids.includes(tool) && primary.dataset.tool !== tool) {
        primary.dataset.tool = tool;
        primary.replaceChildren(icon(tool));
      }
      const id = primary.dataset.tool;
      const meta = TOOLS[id];
      nameButton(primary, meta.label + (meta.key ? ' (' + meta.key.toUpperCase() + ')' : ''));
      primary.setAttribute('aria-pressed', String(!drawings.activePreset && id === tool));
      primary.disabled = blocked && id !== 'cursor' && !meta.temporary;
      if (arrow) arrow.disabled = primary.disabled;
    }
    $('undo').disabled = $('clear').disabled = blocked;
    for (const button of favorites.children) {
      button.disabled = blocked;
      button.setAttribute('aria-pressed', String(!drawings.activePreset && button.dataset.favoriteTool === tool));
    }
    presets.syncSelection();
    refreshStyle();
  };
  drawings.onToolChange(drawings.tool);
}
