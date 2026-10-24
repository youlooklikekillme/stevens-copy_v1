import { describe, expect, it } from 'vitest';
import { dayTitle, groupByDay, groupByTense, isPast, metaDate, metaParts, pluralKey, shortDate } from './dates';
import type { RecordItem } from '../api/types';

const today = new Date(2026, 9, 21, 10, 0);
const rec = (over: Partial<RecordItem>): RecordItem => ({
  id: 1, type: 'task', text: 'x', date: '2026-10-22', time: null, date_kind: 'exact',
  status: 'open', created_at: null, repeat: null, demo: false, marks: [], highlight: null, ...over,
});

describe('busy interval', () => {
  it('shows the interval and is not past until it ends', () => {
    const work = rec({ type: 'busy', date: '2026-10-21', time: '09:00', end_time: '18:00', status: null });
    expect(metaParts(work, false, today).map((p) => p.text)).toEqual(['09:00–18:00']);
    expect(isPast(work, today)).toBe(false);
    expect(isPast(work, new Date(2026, 9, 21, 18, 30))).toBe(true);
  });
});

describe('dates', () => {
  it('formats short and relative dates', () => {
    expect(shortDate('2026-10-19', today)).toBe('пн, 19 окт');
    expect(shortDate('2027-03-05', today)).toBe('пт, 5 мар 2027');
    expect(metaDate('2026-10-22', today)).toBe('завтра');
    expect(dayTitle('2026-10-21', today)).toBe('Сегодня, ср 21 окт');
    expect(dayTitle('2026-10-23', today)).toBe('Пт, 23 окт');
  });
  it('splits a person dossier into ahead and past', () => {
    const groups = groupByTense(
      [rec({ id: 1 }), rec({ id: 2, date: null, date_kind: 'none' }), rec({ id: 3, date: '2026-10-20' }),
        rec({ id: 4, date: null, date_kind: 'none', status: 'done' })],
      today,
    );
    expect(groups.map((g) => [g.ahead, g.records.map((r) => r.id)])).toEqual([[true, [1, 2]], [false, [3, 4]]]);
  });
  it('builds meta parts', () => {
    expect(metaParts(rec({ time: '08:00' }), true, today).map((p) => p.text)).toEqual(['завтра', '08:00']);
    expect(metaParts(rec({ time: '08:00' }), false, today).map((p) => p.text)).toEqual(['08:00']);
    expect(metaParts(rec({ date_kind: 'deadline', date: '2026-10-25' }), false, today)[0].text).toBe('до вс, 25 окт');
    expect(metaParts(rec({ date_kind: 'none', date: null }), true, today)[0].text).toBe('без срока');
  });
  it('detects past records', () => {
    expect(isPast(rec({ date: '2026-10-21', time: '08:00' }), today)).toBe(true);
    expect(isPast(rec({ date: '2026-10-21' }), today)).toBe(false);
    expect(isPast(rec({ date: '2026-10-20', date_kind: 'deadline' }), today)).toBe(true);
  });
  it('groups by day with no-date group last', () => {
    const groups = groupByDay([rec({ id: 1, date: null, date_kind: 'none' }), rec({ id: 2 }), rec({ id: 3 })]);
    expect(groups.map((g) => g.date)).toEqual(['2026-10-22', null]);
  });
  it('plural', () => {
    expect([1, 2, 5, 11, 21, 22, 25].map(pluralKey)).toEqual(['one', 'few', 'many', 'many', 'one', 'few', 'many']);
  });
});
