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

test('sync refresh requires an idle chart both before and after its asynchronous read', async () => {
  const f = fixture();
  const request = deferred(), response = deferred();
  let reads = 0, drawing = false;
  const { store, state } = f.view({ async read(code) {
    if (++reads === 2) {
      request.resolve();
      return response.promise;
    }
    return f.get(code);
  } });
  await store.select('A');
  f.files.set('A', { revision: '2', drawings: [point('phone')] });
  const idle = () => !drawing;
  drawing = true;
  assert.equal(await store.refresh(idle), false);
  assert.equal(reads, 1);
  drawing = false;
  const refreshing = store.refresh(idle);
  await request.promise;
  drawing = true;
  response.resolve(f.get('A'));
  assert.equal(await refreshing, false);
  assert.deepEqual(state.drawings, []);
  drawing = false;
  assert.equal(await store.refresh(idle), true);
  assert.deepEqual(ids(state.drawings), ['phone']);
  assert.equal(f.writes.length, 0);
});

test('a pending save blocks sync refresh and rebases independent changes onto the current revision', async () => {
  const f = fixture(), baseline = [point('edited'), point('removed')];
  f.files.set('A', { revision: '2', drawings: baseline });
  const { store, state } = f.view({ mergeChanges: () => true });
  await store.select('A');
  const edit = { ...point('edited'), points: [{ time: 1, price: 20 }] };
  store.change([edit, point('pc')]);
  f.files.set('A', { revision: '3', drawings: [...baseline, point('phone')] });
  assert.equal(await store.refresh(() => true), false);
  await store.flushAll();
  assert.deepEqual(f.writes.map(w => w.revision), ['2', '3']);
  assert.deepEqual(f.get('A').drawings, [edit, point('phone'), point('pc')]);
  assert.deepEqual(state.drawings, f.get('A').drawings);
  assert.equal(state.conflict, false);
  assert.equal(f.saved.size, 0);
});

test('after a failed save, a restarted chart can rebase its durable original snapshot', async () => {
  const f = fixture(), baseline = [point('original')];
  f.files.set('A', { revision: '1', drawings: baseline });
  const first = f.view({ mergeChanges: () => true, write: async () => { throw Error('offline'); } });
  await first.store.select('A');
  first.store.change([...baseline, point('pc')]);
  await first.store.flushAll();
  f.files.set('A', { revision: '4', drawings: [point('phone')] });
  const next = f.view({ mergeChanges: () => true });
  await next.store.select('A');
  assert.deepEqual(ids(f.get('A').drawings), ['phone', 'pc']);
  assert.deepEqual(next.state.drawings, f.get('A').drawings);
  assert.deepEqual(f.writes.map(w => w.revision), ['4']);
});

test('an overlapping local pending edit is retained when the same drawing changed during sync', async () => {
  const f = fixture(), baseline = [point('a')];
  f.files.set('A', { revision: '1', drawings: baseline });
  const { store, state } = f.view({ mergeChanges: () => true });
  await store.select('A');
  const local = { ...point('a'), points: [{ time: 1, price: 20 }] };
  const remote = { ...point('a'), points: [{ time: 1, price: 30 }] };
  store.change([local]);
  f.files.set('A', { revision: '2', drawings: [remote] });
  await store.flushAll();
  assert.equal(state.conflict, true);
  assert.deepEqual(state.drawings, [local]);
  assert.deepEqual(f.get('A').drawings, [remote]);
  assert.equal(f.saved.size, 1);
});

test('wire key ordering is ignored when rebasing an edit and when redrawing an idle chart', async () => {
  const f = fixture(), baseline = point('a');
  const reordered = { points: [{ time: 1, price: 10 }], type: 'hline', id: 'a' };
  f.files.set('A', { revision: '1', drawings: [baseline] });
  const displays = [];
  const { store } = f.view({ mergeChanges: () => true, display: drawings => displays.push(drawings) });
  await store.select('A');
  const before = displays.length;
  f.files.set('A', { revision: '2', drawings: [reordered] });
  await store.refresh(() => true);
  assert.equal(displays.length, before);
  const edit = { ...baseline, style: { color: '#fff' } };
  store.change([edit]);
  f.files.set('A', { revision: '3', drawings: [baseline, point('phone')] });
  await store.flushAll();
  assert.deepEqual(f.get('A').drawings, [edit, point('phone')]);
});

test('resolving a same-drawing conflict retains independent additions on either device', async () => {
  for (const keepLocal of [true, false]) {
    const f = fixture(), baseline = point('a');
    f.files.set('A', { revision: '1', drawings: [baseline] });
    const { store } = f.view({ mergeChanges: () => true });
    await store.select('A');
    const local = { ...baseline, value: 'local' }, remote = { ...baseline, value: 'remote' };
    store.change([local, point('pc')]);
    f.files.set('A', { revision: '2', drawings: [remote, point('phone')] });
    await store.flushAll();
    await store.resolve(keepLocal);
    assert.deepEqual(f.get('A').drawings, [keepLocal ? local : remote, point('phone'), point('pc')]);
    assert.equal(f.saved.size, 0);
  }
});

test('an acknowledged-by-server intermediate drawing subsequently removed locally stays removed after recovery', async () => {
  const f = fixture(), started = deferred(), close = deferred();
  const tab = f.view({ mergeChanges: () => true, async write(...args) {
    await f.save(...args);
    started.resolve();
    await close.promise;
    throw Error('closed before acknowledgement');
  } });
  await tab.store.select('A');
  tab.store.change([point('temporary')]);
  const flight = tab.store.flushAll();
  await started.promise;
  tab.store.change([point('final')]);
  close.resolve();
  await flight;
  f.files.set('A', { revision: '2', drawings: [point('temporary'), point('phone')] });
  const restore = f.view({ mergeChanges: () => true });
  await restore.store.select('A');
  assert.deepEqual(f.get('A').drawings, [point('phone'), point('final')]);
  assert.equal(f.saved.size, 0);
});
