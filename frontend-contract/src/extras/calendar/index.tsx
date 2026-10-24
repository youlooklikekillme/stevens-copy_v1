/** Д6: встроенный календарь — 07-extras.md, раздел 2. */
import { ChevronLeft, ChevronRight, CalendarDays, Circle, CircleAlert, CircleCheck, X } from 'lucide-react';
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { api, ApiFailure, errorKey } from '../../api';
import type { RecordItem } from '../../api/types';
import { Dots } from '../../components/Dots';
import { Pill } from '../../components/Pill';
import { RecordCard } from '../../components/RecordCard';
import { T } from '../../content/texts';
import { now } from '../../lib/clock';
import { dayTitle, isoDate } from '../../lib/dates';
import { onAssistantResponse, onRecordsChanged } from '../../state/events';
import { sessionExpired } from '../../state/session';
import { store, useApp } from '../../state/store';
import { EditForm } from '../edit/EditForm';
import { DragGhost, MoveNotice, startDrag, takeSuppressedClick, useDrag } from './move';
import { calendar, load, shiftMonth } from './state';
import { busyDates, conflictIds, countByDate, dayLabel, dotsFor, gridDays, sortDay } from './month';
import './calendar.css';

const RELOAD_KINDS = new Set(['created', 'completed', 'deleted', 'cancelled', 'updated']);
const DELETED_ROW_MS = 5000;
const ERROR_MS = 6000;

type Layout = 'wide' | 'mid' | 'phone';

function layoutNow(): Layout {
  if (window.matchMedia('(min-width: 1200px)').matches) return 'wide';
  if (window.matchMedia('(min-width: 761px)').matches) return 'mid';
  return 'phone';
}

function useLayout(): Layout {
  return useSyncExternalStore(
    (cb) => {
      window.addEventListener('resize', cb);
      return () => window.removeEventListener('resize', cb);
    },
    layoutNow,
  );
}

/** Перечитывать после ответа ассистента с нужным kind и после правок в ленте */
onAssistantResponse((response) => {
  if (RELOAD_KINDS.has(response.kind) && store.get().calendarOpen) void load();
});
onRecordsChanged(() => {
  if (store.get().calendarOpen) void load();
});

export function CalendarToggle() {
  const open = useApp((s) => s.calendarOpen);
  const layout = useLayout();
  const previous = useRef(layout);
  // Сменилась ширина окна: на широком панель открыта, на остальных закрыта (07, 2.1)
  useEffect(() => {
    if (previous.current === layout) return;
    previous.current = layout;
    store.set({ calendarOpen: layout === 'wide' });
  }, [layout]);
  return (
    <button
      type="button"
      className={`cal-toggle ${open ? 'cal-toggle--open' : ''}`}
      aria-label={T.calendar_toggle}
      aria-pressed={open}
      onClick={() => store.set({ calendarOpen: !open })}
    >
      <CalendarDays size={18} strokeWidth={1.5} aria-hidden="true" />
    </button>
  );
}

