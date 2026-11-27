const byId = id => document.getElementById(id);

export function setupReplay(chart, getBars, render) {
  const strip = byId('replay-strip');
  const toggle = byId('replay-button');
  const position = byId('replay-position');
  const play = byId('replay-play');
  const pick = byId('replay-pick');
  let snapshot = null;
  let frame = null;
  let timer;
  let picking = false;

  function pause() {
    clearInterval(timer);
    timer = null;
    play.textContent = '재생';
  }

  function seek(count) {
    picking = false;
    pick.setAttribute('aria-pressed', 'false');
    count = Math.max(1, Math.min(snapshot.length, count));
    frame = snapshot.slice(0, count);
    position.value = count;
    byId('replay-date').textContent = '';
    byId('replay-date').value = new Date(frame.at(-1).time * 1000).toISOString().slice(0, 16).replace('T', ' ');
    const atEnd = count === snapshot.length;
    byId('replay-step').disabled = atEnd;
    play.disabled = atEnd;
    if (atEnd) pause();
    render();
  }

  function stop(redraw = true) {
    if (!snapshot) return;
    pause();
    picking = false;
    pick.setAttribute('aria-pressed', 'false');
    snapshot = frame = null;
    strip.hidden = true;
    toggle.setAttribute('aria-expanded', 'false');
    if (redraw) render();
  }

  toggle.onclick = () => {
    if (snapshot) { stop(); return; }
    const bars = getBars();
    if (bars.length < 2) return;
    snapshot = bars.map(b => ({ ...b }));
    position.max = snapshot.length;
    strip.hidden = false;
    toggle.setAttribute('aria-expanded', 'true');
    seek(Math.max(1, snapshot.length - Math.min(40, Math.ceil(snapshot.length / 2))));
  };
  position.oninput = () => { pause(); seek(Number(position.value)); };
  pick.onclick = () => {
    pause();
    if (picking) { seek(Number(position.value)); return; }
    picking = true;
    pick.setAttribute('aria-pressed', 'true');
    frame = snapshot;
    play.disabled = byId('replay-step').disabled = true;
    byId('replay-date').value = '차트에서 시작할 봉을 클릭하세요';
    render();
  };
  byId('chart-view').addEventListener('mousedown', event => {
    if (!picking || event.button !== 0 || !byId('chart').contains(event.target)) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = byId('chart').getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    if (x >= chart.timeScale().width() || y < 0 || y >= chart.panes()[0].getHeight()) return;
    const logical = chart.timeScale().coordinateToLogical(x);
    if (logical != null) seek(Math.round(logical) + 1);
  }, true);
  document.addEventListener('keydown', event => {
    if (picking && event.key === 'Escape' && !document.querySelector('dialog[open]')) {
      event.preventDefault();
      seek(Number(position.value));
    }
  });
  byId('replay-step').onclick = () => seek(frame.length + 1);
  play.onclick = () => {
    if (timer) { pause(); return; }
    play.textContent = '일시정지';
    timer = setInterval(() => seek(frame.length + 1), 650);
  };
  byId('replay-exit').onclick = () => stop();
  byId('settings-tab').addEventListener('click', pause);
  return { get bars() { return frame; }, stop };
}

const ALERT_KEY = 'kis.localPriceAlerts.v1';
const directions = { up: '상향 돌파', down: '하향 돌파', either: '양방향 돌파' };
const tfLabel = tf => ({ D: '일봉', W: '주봉', M: '월봉' }[tf] || tf + '분봉');

function readAlerts() {
  try {
    const saved = JSON.parse(localStorage.getItem(ALERT_KEY) || '[]');
    return Array.isArray(saved) ? saved.filter(rule => {
      if (!rule || typeof rule.key !== 'string' || !Object.hasOwn(directions, rule.direction)) return false;
      if (!rule.kind) return Number.isFinite(rule.price); // Existing fixed-price alerts.
      if (typeof rule.id !== 'string' || typeof rule.tf !== 'string') return false;
      return rule.kind === 'drawing' || (rule.kind === 'study' && Number.isInteger(rule.line)
        && (rule.level === undefined || Number.isFinite(rule.level)));
    }) : [];
  } catch { return []; }
}

