/** Перенос дела перетаскиванием: на день в сетке или вверх-вниз по списку дня — другое время. */
import { CircleAlert } from 'lucide-react';
import { useEffect, useRef, useSyncExternalStore, type PointerEvent as ReactPointerEvent } from 'react';
import { createPortal } from 'react-dom';
import { api, ApiFailure, errorKey } from '../../api';
import type { RecordItem } from '../../api/types';
import { Pill } from '../../components/Pill';
import { T, fill } from '../../content/texts';
import { shortDate } from '../../lib/dates';
import { emitActivity, emitRecordsChanged } from '../../state/events';
import { sessionExpired } from '../../state/session';
import { createStore } from '../../state/store';
import {
  ARROW_HOLD_MS, clashes, MOUSE_SLOP_PX, movePatch, shiftedTime, TOUCH_HOLD_MS, TOUCH_SLOP_PX,
} from './drag';
import { calendar, load, shiftMonth } from './state';

const NOTICE_MS = 6000;
/** Половина ширины карточки у пальца (max-width 260 в calendar.css) и поле 8 */
const GHOST_HALF = 138;

type Target = { kind: 'day'; date: string } | { kind: 'time'; time: string } | { kind: 'none' };
type Drag = { record: RecordItem; x: number; y: number; target: Target };
type Notice =
  | { kind: 'moved'; text: string; record: RecordItem; back: { date: string | null; time: string | null } }
  | { kind: 'clash'; text: string; record: RecordItem; date: string; time: string | null }
  | { kind: 'error'; text: string };

const dragging = createStore({ drag: null as Drag | null });
const notices = createStore({ notice: null as Notice | null });

let noticeTimer: ReturnType<typeof setTimeout> | undefined;
function showNotice(notice: Notice | null, autoHide = true) {
  clearTimeout(noticeTimer);
  notices.set({ notice });
  if (notice && autoHide) noticeTimer = setTimeout(() => notices.set({ notice: null }), NOTICE_MS);
}

/** Пока дело тащат пальцем, страница не прокручивается. Слушатель с самого начала: iOS решает при касании */
document.addEventListener('touchmove', (e) => {
  if (dragging.get().drag) e.preventDefault();
}, { passive: false });

/** Было ли перетаскивание — тогда клик по карточке не открывает правку */
let suppressClick = false;
export function takeSuppressedClick(): boolean {
  const value = suppressClick;
  suppressClick = false;
  return value;
}

function whenText(date: string, time: string | null): string {
  return time ? `${shortDate(date)}, ${time}` : shortDate(date);
}

async function save(record: RecordItem, date: string, time: string | null) {
  if (record.id === null) return;
  const id = record.id;
  const patch = movePatch(record, date, time);
  if (Object.keys(patch).length === 0) return;
  const swap = (d: string | null, t: string | null) =>
    calendar.set((c) => ({ records: c.records.map((r) => (r.id === id ? { ...r, date: d, time: t } : r)) }));
  swap(date, time);
  try {
    const saved = await api.patchRecord(id, patch);
    showNotice({ kind: 'moved', text: fill(T.calendar_moved, { when: whenText(date, time) }), record: saved,
      back: { date: record.date, time: record.time } });
    emitActivity('edit-saved');
    emitActivity('calendar-moved');
    emitRecordsChanged();
    void load();
  } catch (error) {
    swap(record.date, record.time);
    if (error instanceof ApiFailure && error.error.type === 'unauthorized') {
      sessionExpired();
      return;
    }
    showNotice({ kind: 'error', text: T[errorKey(error) === 'err_network' ? 'err_network' : 'err_server'] });
  }
}

function drop(record: RecordItem, target: Target) {
  if (target.kind === 'none' || !record.date) return;
  const date = target.kind === 'day' ? target.date : record.date;
  const time = target.kind === 'time' ? target.time : record.time;
  if (date === record.date && time === record.time) return;
  const busy = clashes(record, date, time, calendar.get().records);
  if (busy.length > 0 && time) {
    showNotice({ kind: 'clash', text: fill(T.calendar_move_clash, { time, text: busy[0].text }), record, date, time }, false);
    return;
  }
  void save(record, date, time);
}

function targetAt(x: number, y: number, record: RecordItem, startY: number): Target {
  const el = document.elementFromPoint(x, y);
  const day = el?.closest<HTMLElement>('[data-drop-day]');
  if (day?.dataset.dropDay) return { kind: 'day', date: day.dataset.dropDay };
  // Ниже сетки месяца (список дня и пустое место панели) — сдвиг времени
  const grid = el?.closest('.cal')?.querySelector('.cal__grid');
  if (grid && y > grid.getBoundingClientRect().bottom && record.time) {
    return { kind: 'time', time: shiftedTime(record.time, y - startY) };
  }
  return { kind: 'none' };
}