function MonthGrid() {
  const state = useSyncExternalStore(calendar.subscribe, calendar.get);
  const { year, month, selected, records, loading } = state;
  const todayIso = isoDate(now());
  const counts = loading ? new Map<string, number>() : countByDate(records);
  const busyDays = loading ? new Set<string>() : busyDates(records);
  const isCurrent = year === now().getFullYear() && month === now().getMonth();
  const drag = useDrag();
  const dropDay = drag?.target.kind === 'day' && drag.target.date !== drag.record.date ? drag.target.date : null;

  return (
    <>
      <div className="cal__month">
        <span className="cal__month-title">{`${T.months[month]} ${year}`}</span>
        <span className="cal__month-nav">
          {!isCurrent && (
            <Pill variant="secondary" className="cal__today" onClick={() => {
              const d = now();
              calendar.set({ year: d.getFullYear(), month: d.getMonth(), selected: isoDate(d) });
              void load();
            }}>
              {T.calendar_today}
            </Pill>
          )}
          <button type="button" className="cal__arrow" aria-label={T.calendar_prev} data-drop-month="-1" onClick={() => shiftMonth(-1)}>
            <ChevronLeft size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
          <button type="button" className="cal__arrow" aria-label={T.calendar_next} data-drop-month="1" onClick={() => shiftMonth(1)}>
            <ChevronRight size={16} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </span>
      </div>
      <div className="cal__weekdays" aria-hidden="true">
        {T.weekdays_short.map((d) => <span key={d}>{d}</span>)}
      </div>
      <div className="cal__grid" role="grid">
        {gridDays(year, month).map((day) => {
          const iso = isoDate(day);
          const outside = day.getMonth() !== month;
          const count = counts.get(iso) ?? 0;
          const classes = [
            'cal__day',
            outside && 'cal__day--outside',
            !outside && iso < todayIso && 'cal__day--past',
            iso === todayIso && 'cal__day--today',
            iso === selected && 'cal__day--selected',
            iso === dropDay && 'cal__day--drop',
          ].filter(Boolean).join(' ');
          return (
            <button
              key={iso}
              type="button"
              className={classes}
              aria-label={dayLabel(iso, count)}
              aria-pressed={iso === selected}
              data-drop-day={iso}
              onClick={() => {
                if (outside) shiftMonth(day.getFullYear() * 12 + day.getMonth() - (year * 12 + month), iso);
                else calendar.set({ selected: iso });
              }}
            >
              <span className="cal__num">{day.getDate()}</span>
              <span className="cal__dots" aria-hidden="true">
                {busyDays.has(iso) && <span className="cal__busy" />}
                {Array.from({ length: dotsFor(count) }, (_, i) => <span key={i} className="cal__dot" />)}
              </span>
            </button>
          );
        })}
      </div>
    </>
  );
}

type Ghost = { record: RecordItem; timer: ReturnType<typeof setTimeout> };

