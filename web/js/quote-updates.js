// The first response has the newest quotes and feed status. Older pages can overlap it.
export async function readQuoteWindow(read, since) {
  const latest = await read(since, null);
  let page = latest;
  let before = Infinity;
  const pages = [latest.bars];
  let recovery = latest.recovery;
  while (page.next_before != null) {
    const cursor = page.next_before;
    if (!Number.isFinite(cursor) || cursor >= before || cursor <= since) {
      throw new Error('이전 시세를 이어 받을 수 없습니다.');
    }
    before = cursor;
    page = await read(since, before);
    pages.push(page.bars);
    if (page.recovery) recovery = page.recovery;
  }
  if (pages.length === 1) return latest;
  const rows = new Map();
  for (const bars of pages.reverse()) for (const bar of bars) rows.set(bar.time, bar);
  return { ...latest, bars: [...rows.values()].sort((a, b) => a.time - b.time), recovery };
}

function lowerBound(bars, time) {
  let lo = 0, hi = bars.length;
  while (lo < hi) {
    const mid = (lo + hi) >>> 1;
    if (bars[mid].time < time) lo = mid + 1;
    else hi = mid;
  }
  return lo;
}

const sameBar = (a, b) => a.open === b.open && a.high === b.high && a.low === b.low
  && a.close === b.close && a.volume === b.volume;

// Keep the loaded prefix. In the common case we replace or append just the last bar.
export function mergeQuoteWindow(history, incoming, recovery) {
  const first = history[0]?.time;
  const last = history.at(-1)?.time;
  let historicalEdit = false, lastChanged = false, changes = 0;
  for (const bar of incoming) {
    if (first !== undefined && bar.time < first) continue;
    if (!history.length || bar.time > history.at(-1).time) {
      history.push(bar);
    } else {
      const i = lowerBound(history, bar.time);
      if (history[i].time !== bar.time) history.splice(i, 0, bar);
      else {
        if (sameBar(history[i], bar)) continue;
        history[i] = bar;
      }
      if (bar.time === last) lastChanged = true;
      else historicalEdit = true;
    }
    changes++;
  }
  const anchor = last === undefined ? -1 : lowerBound(history, last);
  const reset = anchor < 0 || recovery === 'unavailable';
  const rebase = !reset && (historicalEdit || recovery === 'backfilled');
  // Revisions to older closed candles only correct the baseline. They never trigger alerts.
  const observeFrom = reset ? history.length : anchor + (lastChanged && !rebase ? 0 : 1);
  return { changes, tailOnly: changes === 1 && !historicalEdit, anchor, rebase, reset, observeFrom };
}
