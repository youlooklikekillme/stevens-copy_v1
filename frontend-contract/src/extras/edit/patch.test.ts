import { describe, expect, it } from 'vitest';
import type { RecordItem } from '../../api/types';
import { buildPatch, validate } from './patch';

const task: RecordItem = {
  id: 1, type: 'task', text: 'Отчёт', date: '2026-10-22', time: '10:00', date_kind: 'exact',
  status: 'open', created_at: null, repeat: null, demo: false, marks: [], highlight: null,
};

describe('edit form', () => {
  it('busy needs start and end, sends end_time', () => {
    const work: RecordItem = { ...task, type: 'busy', text: 'Работа', time: '09:00', end_time: '18:00', status: null };
    expect(validate(work, { text: 'Работа', date: '2026-10-22', time: '09:00', end: '' })).toEqual({ datetime: 'edit_err_interval' });
    expect(buildPatch(work, { text: 'Работа', date: '2026-10-22', time: '09:00', end: '17:00' })).toEqual({ end_time: '17:00' });
  });
  it('sends only changed fields', () => {
    expect(buildPatch(task, { text: 'Отчёт', date: '2026-10-22', time: '12:00' })).toEqual({ time: '12:00' });
    expect(buildPatch(task, { text: 'Отчёт', date: '', time: '' })).toEqual({ date: null, time: null });
  });
  it('meeting sends remind_before only when changed', () => {
    const meeting: RecordItem = { ...task, type: 'meeting', status: null, remind_before: 15 };
    const same = { text: 'Отчёт', date: '2026-10-22', time: '10:00' };
    expect(buildPatch(meeting, { ...same, remindBefore: 15 })).toEqual({});
    expect(buildPatch(meeting, { ...same, remindBefore: 30 })).toEqual({ remind_before: 30 });
  });
  it('validates', () => {
    expect(validate(task, { text: ' ', date: '', time: '' })).toEqual({ text: 'edit_err_text' });
    expect(validate(task, { text: 'a', date: '', time: '10:00' })).toEqual({ datetime: 'edit_err_datetime' });
    expect(validate({ ...task, type: 'meeting' }, { text: 'a', date: '2026-10-22', time: '' })).toEqual({ datetime: 'edit_err_datetime' });
  });
});
