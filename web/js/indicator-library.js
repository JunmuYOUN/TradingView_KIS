// Indicator picker and the toolbar's favorite-indicator shortcut.
import { readPersonalScripts, deletePersonalScript, PERSONAL_SCRIPT_KEY } from './script-store.js';
import { createScriptEditor } from './script-editor.js';

const FAVORITES_KEY = 'kis.indicatorFavorites.v1';
const INITIAL_FAVORITES = ['volume24h', 'rsi', 'vwap'];
const DISPLAY_NAMES = { volume24h: '24-hour volume', rsi: 'Relative strength index', vwap: '거래량 가중 평균 가격' };
const CATEGORIES = [
  ['퍼스널', [
    ['favorites', '즐겨찾기', 'star'], ['personal', '내 스크립트', 'person'], ['purchased', '구매됨', 'wallet'],
  ]],
  ['빌트인', [
    ['technical', '기술적 지표', 'technical'], ['fundamental', '펀더멘탈', 'fundamental'],
  ]],
  ['커뮤니티', [
    ['editors', '에디터즈 픽', 'bookmark'], ['top', '탑', 'top'], ['trending', '트렌드', 'flame'], ['market', '시장', 'bag'],
  ]],
];
const EMPTY = {
  favorites: '지표 이름 옆의 별을 눌러 즐겨찾기에 추가하세요.',
  technical: '사용할 수 있는 기술적 지표가 없습니다.',
  fundamental: '펀더멘털 지표에 필요한 재무 데이터는 현재 연결되어 있지 않습니다.',
  personal: '이 기기에 저장한 스크립트가 없습니다.',
  purchased: '구매한 스크립트는 TradingView 계정에서 관리합니다.',
};
const ICONS = {
  star: 'm12 2.6 2.9 5.9 6.5 1-4.7 4.6 1.1 6.5-5.8-3.1-5.8 3.1 1.1-6.5L2.6 9.5l6.5-1Z',
  person: 'M7.4 7.2a4.6 4.6 0 1 0 9.2 0 4.6 4.6 0 0 0-9.2 0ZM3.4 21c.4-4.3 3.4-7 8.6-7s8.2 2.7 8.6 7',
  wallet: 'M3 7.5h18v13H3Zm1.1 0L17.7 3l1.4 4.5m2 3.5h-4.3a2.7 2.7 0 0 0 0 5.4H21M17 13.7h.2',
  technical: 'M2 12.2 7.6 6.7l4.4 3.4 9-7M3 20v-3m4 3v-6m4 6v-4m4 4v-6m4 6V9',
  fundamental: 'M2.5 19.5h19M3.5 19.5v-5h4v5m2 0v-10h4v10m2 0V4h4v15.5',
  bookmark: 'M5 3.5h14v17l-7-5-7 5Z',
  top: 'M2.5 21h19M4 21v-5h3v5m2 0v-7h3v7m2 0V11h3v10m2 0V8h2v13M3 12l6-6 3 2 6-6m-3.5 0H18v3.5',
  flame: 'M12 2c-1.4 4.9 4.3 5.3 4.3 10.8 1.4-1 2-2.6 1.9-4.4 4.6 4.7 3 12.7-4.5 13.2 2.4-2.3 2.6-4.2.8-6.8-.5 2.2-3 2.9-2.6 5.5C5.6 20.2 3.2 16.8 4 12.4c.4-2.2 1.7-3.6 3-5.3-.1 2.8.6 4 2.2 5.2C8.7 8 9.3 4.6 12 2Z',
  bag: 'M4 7h16l1 14H3ZM8.5 9V5a3.5 3.5 0 0 1 7 0v4',
  search: 'M17 17l5 5M2.4 10.5a8.1 8.1 0 1 0 16.2 0 8.1 8.1 0 0 0-16.2 0Z',
  close: 'M4 4l16 16M20 4 4 20',
  check: 'm4.5 12 5 5 10-11',
};

function icon(name) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const path = document.createElementNS(svg.namespaceURI, 'path');
  path.setAttribute('d', ICONS[name]);
  svg.append(path);
  return svg;
}

function element(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text) el.textContent = text;
  if (tag === 'button') el.type = 'button';
  return el;
}

const titleOf = item => DISPLAY_NAMES[item.id] || item.name;
const normalize = value => String(value || '').normalize('NFKC').toLowerCase().replace(/[\s_-]/g, '');