export function setupPriceAlerts(getContext, { studyTarget, studyValue, drawingTarget, drawingValue }) {
  const dialog = byId('alerts-dialog');
  const feedback = byId('alert-feedback');
  const list = byId('alert-list');
  const input = byId('alert-price');
  const repeatInput = byId('alert-repeat');
  const lineSelect = byId('alert-line');
  const previous = new Map();
  let alerts = readAlerts();
  let target = null;
  let openedKey;
  let openedTf;
  let noticeTimer;

  function save() {
    try { localStorage.setItem(ALERT_KEY, JSON.stringify(alerts)); }
    catch { feedback.textContent = '이 실행에서만 얼러트를 유지합니다.'; }
  }

  function setRepeat(rule, repeat) {
    rule.repeat = repeat;
    feedback.textContent = repeat ? '계속 알림으로 변경했습니다.' : '한 번 알림으로 변경했습니다.';
    save();
  }

  function describe(rule, context) {
    if (!rule.kind) return context.formatPrice?.(rule.price) ?? rule.price.toLocaleString('ko-KR');
    let name = rule.name;
    if (rule.kind === 'drawing' && rule.code === context.code) name = drawingTarget(rule.id)?.label || name;
    if (rule.kind === 'study') {
      const study = studyTarget(rule.id);
      const channel = study?.lines.find(line => line.index === rule.line)?.label || rule.lineLabel;
      name = [study?.name || name, channel].filter((part, index, parts) => part && parts.indexOf(part) === index).join(' · ');
      if (rule.level !== undefined) name += ' 기준값 ' + rule.level.toLocaleString('ko-KR', { maximumFractionDigits: 12 });
    }
    return tfLabel(rule.tf) + ' · ' + name;
  }

  function showList() {
    const context = getContext();
    list.replaceChildren();
    for (const rule of alerts) {
      if (rule.key !== context.key) continue;
      if (target && (rule.kind !== target.kind || rule.id !== target.id || rule.tf !== context.tf)) continue;
      const row = document.createElement('li');
      const description = document.createElement('span');
      description.className = 'alert-description';
      description.textContent = describe(rule, context) + ' ' + directions[rule.direction];
      const frequency = document.createElement('label');
      frequency.className = 'alert-repeat-field';
      const toggle = document.createElement('input');
      toggle.type = 'checkbox';
      toggle.checked = rule.repeat === true;
      toggle.setAttribute('aria-label', description.textContent + ' 계속 알림');
      toggle.onchange = () => setRepeat(rule, toggle.checked);
      frequency.append(toggle, '계속 알림');
      const remove = document.createElement('button');
      remove.type = 'button';
      remove.textContent = '삭제';
      remove.onclick = () => {
        previous.delete(rule);
        alerts = alerts.filter(item => item !== rule);
        save();
        showList();
      };
      row.append(description, frequency, remove);
      list.append(row);
    }
  }

  // Store the signed distance for each rule. Both price and line can change between quotes.
  function distance(rule, context) {
    if (rule.key !== context.key || !Number.isFinite(context.price)) return null;
    if (!rule.kind) return context.price - rule.price;
    if (!context.chartReady || rule.tf !== context.tf) return null;
    const value = rule.kind === 'study' ? studyValue(rule.id, rule.line, context.slot) : drawingValue(rule.id, context.time);
    if (!Number.isFinite(value)) return null;
    return rule.level === undefined ? context.price - value : value - rule.level;
  }

  function reset(context = getContext()) {
    previous.clear();
    if (!alerts.length) return;
    for (const rule of alerts) previous.set(rule, distance(rule, context));
  }

  function targetChanged(kind, id, reason) {
    if (!alerts.length) return;
    const context = getContext();
    const matches = rule => rule.kind === kind && rule.id === id
      && (kind === 'study' || rule.code === context.code);
    const affected = alerts.filter(matches);
    if (!affected.length) return;
    for (const rule of affected) {
      if (reason === 'remove') previous.delete(rule);
      else previous.set(rule, distance(rule, context));
    }
    if (reason === 'remove') {
      alerts = alerts.filter(rule => !matches(rule));
      save();
    }
    if (dialog.open) showList();
  }

  function setDefaultLevel() {
    const value = target?.lines?.find(line => line.index === Number(lineSelect.value))?.value;
    input.value = Number.isFinite(value) ? Number(value.toPrecision(8)) : '';
  }
  lineSelect.onchange = setDefaultLevel;

  function updateHelp() {
    const subject = !target ? '이 종목의 새 시세가 설정 가격을'
      : target.kind === 'study' && !target.overlay ? '선택한 지표선이 기준값을' : '시세가 선택한 선을';
    let help = subject + (repeatInput.checked ? ' 돌파할 때마다 알립니다.' : ' 돌파하면 한 번 알립니다.');
    if (target) help += ' 이 종목과 봉 주기의 실시간 차트에서 작동합니다.';
    if (target?.kind === 'drawing') help += ' 선이 그려진 구간에서 감시합니다.';
    byId('alert-help').textContent = help;
  }
  repeatInput.onchange = updateHelp;

  function open(selected = null) {
    const context = getContext();
    target = selected;
    openedKey = context.key;
    openedTf = context.tf;
    const study = target?.kind === 'study';
    const threshold = study && !target.overlay;
    const needsNumber = !target || threshold;
    byId('alert-heading').textContent = target ? target.name + ' 얼러트' : '가격 얼러트';
    byId('alert-symbol').textContent = context.label + (target ? ' · ' + tfLabel(context.tf) : '');
    byId('alert-line-field').hidden = !study;
    lineSelect.replaceChildren();
    for (const line of target?.lines || []) lineSelect.add(new Option(line.label, String(line.index)));
    byId('alert-price-field').hidden = !needsNumber;
    byId('alert-price-label').textContent = threshold ? '기준값' : '가격';
    input.disabled = !needsNumber;
    if (!target && !context.allowNegative) input.min = '0';
    else input.removeAttribute('min');
    if (threshold) setDefaultLevel();
    else input.value = !target && Number.isFinite(context.price) ? context.price : '';
    byId('alert-direction').value = 'up';
    repeatInput.checked = false;
    updateHelp();
    feedback.textContent = '';
    showList();
    if (!dialog.open) dialog.showModal();
  }

  byId('alerts-button').onclick = () => open();
  byId('price-alert-form').onsubmit = event => {
    event.preventDefault();
    const context = getContext();
    if (context.key !== openedKey || (target && openedTf !== context.tf)) { dialog.close(); return; }
    const direction = byId('alert-direction').value;
    const rule = { key: context.key, direction, repeat: repeatInput.checked };
    if (target) Object.assign(rule, { kind: target.kind, id: target.id, name: target.name, tf: context.tf, code: context.code });
    if (target?.kind === 'study') {
      const line = target.lines.find(item => item.index === Number(lineSelect.value));
      if (!line) return;
      rule.line = line.index;
      rule.lineLabel = line.label;
    }
    if (!input.disabled) {
      const value = input.valueAsNumber;
      if (!Number.isFinite(value) || (!target && !context.allowNegative && value <= 0)) {
        feedback.textContent = target ? '기준값을 입력하세요.' : '유효한 가격을 입력하세요.';
        return;
      }
      if (target) rule.level = value;
      else rule.price = value;
    }
    const identity = ['key', 'direction', 'kind', 'tf', 'id', 'line', 'price', 'level'];
    const existing = alerts.find(item => identity.every(field => item[field] === rule[field]));
    if (existing) {
      if ((existing.repeat === true) === rule.repeat) feedback.textContent = '같은 조건의 얼러트가 있습니다.';
      else {
        setRepeat(existing, rule.repeat);
        showList();
      }
      return;
    }
    alerts.push(rule);
    previous.set(rule, distance(rule, context));
    feedback.textContent = '얼러트를 만들었습니다.';
    save();
    showList();
  };

  function observeBars(bars, from, baseline = -1) {
    if (!alerts.length) return;
    const context = getContext();
    function setPoint(i) {
      context.time = bars[i].time;
      context.price = bars[i].close;
      context.slot = i;
    }
    if (baseline >= 0) {
      setPoint(baseline);
      reset(context);
    }
    const fired = new Map();
    const finished = new Set();
    for (let i = from; i < bars.length; i++) {
      setPoint(i);
      for (const rule of alerts) {
        if (finished.has(rule)) continue;
        const now = distance(rule, context);
        const before = previous.get(rule);
        previous.set(rule, now);
        if (!Number.isFinite(before) || !Number.isFinite(now)) continue;
        const up = before < 0 && now >= 0;
        const down = before > 0 && now <= 0;
        if ((up && rule.direction !== 'down') || (down && rule.direction !== 'up')) {
          let hits = fired.get(rule);
          if (!hits) { hits = { up: 0, down: 0 }; fired.set(rule, hits); }
          hits[up ? 'up' : 'down']++;
          if (rule.repeat !== true) {
            previous.delete(rule);
            finished.add(rule);
          }
        }
      }
    }
    if (!fired.size) return;
    if (finished.size) {
      alerts = alerts.filter(rule => !finished.has(rule));
      save();
      if (dialog.open) showList();
    }
    const notice = byId('alert-notice');
    notice.textContent = context.label + ' · ' + [...fired].map(([rule, hits]) => {
      const labels = Object.entries(hits).filter(([, n]) => n)
        .map(([dir, n]) => directions[dir] + (n > 1 ? ' ' + n + '회' : ''));
      return describe(rule, context) + ' ' + labels.join(' / ');
    }).join(', ');
    notice.hidden = false;
    clearTimeout(noticeTimer);
    noticeTimer = setTimeout(() => { notice.hidden = true; }, 12000);
  }

  return {
    openStudy: target => open({ ...target, kind: 'study' }),
    openDrawing: ({ id, label }) => open({ kind: 'drawing', id, name: label }),
    targetChanged, reset, observeBars,
  };
}
