/** Проверка формы правки и изменённые поля — 07-extras.md, раздел 1; контракт v1.4, 15.5. */
import type { RecordItem, RecordPatch } from '../../api/types';

export type EditValues = { text: string; date: string; time: string; end?: string };
export type EditErrors = { text?: 'edit_err_text'; datetime?: 'edit_err_datetime' | 'edit_err_interval' };

export function validate(record: RecordItem, values: EditValues): EditErrors {
  const errors: EditErrors = {};
  if (!values.text.trim()) errors.text = 'edit_err_text';
  if (record.type === 'note') return errors;
  if (record.type === 'busy') {
    // Занятость — всегда интервал: день, начало и конец
    if (!values.date || !values.time || !values.end || values.end === values.time) errors.datetime = 'edit_err_interval';
    return errors;
  }
  const strict = record.type === 'meeting' || record.type === 'reminder';
  if (strict && (!values.date || !values.time)) errors.datetime = 'edit_err_datetime';
  if (!strict && values.time && !values.date) errors.datetime = 'edit_err_datetime';
  return errors;
}

/** Только изменённые поля. Очищенная дата у задачи — без срока, время тоже очищается */
export function buildPatch(record: RecordItem, values: EditValues): RecordPatch {
  const patch: RecordPatch = {};
  const text = values.text.trim();
  if (text !== record.text) patch.text = text;
  const date = values.date || null;
  const time = values.time || null;
  if (date !== record.date) patch.date = date;
  if (date === null) {
    if (record.time !== null) patch.time = null;
  } else if (time !== record.time) {
    patch.time = time;
  }
  const end = values.end || null;
  if (values.end !== undefined && end !== (record.end_time ?? null)) patch.end_time = end;
  return patch;
}