function loadFavorites(catalog) {
  let ids = INITIAL_FAVORITES;
  try {
    const raw = JSON.parse(localStorage.getItem(FAVORITES_KEY));
    if (Array.isArray(raw)) ids = raw;
  } catch { /* Keep initial favorites if storage is unavailable. */ }
  return new Set(ids.filter(id => catalog.has(id)));
}

export function setupIndicatorLibrary({ button, favoritesButton, catalog, onAdd, has = () => false,
  onPersonalScriptsChange = () => {} }) {
  const byId = new Map(catalog.map(item => [item.id, item]));
  function syncScripts() {
    for (const item of byId.values()) if (item.category === 'personal') byId.delete(item.id);
    const scripts = readPersonalScripts();
    for (const item of scripts) byId.set(item.id, { ...item, category: 'personal', description: item.expression });
    return scripts;
  }
  syncScripts();
  let favorites = loadFavorites(byId);
  let sessionOnly = false;
  let category = 'favorites';
  let opener = button;
  let pending = null;
  let confirmDelete = null;

  const dialog = element('dialog');
  dialog.id = 'indicator-library';
  dialog.setAttribute('aria-labelledby', 'indicator-library-heading');
  const header = element('header', 'il-header');
  const heading = element('h2', '', '인디케이터, 메트릭 및 스트래티지');
  heading.id = 'indicator-library-heading';
  const dismiss = element('button', 'il-close');
  dismiss.setAttribute('aria-label', '닫기');
  dismiss.title = '닫기';
  dismiss.append(icon('close'));
  header.append(heading, dismiss);

  const searchBox = element('label', 'il-search');
  const search = element('input');
  search.id = 'indicator-search';
  search.type = 'search';
  search.placeholder = '찾기';
  search.setAttribute('aria-label', '지표 찾기');
  search.autocomplete = 'off';
  search.spellcheck = false;
  searchBox.append(icon('search'), search);

  const body = element('div', 'il-body');
  const sidebar = element('nav', 'il-sidebar');
  sidebar.setAttribute('aria-label', '지표 카테고리');
  for (const [title, categories] of CATEGORIES) {
    const section = element('section', 'il-group');
    section.append(element('h3', 'il-caption', title));
    for (const [id, text, glyph] of categories) {
      const item = element('button', 'il-category');
      item.dataset.category = id;
      item.append(icon(glyph), document.createTextNode(text));
      item.onclick = () => {
        category = id;
        confirmDelete = null;
        search.value = '';
        render();
      };
      section.append(item);
    }
    sidebar.append(section);
  }
  const content = element('section', 'il-content');
  content.setAttribute('aria-label', '지표 목록');
  const columnHeading = element('div', 'il-caption il-column', '이름');
  const create = element('button', 'il-script-create', '＋ 새 스크립트');
  create.id = 'create-personal-script';
  create.hidden = true;
  const list = element('ul', 'il-list');
  const notice = element('div', 'il-notice');
  notice.setAttribute('role', 'status');
  content.append(create, columnHeading, list, notice);
  body.append(sidebar, content);
  dialog.append(header, searchBox, body);
  document.body.append(dialog);

  const quick = element('div');
  quick.id = 'indicator-quick-favorites';
  quick.hidden = true;
  quick.setAttribute('role', 'menu');
  quick.setAttribute('aria-label', '즐겨찾는 지표');
  document.body.append(quick);

  function scriptsChanged() {
    const scripts = syncScripts();
    onPersonalScriptsChange(scripts);
    for (const id of favorites) if (!byId.has(id)) favorites.delete(id);
    closeQuick();
    if (dialog.open) render();
  }

  const editor = createScriptEditor(() => {
    scriptsChanged();
    if (dialog.open) notice.textContent = '이 기기에 저장했습니다.';
  });
  create.onclick = () => editor.open();
  window.addEventListener('kis-edit-personal-script', event => {
    const script = byId.get(event.detail?.id);
    if (script?.category === 'personal') editor.open(script, event.detail.openAlert);
  });
  window.addEventListener('storage', event => {
    if (event.key === PERSONAL_SCRIPT_KEY || event.key === null) scriptsChanged();
  });

  function closeQuick(restoreFocus = false) {
    if (quick.hidden) return;
    quick.hidden = true;
    favoritesButton?.setAttribute('aria-expanded', 'false');
    if (restoreFocus) favoritesButton?.focus({ preventScroll: true });
  }

  function open(source = button) {
    closeQuick();
    if (dialog.open) return;
    opener = source;
    confirmDelete = null;
    if (!sessionOnly) favorites = loadFavorites(byId);
    search.value = '';
    render();
    dialog.showModal();
    search.focus({ preventScroll: true });
    button.setAttribute('aria-expanded', 'true');
  }

  function matching() {
    const query = normalize(search.value);
    const items = [...byId.values()];
    if (query) return items.filter(item => [
      titleOf(item), item.name, item.english, item.short, item.id, ...(item.aliases || []),
    ].some(field => normalize(field).includes(query)));
    if (category === 'favorites') return [...favorites].map(id => byId.get(id));
    return items.filter(item => (item.category || 'technical') === category);
  }

  function updateAddButton(control, id) {
    const active = has(id);
    control.dataset.added = String(active);
    control.setAttribute('aria-disabled', String(active));
    control.setAttribute('aria-label', titleOf(byId.get(id)) + (active ? ' · 차트에 추가됨' : ' · 차트에 추가'));
    const badge = control.querySelector('.il-added');
    if (badge) badge.hidden = !active;
  }

  async function add(id, source) {
    if (pending || has(id)) return;
    pending = id;
    try {
      const result = await onAdd(id);
      if (result === false && !has(id)) throw new Error();
      if (source === quick) closeQuick(true);
      else {
        const control = list.querySelector('[data-study="' + CSS.escape(id) + '"]');
        if (control) updateAddButton(control, id);
      }
    } catch {
      if (source === quick) open(favoritesButton);
      notice.textContent = titleOf(byId.get(id)) + ' 지표를 적용할 수 없습니다.';
    } finally {
      pending = null;
    }
  }

  function toggleFavorite(id) {
    const focusedIndex = [...list.children].findIndex(row => row.dataset.id === id);
    if (favorites.has(id)) favorites.delete(id);
    else favorites.add(id);
    try {
      localStorage.setItem(FAVORITES_KEY, JSON.stringify([...favorites]));
      sessionOnly = false;
    } catch { sessionOnly = true; }
    render();
    if (sessionOnly) notice.textContent = '즐겨찾기 변경을 이 창에서만 유지합니다.';
    const replacement = list.querySelector('[data-favorite="' + CSS.escape(id) + '"]') ||
      list.children[Math.min(focusedIndex, list.children.length - 1)]?.querySelector('.il-star');
    (replacement || search).focus({ preventScroll: true });
  }

  function render() {
    const searching = !!normalize(search.value);
    create.hidden = searching || category !== 'personal';
    for (const item of sidebar.querySelectorAll('button')) {
      item.setAttribute('aria-pressed', String(!searching && item.dataset.category === category));
    }
    notice.textContent = '';
    list.replaceChildren();
    const items = matching();
    if (!items.length) {
      notice.textContent = searching ? '일치하는 지표가 없습니다.' :
        EMPTY[category] || 'TradingView 커뮤니티 목록은 TradingView 웹에서 제공됩니다.';
      return;
    }
    for (const item of items) {
      const row = element('li', 'il-row');
      row.dataset.id = item.id;
      const star = element('button', 'il-star');
      star.dataset.favorite = item.id;
      const favorite = favorites.has(item.id);
      star.setAttribute('aria-pressed', String(favorite));
      star.title = favorite ? '즐겨찾기에서 제거' : '즐겨찾기에 추가';
      star.setAttribute('aria-label', titleOf(item) + ' · ' + star.title);
      star.append(icon('star'));
      star.onclick = () => toggleFavorite(item.id);
      const choice = element('button', 'il-study');
      choice.dataset.study = item.id;
      choice.title = item.description || item.name;
      choice.append(element('span', 'il-name', titleOf(item)));
      const badge = element('span', 'il-added');
      badge.append(icon('check'), document.createTextNode('추가됨'));
      choice.append(badge);
      updateAddButton(choice, item.id);
      choice.onclick = () => add(item.id, list);
      row.append(star, choice);
      if (category === 'personal' && !searching && item.category === 'personal') {
        const edit = element('button', 'il-script-edit', confirmDelete === item.id ? '취소' : '수정');
        edit.setAttribute('aria-label', item.name + ' ' + edit.textContent);
        edit.onclick = () => {
          if (confirmDelete === item.id) { confirmDelete = null; render(); }
          else editor.open(item);
        };
        const remove = element('button', 'il-script-remove', confirmDelete === item.id ? '삭제 확인' : '삭제');
        remove.setAttribute('aria-label', item.name + ' ' + remove.textContent);
        remove.onclick = () => {
          if (confirmDelete !== item.id) {
            confirmDelete = item.id;
            render();
            list.querySelector('[data-id="' + CSS.escape(item.id) + '"] .il-script-remove').focus();
            notice.textContent = '삭제하면 이 기기와 차트에서 스크립트가 제거됩니다.';
          } else {
            try {
              deletePersonalScript(item.id);
              confirmDelete = null;
              scriptsChanged();
              create.focus();
            } catch (error) { notice.textContent = error.message; }
          }
        };
        row.append(edit, remove);
      }
      list.append(row);
    }
  }

  function showQuick() {
    if (!quick.hidden) { closeQuick(true); return; }
    if (!sessionOnly) favorites = loadFavorites(byId);
    quick.replaceChildren();
    for (const id of favorites) {
      const choice = element('button', 'il-quick-item');
      choice.setAttribute('role', 'menuitem');
      choice.dataset.quickStudy = id;
      choice.append(icon('star'), element('span', 'il-name', titleOf(byId.get(id))));
      const badge = element('span', 'il-added');
      badge.append(icon('check'));
      choice.append(badge);
      updateAddButton(choice, id);
      choice.onclick = () => add(id, quick);
      quick.append(choice);
    }
    const browse = element('button', 'il-browse', '지표 찾아보기');
    browse.setAttribute('role', 'menuitem');
    browse.onclick = () => open(favoritesButton);
    quick.append(browse);
    quick.hidden = false;
    const rect = favoritesButton.getBoundingClientRect();
    quick.style.left = Math.max(4, Math.min(rect.left, window.innerWidth - quick.offsetWidth - 4)) + 'px';
    quick.style.top = Math.max(4, Math.min(rect.bottom + 5, window.innerHeight - quick.offsetHeight - 4)) + 'px';
    favoritesButton.setAttribute('aria-expanded', 'true');
    quick.firstElementChild?.focus({ preventScroll: true });
  }

  search.addEventListener('input', () => { confirmDelete = null; render(); });
  function stepFocus(event, controls, fromSearch = false) {
    if (event.isComposing || !controls.length) return;
    const index = controls.indexOf(document.activeElement);
    if (!fromSearch && index < 0) return;
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      const step = event.key === 'ArrowDown' ? 1 : -1;
      const next = index < 0 ? (step > 0 ? 0 : controls.length - 1) : (index + controls.length + step) % controls.length;
      controls[next].focus();
    } else if (event.key === 'Enter' && fromSearch) {
      event.preventDefault();
      controls[0].click();
    }
  }

  dialog.addEventListener('keydown', event => {
    stepFocus(event, [...list.querySelectorAll('.il-study')], event.target === search);
  });
  dismiss.onclick = () => dialog.close();
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const rect = dialog.getBoundingClientRect();
    if (event.clientX < rect.left || event.clientX > rect.right ||
        event.clientY < rect.top || event.clientY > rect.bottom) dialog.close();
  });
  dialog.addEventListener('close', () => {
    if (dialog.open) return;
    button.setAttribute('aria-expanded', 'false');
    if (quick.hidden) opener?.focus({ preventScroll: true });
  });

  button.setAttribute('aria-haspopup', 'dialog');
  button.setAttribute('aria-controls', dialog.id);
  button.setAttribute('aria-expanded', 'false');
  button.addEventListener('click', () => open());
  if (favoritesButton) {
    favoritesButton.setAttribute('aria-haspopup', 'menu');
    favoritesButton.setAttribute('aria-controls', quick.id);
    favoritesButton.setAttribute('aria-expanded', 'false');
    favoritesButton.addEventListener('click', showQuick);
    favoritesButton.addEventListener('keydown', event => {
      if (event.key === 'ArrowDown' && quick.hidden) { event.preventDefault(); showQuick(); }
    });
    quick.addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); closeQuick(true); }
      else stepFocus(event, [...quick.querySelectorAll('button')]);
    });
    document.addEventListener('pointerdown', event => {
      if (!quick.contains(event.target) && !favoritesButton.contains(event.target)) closeQuick();
    });
    quick.addEventListener('focusout', event => {
      if (event.relatedTarget && !quick.contains(event.relatedTarget) && !favoritesButton.contains(event.relatedTarget)) closeQuick();
    });
    window.addEventListener('resize', () => closeQuick());
  }
  document.addEventListener('keydown', event => {
    if (event.key !== '/' || event.isComposing || event.ctrlKey || event.altKey || event.metaKey || dialog.open) return;
    if (document.querySelector('dialog[open]') || !button.getClientRects().length ||
        event.target.closest?.('input,textarea,select,[contenteditable=true]')) return;
    event.preventDefault();
    open();
  });
  return { open, close: () => { if (dialog.open) dialog.close(); closeQuick(); } };
}
