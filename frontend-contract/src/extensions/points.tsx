/**
 * Точки расширения — 09-extensions.md, раздел 2. Флаг проверяется только здесь;
 * код допа грузится динамическим импортом. Флаги выключены — точки пусты.
 */
import { lazy, Suspense, useSyncExternalStore, type ReactNode } from 'react';
import type { RecordItem } from '../api/types';
import { features } from '../config/features';
import { serverFeatures, serverHas, type ServerFeature } from '../config/server';
import { store, type AnswerMessage } from '../state/store';

const calendar = () => import('../extras/calendar');
const edit = () => import('../extras/edit');
const mascot = () => import('../extras/mascot');
const deleted = () => import('../extras/deleted-card');
const tour = () => import('../extras/tour');

const CalendarToggle = features.d6_calendar ? lazy(() => calendar().then((m) => ({ default: m.CalendarToggle }))) : null;
const CalendarPanel = features.d6_calendar ? lazy(() => calendar().then((m) => ({ default: m.CalendarPanel }))) : null;
const EditButton = features.d10_undo ? lazy(() => edit().then((m) => ({ default: m.EditButton }))) : null;
const FeedEditForm = features.d10_undo ? lazy(() => edit().then((m) => ({ default: m.FeedEditForm }))) : null;
const MascotPerch = features.d14_mascot ? lazy(() => mascot().then((m) => ({ default: m.MascotPerch }))) : null;
const MascotTitle = features.d14_mascot ? lazy(() => mascot().then((m) => ({ default: m.MascotTitle }))) : null;
const TourBar = features.d15_tour ? lazy(() => tour().then((m) => ({ default: m.TourBar }))) : null;
const DeletedLabel =
  features.d2_delete || features.d10_undo ? lazy(() => deleted().then((m) => ({ default: m.DeletedLabel }))) : null;

const wrap = (node: ReactNode) => <Suspense fallback={null}>{node}</Suspense>;

/** Фича включена на сервере (GET /api/health): нет — места на экране не занимает */
function useServer(key: ServerFeature): boolean {
  useSyncExternalStore(serverFeatures.subscribe, serverFeatures.get);
  return serverHas(key);
}

/** Д6 / Ф13: кнопка календаря в шапке */
export function HeaderRightStart() {
  const on = useServer('f13');
  return CalendarToggle && on ? wrap(<CalendarToggle />) : null;
}

/** Д6 / Ф13: панель календаря справа */
export function LayoutSidePanel() {
  const on = useServer('f13');
  return CalendarPanel && on ? wrap(<CalendarPanel canComplete={features.d3_complete} canDelete={features.d2_delete} />) : null;
}

/** Д10: «Исправить» первой в ряду кнопок ответа created с одной записью; активна только у последнего */
export function AnswerExtraButtons({ message, isLast }: { message: AnswerMessage; isLast: boolean }) {
  if (!EditButton) return null;
  const { response } = message;
  if (response.kind !== 'created' || response.records.length !== 1 || response.records[0].id === null) return null;
  if (response.records[0].marks.includes('deleted')) return null;
  return wrap(<EditButton message={message} isLast={isLast} />);
}

/**
 * Д10: в ответе created с несколькими записями (плотный график) любую карточку можно открыть
 * и поправить нажатием. undefined — карточка не нажимается
 */
export function cardEditHandler(message: AnswerMessage, record: RecordItem, active: boolean) {
  const { response } = message;
  if (!FeedEditForm || !active || response.kind !== 'created' || response.records.length < 2) return undefined;
  if (record.id === null || record.marks.includes('deleted')) return undefined;
  const id = record.id;
  return () => store.set({ editing: { messageId: message.id, recordId: id } });
}

/** Д10: форма правки на месте карточки в ленте. null — обычная карточка */
export function RecordCardOverride({ message, record, editing }: { message: AnswerMessage; record: RecordItem; editing: boolean }) {
  if (!FeedEditForm || !editing) return null;
  return wrap(<FeedEditForm message={message} record={record} />);
}

/** Состояние «Удалено» (Д2 или Д10): подпись в мете */
export function RecordCardState({ record }: { record: RecordItem }) {
  if (!DeletedLabel || !record.marks.includes('deleted')) return null;
  return wrap(<DeletedLabel />);
}

/** Д14: персонаж на строке ввода */
export function InputBarOverlay() {
  return MascotPerch ? wrap(<MascotPerch />) : null;
}

/** Д14: персонаж справа от «Stevens» на экране входа */
export function LoginTitleAside() {
  return MascotTitle ? wrap(<MascotTitle />) : null;
}

/** Д15: обучение в песочнице — над строкой статуса */
export function BottomTop() {
  return TourBar ? wrap(<TourBar />) : null;
}

/** Д15: «Пройти обучение» в меню песочницы */
export function sandboxMenuExtra(label: string): { label: string; onSelect: () => void }[] {
  return features.d15_tour && serverHas('f23') ? [{ label, onSelect: () => void import('../extras/tour/state').then((m) => m.startTour()) }] : [];
}
