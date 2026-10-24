/** Форма правки записи — общая для ленты (Д10) и календаря (Д6). 07-extras.md, раздел 1. */
import { CircleAlert, Trash2 } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { api, ApiFailure, errorKey } from '../../api';
import type { RecordItem } from '../../api/types';
import { Dots } from '../../components/Dots';
import { T } from '../../content/texts';
import { createdText } from '../../lib/dates';
import { sessionExpired } from '../../state/session';
import { buildPatch, validate, type EditErrors } from './patch';
import './edit.css';

type Props = {
  record: RecordItem;
  onSaved: (record: RecordItem) => void;
  onCancel: () => void;
  /** Только в календаре и при Д2 */
  onDelete?: () => void;
};

export function EditForm({ record, onSaved, onCancel, onDelete }: Props) {
  const [text, setText] = useState(record.text);
  const [date, setDate] = useState(record.date ?? '');
  const [time, setTime] = useState(record.time ?? '');
  const [end, setEnd] = useState(record.end_time ?? '');
  const interval = record.type === 'busy' || !!record.end_time;
  const [errors, setErrors] = useState<EditErrors>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const textRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    textRef.current?.focus();
  }, []);

  useLayoutEffect(() => {
    const el = textRef.current;
    if (!el) return;
    el.style.height = 'auto';
    const line = parseFloat(getComputedStyle(el).lineHeight) || 20;
    const pad = parseFloat(getComputedStyle(el).paddingTop) * 2 + 2;
    el.style.height = `${Math.min(el.scrollHeight + 2, line * 3 + pad)}px`;
  }, [text]);

  const save = async () => {
    const values = interval ? { text, date, time, end } : { text, date, time };
    const found = validate(record, values);
    setErrors(found);
    if (found.text || found.datetime || record.id === null) return;
    const patch = buildPatch(record, values);
    if (Object.keys(patch).length === 0) {
      onCancel();
      return;
    }
    setBusy(true);
    setFailure(null);
    try {
      onSaved(await api.patchRecord(record.id, patch));
    } catch (error) {
      if (error instanceof ApiFailure && error.error.type === 'unauthorized') {
        sessionExpired();
        return;
      }
      setFailure(T[errorKey(error) === 'err_network' ? 'err_network' : 'err_server']);
      setBusy(false);
    }
  };

  return (
    <div
      className="edit-form"
      onKeyDown={(e) => {
        if (e.key === 'Escape') { e.stopPropagation(); onCancel(); }
        if (e.key === 'Enter' && (e.target as HTMLElement).tagName === 'INPUT') { e.preventDefault(); void save(); }
      }}
    >
      <label className="edit-form__field">
        <span className="edit-form__label">{T.edit_text}</span>
        <textarea
          ref={textRef}
          className="edit-form__textarea"
          rows={1}
          maxLength={300}
          value={text}
          disabled={busy}
          onChange={(e) => { setText(e.target.value); setErrors((v) => ({ ...v, text: undefined })); }}
        />
        {errors.text && <span className="edit-form__error">{T[errors.text]}</span>}
      </label>
      {record.type !== 'note' && <div className={`edit-form__row ${interval ? 'edit-form__row--interval' : ''}`}>
        <label className="edit-form__field edit-form__field--date">
          <span className="edit-form__label">{record.date_kind === 'deadline' ? T.edit_date_deadline : T.edit_date}</span>
          <input className="edit-form__input" type="date" value={date} disabled={busy}
            onChange={(e) => { setDate(e.target.value); setErrors((v) => ({ ...v, datetime: undefined })); }} />
        </label>
        <label className="edit-form__field edit-form__field--time">
          <span className="edit-form__label">{interval ? T.edit_time_from : T.edit_time}</span>
          <input className="edit-form__input" type="time" value={time} disabled={busy}
            onChange={(e) => { setTime(e.target.value); setErrors((v) => ({ ...v, datetime: undefined })); }} />
        </label>
        {interval && (
          <label className="edit-form__field edit-form__field--time">
            <span className="edit-form__label">{T.edit_time_to}</span>
            <input className="edit-form__input" type="time" value={end} disabled={busy}
              onChange={(e) => { setEnd(e.target.value); setErrors((v) => ({ ...v, datetime: undefined })); }} />
          </label>
        )}
      </div>}
      {errors.datetime && <span className="edit-form__error">{T[errors.datetime]}</span>}
      {createdText(record) && <span className="edit-form__created">{createdText(record)}</span>}
      {failure && (
        <div className="edit-form__failure" role="alert">
          <CircleAlert size={14} strokeWidth={1.5} aria-hidden="true" />
          {failure}
        </div>
      )}
      <div className="edit-form__buttons">
        {onDelete && (
          <button type="button" className="edit-form__delete" disabled={busy} onClick={onDelete}>
            <Trash2 size={15} strokeWidth={1.5} aria-hidden="true" />
            {T.edit_delete}
          </button>
        )}
        <span className="edit-form__spacer" />
        <button type="button" className="pill pill--secondary pill--compact" disabled={busy} onClick={onCancel}>{T.edit_cancel}</button>
        <button type="button" className="pill pill--selected pill--compact pill--action" disabled={busy} onClick={() => void save()}>
          {busy ? <Dots className="dots--light" /> : T.edit_save}
        </button>
      </div>
    </div>
  );
}
