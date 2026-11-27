// Keep edits per symbol until a versioned server write acknowledges that exact snapshot.
const prefix = 'kis.pendingDrawings.v1.';
const empty = '[]';

export function createDrawingPersistence({ read, write, storage, display, status, delay = 350 }) {
  const entries = new Map();
  let active = null;
  let shown = null;

  function entry(code) {
    if (entries.has(code)) return entries.get(code);
    let draft = null, stored = null;
    try {
      stored = storage.getItem(prefix + code);
      const record = JSON.parse(stored);
      if (record && Array.isArray(JSON.parse(record.text)) &&
          (record.base === null || typeof record.base === 'string')) draft = record;
    } catch { /* Storage may be unavailable. The server still accepts normal saves. */ }
    const e = { code, draft, stored, durable: !!draft, revision: null, server: empty,
      needsRead: true, conflict: false, error: '', busy: null, timer: null };
    entries.set(code, e);
    return e;
  }

  function persist(e) {
    try {
      if (e.draft) {
        const value = JSON.stringify(e.draft);
        storage.setItem(prefix + e.code, value);
        e.stored = value;
      } else {
        if (storage.getItem(prefix + e.code) === e.stored) storage.removeItem(prefix + e.code);
        e.stored = null;
      }
      e.durable = true;
    } catch { e.durable = false; }
  }

  function report(e) {
    if (active !== e) return;
    let message = e.error;
    if (e.conflict) message = '다른 창에서 이 종목의 그림을 변경했습니다.';
    else if (e.draft && !e.durable) message = e.error || '이 창에만 보관 중 · 서버 저장 대기';
    else if (!message && e.draft) message = '저장 중…';
    else if (!message) message = e.revision === null ? '그림 불러오는 중…' : '저장됨';
    status(message, Boolean(e.error), e.conflict);
  }

  function present(e) {
    if (active !== e) return;
    const text = e.draft?.text ?? e.server;
    if (shown !== text) {
      shown = text;
      // The drawing manager modifies objects on edit and repairs IDs in older files.
      display(JSON.parse(text));
    }
    report(e);
  }

  function acceptRemote(e, result) {
    if (typeof result?.revision !== 'string' || !Array.isArray(result.drawings)) {
      throw new Error('그림 응답을 확인할 수 없습니다.');
    }
    e.revision = result.revision;
    e.server = JSON.stringify(result.drawings);
    const draft = e.draft;
    e.conflict = false;
    if (draft) {
      if (draft.text === e.server) e.draft = null;
      else if (draft.base === null) {
        // Before the first successful read only new drawings were visible to the user.
        const additions = JSON.parse(draft.text);
        const byId = new Map(result.drawings.filter(Boolean).map(item => [item.id, item]));
        e.conflict = additions.some(item => byId.has(item.id) &&
          JSON.stringify(byId.get(item.id)) !== JSON.stringify(item));
        if (!e.conflict) {
          draft.text = JSON.stringify([...result.drawings, ...additions.filter(item => !byId.has(item.id))]);
          draft.base = e.revision;
        }
      } else if (draft.base === e.revision || draft.sent === e.server) {
        draft.base = e.revision;
      } else {
        e.conflict = true;
      }
      if (!e.conflict && e.draft?.text === e.server) e.draft = null;
      persist(e);
    }
    present(e);
  }

  async function synchronize(e) {
    let reading = false;
    try {
      while (e.needsRead || (e.draft && !e.conflict)) {
        if (e.needsRead) {
          reading = true;
          e.needsRead = false;
          acceptRemote(e, await read(e.code));
          reading = false;
          continue;
        }
        const sent = e.draft.text;
        e.draft.sent = sent;
        persist(e);
        const result = await write(e.code, e.draft.base, sent);
        if (result.conflict) {
          acceptRemote(e, result);
          continue;
        }
        if (typeof result?.revision !== 'string') throw new Error('그림 저장을 확인할 수 없습니다.');
        e.server = sent;
        e.revision = result.revision;
        if (e.draft.text === sent) e.draft = null;
        else { e.draft.base = e.revision; e.draft.sent = null; }
        persist(e);
      }
    } catch {
      // After an ambiguous write error, first check whether the server already committed it.
      e.needsRead = true;
      e.error = reading ? '그림 불러오기 실패' : '그림 저장 실패';
    }
    report(e);
  }

  function flush(e) {
    clearTimeout(e.timer);
    if (e.busy) return e.busy;
    e.error = '';
    e.busy = synchronize(e).finally(() => {
      e.busy = null;
      if (active !== e && !e.draft) entries.delete(e.code);
    });
    return e.busy;
  }

  function select(code) {
    const previous = active;
    active = entry(code);
    if (active !== previous) {
      shown = null;
      if (previous?.draft && !previous.conflict) flush(previous);
      else if (previous && !previous.busy && !previous.draft) entries.delete(previous.code);
    }
    active.needsRead = true;
    present(active);
    return flush(active);
  }

  function change(drawings) {
    if (!active) return;
    const e = active;
    shown = JSON.stringify(drawings);
    if (!e.draft) e.draft = { base: e.revision, text: shown, sent: null };
    else e.draft.text = shown;
    persist(e);
    clearTimeout(e.timer);
    if (!e.conflict) e.timer = setTimeout(() => flush(e), e.durable ? delay : 0);
    report(e);
  }

  function recover() {
    const codes = [];
    try {
      for (let i = 0; i < storage.length; i++) {
        const key = storage.key(i);
        if (key?.startsWith(prefix)) codes.push(key.slice(prefix.length));
      }
    } catch { return; }
    for (const code of codes) {
      const e = entry(code);
      if (e.draft && !e.conflict) flush(e);
    }
  }

  return {
    select, change, recover,
    retry() { if (active) { active.needsRead = true; return flush(active); } },
    resolve(keepLocal) {
      const e = active;
      if (!e?.conflict) return;
      if (keepLocal) e.draft.base = e.revision;
      else e.draft = null;
      e.conflict = false;
      persist(e);
      present(e);
      return flush(e);
    },
    flushAll() { return Promise.all([...entries.values()].filter(e => e.draft && !e.conflict).map(flush)); },
    get unprotectedEdits() { return [...entries.values()].some(e => e.draft && !e.durable); },
  };
}