function DayList({ canComplete, canDelete }: { canComplete: boolean; canDelete: boolean }) {
  const { selected, records, loading, failed } = useSyncExternalStore(calendar.subscribe, calendar.get);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [ghosts, setGhosts] = useState<Record<number, Ghost>>({});
  const [errors, setErrors] = useState<Record<number, string>>({});
  const ghostsRef = useRef(ghosts);
  useEffect(() => {
    ghostsRef.current = ghosts;
  }, [ghosts]);
  useEffect(() => () => Object.values(ghostsRef.current).forEach((g) => clearTimeout(g.timer)), []);

  const showError = useCallback((id: number, error: unknown) => {
    if (error instanceof ApiFailure && error.error.type === 'unauthorized') {
      sessionExpired();
      return;
    }
    setErrors((e) => ({ ...e, [id]: T[errorKey(error) === 'err_network' ? 'err_network' : 'err_server'] }));
    setTimeout(() => setErrors((e) => {
      const next = { ...e };
      delete next[id];
      return next;
    }), ERROR_MS);
  }, []);

  const dayRecords = records.filter((r) => r.date === selected);
  const ghostList = Object.values(ghosts).map((g) => g.record).filter((r) => r.date === selected && !dayRecords.some((d) => d.id === r.id));
  const list = sortDay([...dayRecords, ...ghostList]);
  const conflicts = conflictIds(dayRecords);
  const drag = useDrag();

  const toggleDone = async (record: RecordItem) => {
    if (record.id === null) return;
    const id = record.id;
    const status = record.status === 'done' ? 'open' : 'done';
    const swap = (s: 'open' | 'done' | null) =>
      calendar.set((c) => ({ records: c.records.map((r) => (r.id === id ? { ...r, status: s } : r)) }));
    swap(status);
    try {
      await api.patchRecord(id, { status });
    } catch (error) {
      swap(record.status);
      showError(id, error);
    }
  };

  const remove = async (record: RecordItem) => {
    if (record.id === null) return;
    const id = record.id;
    try {
      await api.deleteRecord(id);
    } catch (error) {
      showError(id, error);
      return;
    }
    setEditingId(null);
    const timer = setTimeout(() => {
      setGhosts((g) => {
        const next = { ...g };
        delete next[id];
        return next;
      });
      void load();
    }, DELETED_ROW_MS);
    setGhosts((g) => ({ ...g, [id]: { record, timer } }));
    calendar.set((c) => ({ records: c.records.filter((r) => r.id !== id) }));
  };

  const restore = async (id: number) => {
    const ghost = ghosts[id];
    if (!ghost) return;
    try {
      const record = await api.restoreRecord(id);
      clearTimeout(ghost.timer);
      setGhosts((g) => {
        const next = { ...g };
        delete next[id];
        return next;
      });
      calendar.set((c) => ({ records: [...c.records, record] }));
      void load();
    } catch (error) {
      showError(id, error);
    }
  };

  return (
    <div className="cal__day-list">
      <div className="day-title">{dayTitle(selected)}</div>
      {loading && list.length === 0 ? (
        <Dots className="cal__loading" />
      ) : failed ? (
        <div className="cal__failed">
          <span>{T.calendar_load_error}</span>
          <Pill variant="secondary" onClick={() => void load()}>{T.btn_retry}</Pill>
        </div>
      ) : list.length === 0 ? (
        <div className="cal__empty">{T.calendar_empty}</div>
      ) : (
        list.map((record) => {
          const id = record.id ?? -1;
          if (ghosts[id]) {
            return (
              <div key={id} className="cal__deleted">
                {T.label_deleted}
                {' · '}
                <button type="button" className="cal__restore" onClick={() => void restore(id)}>{T.btn_restore}</button>
              </div>
            );
          }
          if (editingId === id) {
            return (
              <EditForm
                key={id}
                record={record}
                onCancel={() => setEditingId(null)}
                onSaved={(saved) => {
                  setEditingId(null);
                  calendar.set((c) => ({ records: c.records.map((r) => (r.id === saved.id ? saved : r)) }));
                  void load();
                }}
                onDelete={canDelete ? () => void remove(record) : undefined}
              />
            );
          }
          const checkable = record.type === 'task' || record.type === 'reminder' || record.type === null;
          const leading = !canComplete ? undefined : checkable ? (
            <button
              type="button"
              className={`cal__check ${record.status === 'done' ? 'cal__check--done' : ''}`}
              aria-label={record.status === 'done' ? T.calendar_mark_open : T.calendar_mark_done}
              onClick={(e) => { e.stopPropagation(); void toggleDone(record); }}
            >
              {record.status === 'done'
                ? <CircleCheck size={20} strokeWidth={1.5} aria-hidden="true" />
                : <Circle size={20} strokeWidth={1.5} aria-hidden="true" />}
            </button>
          ) : (
            <span className="cal__check-space" aria-hidden="true" />
          );
          return (
            <div
              key={id}
              className={`cal__item ${drag?.record.id === id ? 'cal__item--dragging' : ''}`}
              onPointerDown={(e) => startDrag(e, record)}
            >
              <RecordCard
                record={{ ...record, marks: [], highlight: null }}
                withDate={false}
                compact
                conflict={conflicts.has(id)}
                leading={leading}
                onClick={() => { if (!takeSuppressedClick()) setEditingId(id); }}
              />
              {errors[id] && (
                <div className="cal__error">
                  <CircleAlert size={14} strokeWidth={1.5} aria-hidden="true" />
                  {errors[id]}
                </div>
              )}
            </div>
          );
        })
      )}
    </div>
  );
}

export function CalendarPanel({ canComplete, canDelete }: { canComplete: boolean; canDelete: boolean }) {
  const open = useApp((s) => s.calendarOpen);
  const layout = useLayout();

  useEffect(() => {
    if (open) void load();
  }, [open]);

  useEffect(() => {
    if (!open || layout === 'wide') return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') store.set({ calendarOpen: false });
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [open, layout]);

  if (!open) return null;
  const close = () => store.set({ calendarOpen: false });
  const panel = (
    <aside className={`cal cal--${layout}`} aria-label={T.calendar_title}>
      {layout === 'phone' && (
        <div className="cal__top">
          <span className="cal__top-title">{T.calendar_title}</span>
          <button type="button" className="cal__close" aria-label={T.calendar_close} onClick={close}>
            <X size={20} strokeWidth={1.5} aria-hidden="true" />
          </button>
        </div>
      )}
      <div className="cal__scroll">
        <MonthGrid />
        <div className="cal__divider" />
        <DayList canComplete={canComplete} canDelete={canDelete} />
      </div>
      <MoveNotice />
      <DragGhost />
    </aside>
  );
  if (layout === 'mid') {
    return (
      <>
        <div className="cal-overlay" onMouseDown={close} />
        {panel}
      </>
    );
  }
  return panel;
}

