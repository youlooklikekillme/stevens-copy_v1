/** Карточка записи — 02-screens.md, раздел 3. */
import { Bell, CircleAlert, CircleCheck, Hourglass, Repeat, StickyNote, Users } from 'lucide-react';
import { useEffect, useState, type ReactNode } from 'react';
import type { RecordItem } from '../api/types';
import { T } from '../content/texts';
import { metaParts, repeatText } from '../lib/dates';
import { isPast } from '../lib/dates';
import { recordCardDeleted } from '../extensions/flags';
import { RecordCardState } from '../extensions/points';

const TYPE_ICON = { meeting: Users, task: CircleCheck, reminder: Bell, note: StickyNote, busy: Hourglass } as const;
const TYPE_WORD = {
  meeting: T.type_meeting, task: T.type_task, reminder: T.type_reminder, note: T.type_note, busy: T.type_busy,
} as const;
const NEW_MS = 2000;

type Props = {
  record: RecordItem;
  /** Внутри группы дня дата в мете не пишется */
  withDate: boolean;
  /** Подсветить как новую (только у свежего ответа) */
  isNew?: boolean;
  /** Компактная карточка панели календаря */
  compact?: boolean;
  /** «В то же время» в календаре — считает фронт */
  conflict?: boolean;
  /** Слева от содержимого: кружок-галочка в календаре */
  leading?: ReactNode;
  onClick?: () => void;
};

function useNewFlag(isNew: boolean): boolean {
  const [active, setActive] = useState(isNew);
  useEffect(() => {
    if (!isNew) return;
    setActive(true);
    const timer = setTimeout(() => setActive(false), NEW_MS);
    return () => clearTimeout(timer);
  }, [isNew]);
  return active && isNew;
}

function Mark({ on, children }: { on: boolean; children: ReactNode }) {
  return on ? <mark className="card__mark">{children}</mark> : <>{children}</>;
}

export function RecordCard({ record, withDate, isNew = false, compact = false, conflict = false, leading, onClick }: Props) {
  const fresh = useNewFlag(isNew && record.marks.includes('new'));
  const draft = record.marks.includes('draft');
  const isConflict = conflict || record.marks.includes('conflict');
  const deleted = recordCardDeleted(record);
  const done = record.status === 'done';
  const past = !draft && isPast(record);
  const Icon = record.type ? TYPE_ICON[record.type] : null;
  const parts = metaParts(record, withDate);
  const repeat = repeatText(record);

  const classes = [
    'card',
    compact && 'card--compact',
    record.type === 'busy' && 'card--busy',
    draft && 'card--draft',
    isConflict && !draft && 'card--conflict',
    (done || past || deleted) && 'card--muted',
    done && !deleted && 'card--done',
    fresh && 'is-new',
    onClick && 'card--clickable',
  ].filter(Boolean).join(' ');

  const body = (
    <>
      {leading}
      <div className="card__body">
        <div className="card__meta">
          {Icon && <Icon className="card__icon" size={14} strokeWidth={1.5} aria-hidden="true" />}
          <span className="card__meta-text">
            {[record.type ? TYPE_WORD[record.type] : null, ...parts.map((p) => p)]
              .filter((p) => p !== null)
              .map((part, index) => (
                <span key={index}>
                  {index > 0 && ' · '}
                  {typeof part === 'string' ? (
                    part
                  ) : (
                    <Mark on={!!part && record.highlight === part.field}>{part.text}</Mark>
                  )}
                </span>
              ))}
          </span>
          {repeat && (
            <span className="card__repeat" title={T.repeat_label}>
              <Repeat size={12} strokeWidth={1.5} aria-label={T.repeat_label} />
              {repeat}
            </span>
          )}
          {draft && <span className="card__state">{T.label_draft}</span>}
          {isConflict && !draft && (
            <span className="card__state card__state--alarm">
              <CircleAlert size={13} strokeWidth={1.5} aria-hidden="true" />
              {T.label_conflict}
            </span>
          )}
          <RecordCardState record={record} />
        </div>
        <div className="card__text">
          <Mark on={record.highlight === 'text'}>{record.text}</Mark>
        </div>
      </div>
    </>
  );

  if (onClick) {
    return (
      <div className={classes} role="button" tabIndex={0} onClick={onClick}
        onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); onClick(); } }}>
        {body}
      </div>
    );
  }
  return <div className={classes}>{body}</div>;
}
