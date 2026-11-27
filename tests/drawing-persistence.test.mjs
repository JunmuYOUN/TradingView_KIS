import assert from 'node:assert/strict';
import test from 'node:test';
import { createDrawingPersistence } from '../web/js/drawing-persistence.js';

const point = id => ({ id, type: 'hline', points: [{ price: 10, time: 1 }] });
const ids = drawings => drawings.map(d => d.id);
const deferred = () => Promise.withResolvers();

function fixture() {
  const saved = new Map();
  const files = new Map();
  const writes = [];
  const storage = {
    get length() { return saved.size; }, key: i => [...saved.keys()][i],
    getItem: key => saved.get(key) ?? null, setItem: (key, value) => saved.set(key, value),
    removeItem: key => saved.delete(key),
  };
  const get = code => files.get(code) ?? { revision: '0', drawings: [] };
  const save = async (code, revision, text) => {
    writes.push({ code, revision, text });
    const current = get(code);
    if (current.revision !== revision) return { ...current, conflict: true };
    const result = { revision: String(Number(revision) + 1), drawings: JSON.parse(text) };
    files.set(code, result);
    return result;
  };
  const view = (hooks = {}) => {
    const state = {};
    const store = createDrawingPersistence({
      read: async code => get(code), write: save, delay: 2000, storage,
      display: list => { state.drawings = list; },
      status: (message, retry, conflict) => Object.assign(state, { message, retry, conflict }),
      ...hooks,
    });
    // In the chart, DrawingManager has already applied the edit before its onChange callback.
    const notify = store.change;
    store.change = drawings => { state.drawings = drawings; notify(drawings); };
    return { store, state };
  };
  return { saved, files, writes, get, save, view };
}

test('switching symbols flushes both edits; a slow old write is acknowledged before its successor', async () => {
  const f = fixture();
  const started = deferred(), release = deferred();
  let first = true;
  const { store } = f.view({ async write(...args) {
    if (first) { first = false; started.resolve(); await release.promise; }
    return f.save(...args);
  } });
  await store.select('A');
  store.change([point('a1')]);
  const flight = store.flushAll();
  await started.promise;
  store.change([point('a1'), point('a2')]);
  await store.select('B');
  store.change([point('b1')]);
  const all = store.flushAll();
  release.resolve();
  await Promise.all([flight, all]);
  assert.deepEqual(ids(f.get('A').drawings), ['a1','a2']);
  assert.deepEqual(ids(f.get('B').drawings), ['b1']);
  assert.deepEqual(f.writes.filter(w=>w.code==='A').map(w=>w.revision), ['0','1']);
  assert.equal(f.saved.size, 0);
});

test('read failure cannot overwrite existing drawings; early edits are merged after retry', async () => {
  const f = fixture();
  f.files.set('A', { revision:'3', drawings:[point('existing')] });
  let fail = true;
  const { store, state } = f.view({ read: async code => {
    if (fail) throw Error('offline');
    return f.get(code);
  } });
  await store.select('A');
  assert.equal(state.retry, true);
  store.change([point('new')]);
  await store.flushAll();
  assert.equal(f.writes.length, 0);
  fail = false;
  await store.retry();
  assert.deepEqual(ids(f.get('A').drawings), ['existing','new']);
  assert.deepEqual(ids(state.drawings), ['existing','new']);
});

test('reload recovers inactive pending edits even if an earlier save succeeded without acknowledgement', async () => {
  const f = fixture();
  const started = deferred(), result = deferred();
  const tab = f.view({ async write(...args) {
    await f.save(...args);
    started.resolve();
    await result.promise;
    throw Error('browser closed');
  } });
  await tab.store.select('A');
  tab.store.change([point('first')]);
  const flight = tab.store.flushAll();
  await started.promise;
  tab.store.change([point('latest')]);
  result.resolve();
  await flight;
  // The freshly opened chart may be showing another symbol when recovery runs.
  const restored = f.view();
  restored.store.recover();
  await restored.store.select('B');
  await restored.store.flushAll();
  assert.deepEqual(ids(f.get('A').drawings), ['latest']);
});

test('conflicting writes retain both versions and require an explicit resolution', async () => {
  for (const keepLocal of [true,false]) {
    const f = fixture();
    const { store, state } = f.view();
    await store.select('A');
    store.change([point('local')]);
    f.files.set('A', { revision:'7', drawings:[point('elsewhere')] });
    await store.flushAll();
    assert.equal(state.conflict, true);
    assert.deepEqual(ids(state.drawings), ['local']);
    assert.deepEqual(ids(f.get('A').drawings), ['elsewhere']);
    await store.resolve(keepLocal);
    assert.deepEqual(ids(f.get('A').drawings), [keepLocal ? 'local' : 'elsewhere']);
    assert.deepEqual(state.drawings, f.get('A').drawings);
  }
});
