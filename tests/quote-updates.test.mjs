import test from 'node:test';
import assert from 'node:assert/strict';
import { mergeQuoteWindow, readQuoteWindow } from '../web/js/quote-updates.js';
import { setupPriceAlerts } from '../web/js/chart-actions.js';
import { studyValues, studyTailBars, studyParameters } from '../web/js/study-data.js';

const bar = (time, close) => ({ time, close, open: close, low: close, high: close, volume: 1 });

test('backfill fetches exclusive pages and keeps newer copies of overlapping quotes', async () => {
  const pages = [
    { bars: [bar(4, 100), bar(5, 99)], recovery: 'backfilled', next_before: 4, quote_status: { delayed: true } },
    { bars: [bar(2, 101), bar(3, 103), bar(4, 98)], recovery: 'backfilled', next_before: 2 },
    { bars: [bar(1, 99)], recovery: 'backfilled' },
  ];
  const cursors = [];
  const result = await readQuoteWindow((since, before) => {
    assert.equal(since, 1);
    cursors.push(before);
    return pages.shift();
  }, 1);
  assert.deepEqual(cursors, [null, 4, 2]);
  assert.deepEqual(result.bars.map(b => b.close), [99, 101, 103, 100, 99]);
  assert.equal(result.quote_status.delayed, true);
  await assert.rejects(readQuoteWindow(() => ({ bars: [], next_before: 2 }), 1));
});

test('ordinary polls skip unchanged rows and retain earlier history and corrections', () => {
  const history = [bar(10, 10), bar(20, 20), bar(30, 30)];
  assert.equal(mergeQuoteWindow(history, [bar(20, 20), bar(30, 30)]).changes, 0);
  let update = mergeQuoteWindow(history, [bar(30, 31)]);
  assert.equal(update.tailOnly, true);
  assert.equal(update.observeFrom, 2);
  update = mergeQuoteWindow(history, [bar(20, 21), bar(25, 24), bar(30, 31), bar(40, 41)]);
  assert.deepEqual(history.map(b => b.close), [10, 21, 24, 31, 41]);
  assert.equal(update.rebase, true);
  assert.equal(update.anchor, 3);
  assert.equal(update.observeFrom, 4);
  update = mergeQuoteWindow(history, [bar(80, 80)], 'unavailable');
  assert.equal(update.reset, true);
  assert.equal(update.observeFrom, history.length);
});

test('99 → 101 → 99 fires price, indicator-level, moving-line and drawing rules at their actual bars', t => {
  const rules = [
    { key: 'symbol', direction: 'up', price: 100 },
    { kind: 'study', id: 'rsi', name: 'RSI', line: 0, level: 100, direction: 'either', repeat: true },
    { kind: 'study', id: 'sma', name: 'SMA', line: 0, direction: 'up' },
    { kind: 'drawing', id: 'trend', name: '추세선', direction: 'up' },
  ].map(rule => ({ key: 'symbol', code: 'symbol', tf: '1', ...rule }));
  let saved = JSON.stringify(rules);
  const elements = new Map();
  globalThis.document = { getElementById(id) {
    if (!elements.has(id)) elements.set(id, {});
    return elements.get(id);
  } };
  Object.defineProperty(globalThis, 'localStorage', { configurable: true, value: {
    getItem: () => saved, setItem: (_, value) => { saved = value; },
  } });
  t.after(() => { delete globalThis.document; delete globalThis.localStorage; });
  t.mock.method(globalThis, 'setTimeout', () => 0);
  const bars = [bar(1, 99)];
  const study = { rsi: [99, 101, 99], sma: [100, 100, 120] };
  const trend = [100, 100, 120];
  const alerts = setupPriceAlerts(() => ({
    key: 'symbol', code: 'symbol', tf: '1', chartReady: true, price: bars.at(-1).close, time: bars.at(-1).time,
    label: '종목',
  }), {
    studyTarget: () => null, drawingTarget: () => null,
    studyValue: (id, line, slot = bars.length - 1) => study[id][slot],
    drawingValue: (id, time) => trend[time - 1],
  });
  alerts.reset();
  const update = mergeQuoteWindow(bars, [bar(1, 99), bar(2, 101), bar(3, 99)], 'backfilled');
  alerts.observeBars(bars, update.observeFrom, update.anchor);
  assert.deepEqual(JSON.parse(saved).map(rule => rule.id), ['rsi']);
  const text = elements.get('alert-notice').textContent;
  for (const name of ['100', 'RSI', 'SMA', '추세선', '상향 돌파', '하향 돌파']) assert.ok(text.includes(name));
  // A revision to old bars realigns the baseline and cannot generate a new alert by itself.
  elements.get('alert-notice').textContent = '';
  const correction = mergeQuoteWindow(bars, [bar(1, 105)]);
  alerts.observeBars(bars, correction.observeFrom, correction.anchor);
  assert.equal(elements.get('alert-notice').textContent, '');
});

test('finite-window updates agree with full-history study values on the current bar', () => {
  const bars = Array.from({ length: 740 }, (_, i) => {
    const close = 100 + Math.sin(i / 7) + (i % 11);
    return { ...bar(1 + i, close), high: close + 2, low: close - 1 };
  });
  for (const id of ['sma','wma','bb','stoch','donchian','roc','momentum','ichimoku','rsi']) {
    const params = studyParameters(id);
    const full = studyValues(id, bars, '1', params);
    const tail = studyValues(id, studyTailBars(id, bars, params), '1', params);
    full.forEach((line, i) => assert.ok(Math.abs(line.at(-1) - tail[i].at(-1)) < 1e-9, id));
  }
});
