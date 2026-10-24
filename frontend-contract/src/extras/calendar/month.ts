/** Сетка месяца, точки загрузки, «В то же время» — 07-extras.md, 2.3–2.5. */
import type { RecordItem } from '../../api/types';
import { T, fill } from '../../content/texts';
import { addDays, isoDate, parseIso, pluralKey, weekdayIndex } from '../../lib/dates';

/** Пороги точек: 1 запись — 1, 2 — 2, 3 и больше — 3 */
export const DOT_THRESHOLDS = [1, 2, 3] as const;
export const WEEKS = 6;

export function dotsFor(count: number): number {
  return DOT_THRESHOLDS.filter((t) => count >= t).length;
}

/** 6 недель с понедельника, всегда 42 дня */
export function gridDays(year: number, month: number): Date[] {
  const first = new Date(year, month, 1);
  const start = addDays(first, -weekdayIndex(first));
  return Array.from({ length: WEEKS * 7 }, (_, i) => addDays(start, i));
}

export function gridRange(year: number, month: number): { from: string; to: string } {
  const days = gridDays(year, month);
  return { from: isoDate(days[0]), to: isoDate(days[days.length - 1]) };
}

/** Точки — только дела: занятость рисуется отдельной серой полоской */
export function countByDate(records: RecordItem[]): Map<string, number> {
  const map = new Map<string, number>();
  for (const r of records) {
    if (r.date && r.type !== 'busy' && !r.marks.includes('deleted')) map.set(r.date, (map.get(r.date) ?? 0) + 1);
  }
  return map;
}

/** Дни, где есть занятость: пары, работа, учёба */
export function busyDates(records: RecordItem[]): Set<string> {
  return new Set(records.filter((r) => r.date && r.type === 'busy' && !r.marks.includes('deleted')).map((r) => r.date as string));
}

/** id встреч дня, у которых совпадает время начала с другой встречей */
export function conflictIds(records: RecordItem[]): Set<number> {
  const byTime = new Map<string, number[]>();
  for (const r of records) {
    if (r.type !== 'meeting' || !r.time || r.id === null) continue;
    byTime.set(r.time, [...(byTime.get(r.time) ?? []), r.id]);
  }
  const ids = new Set<number>();
  byTime.forEach((list) => list.length > 1 && list.forEach((id) => ids.add(id)));
  return ids;
}

export function sortDay(records: RecordItem[]): RecordItem[] {
  return [...records].sort((a, b) =>
    (a.time ?? '99:99').localeCompare(b.time ?? '99:99') || (a.id ?? 0) - (b.id ?? 0),
  );
}

export function dayLabel(value: string, count: number): string {
  const date = parseIso(value);
  const values = { weekday: T.weekdays_full[weekdayIndex(date)], day: date.getDate(), month: T.months_gen[date.getMonth()] };
  if (!count) return fill(T.calendar_day_label_empty, values);
  return fill(T.calendar_day_label, { ...values, count: fill(T.records_count[pluralKey(count)], { count }) });
}