/** Начало перетаскивания: мышь — после сдвига, палец — после долгого нажатия */
export function startDrag(event: ReactPointerEvent, record: RecordItem) {
  if (record.id === null || !record.date || event.button !== 0) return;
  if ((event.target as HTMLElement).closest('.cal__check')) return;
  const touch = event.pointerType !== 'mouse';
  const startX = event.clientX;
  const startY = event.clientY;
  let active = false;
  let arrow: { el: Element; timer: ReturnType<typeof setTimeout> } | null = null;

  const activate = (x: number, y: number) => {
    active = true;
    suppressClick = true;
    document.body.classList.add('cal-dragging');
    if (touch) navigator.vibrate?.(10);
    dragging.set({ drag: { record, x, y, target: { kind: 'none' } } });
  };
  const hold = touch ? setTimeout(() => activate(startX, startY), TOUCH_HOLD_MS) : undefined;

  const watchArrow = (x: number, y: number) => {
    const el = document.elementFromPoint(x, y)?.closest('[data-drop-month]') ?? null;
    if (arrow?.el === el) return;
    if (arrow) clearTimeout(arrow.timer);
    arrow = el ? {
      el,
      timer: setTimeout(() => {
        shiftMonth(Number((el as HTMLElement).dataset.dropMonth));
        arrow = null;
      }, ARROW_HOLD_MS),
    } : null;
  };

  const move = (e: PointerEvent) => {
    const distance = Math.hypot(e.clientX - startX, e.clientY - startY);
    if (!active) {
      if (touch) {
        if (distance > TOUCH_SLOP_PX) finish(false);
        return;
      }
      if (distance < MOUSE_SLOP_PX) return;
      activate(e.clientX, e.clientY);
    }
    watchArrow(e.clientX, e.clientY);
    dragging.set({ drag: { record, x: e.clientX, y: e.clientY, target: targetAt(e.clientX, e.clientY, record, startY) } });
  };
  const blockMenu = (e: Event) => {
    if (touch) e.preventDefault();
  };
  const finish = (commit: boolean) => {
    clearTimeout(hold);
    if (arrow) clearTimeout(arrow.timer);
    window.removeEventListener('pointermove', move);
    window.removeEventListener('pointerup', up);
    window.removeEventListener('pointercancel', cancel);
    window.removeEventListener('contextmenu', blockMenu);
    document.body.classList.remove('cal-dragging');
    const target = dragging.get().drag?.target;
    dragging.set({ drag: null });
    if (!active) return;
    // Клик после отпускания приходит сразу; если не пришёл — сбросить флаг
    setTimeout(() => { suppressClick = false; }, 0);
    if (commit && target) drop(record, target);
  };
  const up = () => finish(true);
  const cancel = () => finish(false);

  window.addEventListener('pointermove', move);
  window.addEventListener('pointerup', up);
  window.addEventListener('pointercancel', cancel);
  window.addEventListener('contextmenu', blockMenu);
}

export function useDrag(): Drag | null {
  return useSyncExternalStore(dragging.subscribe, () => dragging.get().drag);
}

/** Карточка у пальца или курсора и куда она ляжет */
export function DragGhost() {
  const drag = useDrag();
  if (!drag) return null;
  const { record, target } = drag;
  const where = target.kind === 'day' ? whenText(target.date, record.time)
    : target.kind === 'time' && record.date ? whenText(record.date, target.time)
      : null;
  return createPortal(
    <div className="cal-ghost" style={{ left: Math.min(Math.max(drag.x, GHOST_HALF), window.innerWidth - GHOST_HALF), top: drag.y }} aria-hidden="true">
      <div className="cal-ghost__text">{record.text}</div>
      <div className={`cal-ghost__where ${where ? 'cal-ghost__where--on' : ''}`}>{where ?? T.calendar_drag_cancel}</div>
    </div>,
    document.body,
  );
}

/** Подсказка во время переноса и итог под календарём */
export function MoveNotice() {
  const drag = useDrag();
  const notice = useSyncExternalStore(notices.subscribe, () => notices.get().notice);
  const busy = useRef(false);
  useEffect(() => () => clearTimeout(noticeTimer), []);

  if (drag) return <div className="cal-notice cal-notice--hint" role="status">{T.calendar_drag_hint}</div>;
  if (!notice) return null;
  if (notice.kind === 'error') {
    return (
      <div className="cal-notice cal-notice--error" role="alert">
        <CircleAlert size={14} strokeWidth={1.5} aria-hidden="true" />
        {notice.text}
      </div>
    );
  }
  if (notice.kind === 'clash') {
    return (
      <div className="cal-notice" role="alert">
        <span className="cal-notice__text">{notice.text}</span>
        <span className="cal-notice__buttons">
          <Pill variant="secondary" onClick={() => showNotice(null)}>{T.edit_cancel}</Pill>
          <Pill onClick={() => {
            showNotice(null);
            void save(notice.record, notice.date, notice.time);
          }}>{T.calendar_move_confirm}</Pill>
        </span>
      </div>
    );
  }
  return (
    <div className="cal-notice" role="status">
      <span className="cal-notice__text">{notice.text}</span>
      <button
        type="button"
        className="cal__restore"
        onClick={() => {
          if (busy.current || !notice.back.date) return;
          busy.current = true;
          showNotice(null);
          void save(notice.record, notice.back.date, notice.back.time).finally(() => { busy.current = false; });
        }}
      >
        {T.calendar_move_undo}
      </button>
    </div>
  );
}
