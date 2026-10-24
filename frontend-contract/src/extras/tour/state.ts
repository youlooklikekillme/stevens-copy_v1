/** Ф23 Обучение в демо-режиме: шаги идут по ответам Stevens (контракт v1.5, Ф23). */
import type { ChatResponse, RecordItem } from '../../api/types';
import { serverHas, type ServerFeature } from '../../config/server';
import { TOUR_STEPS } from '../../content/texts';
import { declineBriefing } from '../../state/briefing';
import { pressButton, resetSandbox, send } from '../../state/chat';
import { emitActivity, onAssistantResponse, onUiActivity } from '../../state/events';
import { notifyStore, showNotifyCard, viewNotifyCard } from '../../state/notify';
import { createStore, store } from '../../state/store';
import { storage } from '../../storage';

type Kind = ChatResponse['kind'];
export type TourStatus = 'off' | 'offer' | 'running' | 'done';

/**
 * Шаги обучения (контракт v1.5, Ф23): фича шага, флажок и какими ответами закрываются подсказки —
 * последний ответ закрывает шаг. Шаг 16 («Уведомление») и 17 («Календарь») закрывает сам фронт:
 * «Посмотреть» на карточке уведомления и перенос перетаскиванием в календаре.
 * Шаг выключенной на сервере фичи (нет в GET /api/health) не показывается.
 */
type StepRule = { feature: ServerFeature; on: boolean; expect: (Kind | 'notify-viewed' | 'calendar-moved')[] };
const RULES: StepRule[] = [
  { feature: 'f5', on: true, expect: ['created'] },
  { feature: 'f5', on: true, expect: ['created'] },
  { feature: 'f5', on: true, expect: ['created'] },
  { feature: 'f7', on: true, expect: ['list'] },
  { feature: 'f8', on: true, expect: ['found'] },
  { feature: 'f6', on: true, expect: ['clarify', 'created'] },
  { feature: 'f11', on: true, expect: ['conflict', 'clarify', 'created'] },
  { feature: 'f12', on: true, expect: ['completed'] },
  { feature: 'f12', on: true, expect: ['found', 'updated', 'deleted', 'created'] },
  { feature: 'f14', on: true, expect: ['confirm', 'created'] },
  { feature: 'f8', on: true, expect: ['found'] },
  { feature: 'f5', on: true, expect: ['created'] },
  { feature: 'f16', on: true, expect: ['clarify'] },
  { feature: 'f19', on: true, expect: ['person'] },
  { feature: 'f24', on: true, expect: ['created', 'found'] },
  { feature: 'f10', on: true, expect: ['notify-show', 'notify-viewed'] as StepRule['expect'] },
  { feature: 'f13', on: true, expect: ['calendar-open', 'calendar-moved'] as StepRule['expect'] },
];

export type TourStep = (typeof TOUR_STEPS)[number] & { expect: StepRule['expect'] };

/** Включённые шаги: флажок в коде и фича на сервере */
export function tourSteps(): TourStep[] {
  return TOUR_STEPS.flatMap((step, i) => {
    const rule = RULES[i];
    return rule && rule.on && serverHas(rule.feature) ? [{ ...step, expect: rule.expect }] : [];
  });
}

const STEP_DONE_MS = 1400;

export const tourStore = createStore({ status: 'off' as TourStatus, step: 0, sub: 0, stepDone: false, again: false });

let doneTimer: ReturnType<typeof setTimeout> | undefined;
/** Напоминание из шага «Напоминание» — его же показываем в примере уведомления */
let exampleRecord: RecordItem | null = null;

function next(): void {
  const { step } = tourStore.get();
  if (step + 1 >= tourSteps().length) tourStore.set({ status: 'done', stepDone: false });
  else tourStore.set({ step: step + 1, sub: 0, stepDone: false });
}

function onResponse(kind: string): void {
  const t = tourStore.get();
  if (t.status !== 'running' || t.stepDone) return;
  const step = tourSteps()[t.step];
  if (!step) return;
  const expect = step.expect as string[];
  // Ответ, которым заканчивается шаг, засчитываем, даже если человек сказал всё одной фразой
  if (kind === expect[expect.length - 1]) {
    tourStore.set({ stepDone: true, sub: expect.length - 1 });
    clearTimeout(doneTimer);
    doneTimer = setTimeout(next, STEP_DONE_MS);
    return;
  }
  const at = expect.indexOf(kind, t.sub);
  if (at >= 0) tourStore.set({ sub: Math.min(at + 1, expect.length - 1) });
}

/** Пример уведомления в шаге «Уведомление»: запись из шага «Напоминание», без ожидания */
function showExample(): void {
  const record: RecordItem = exampleRecord ?? {
    id: null, type: 'reminder', text: 'Позвонить Ивану', date: null, time: null, date_kind: 'none', status: 'open',
    created_at: null, repeat: null, demo: false, marks: [], highlight: null,
  };
  showNotifyCard(record);
  onResponse('notify-show');
}

export async function startTour(): Promise<void> {
  storage.setTourSeen();
  clearTimeout(doneTimer);
  declineBriefing();
  // С чистых демо-данных: иначе встречи из прошлого прохода сбили бы шаги про совпадение и правку
  await resetSandbox();
  exampleRecord = null;
  tourStore.set({ status: 'running', step: 0, sub: 0, stepDone: false });
}

export function declineTour(): void {
  storage.setTourSeen();
  dismissedFor = store.get().conversationId;
  entry = null;
  tourStore.set({ status: 'off' });
}

export function closeTour(): void {
  clearTimeout(doneTimer);
  tourStore.set({ status: 'off', stepDone: false });
}

export function skipStep(): void {
  clearTimeout(doneTimer);
  next();
}

/** Подсказка нажата: фраза уходит как текст, кнопка — нажимается под последним ответом */
export function sendHint(): void {
  const { step, sub } = tourStore.get();
  const subs = tourSteps()[step]?.subs;
  const hint = subs?.[Math.min(sub, subs.length - 1)];
  if (!hint) return;
  if ('say' in hint) {
    emitActivity('button-press');
    send(hint.say, 'text');
    return;
  }
  emitActivity('button-press');
  if (special(hint.press)) return;
  const found = lastButton(hint.press);
  if (found) pressButton(found.answerId, found.button, found.index);
}

/** Подсказки шагов, которые нажимают не кнопку под ответом: пример уведомления, «Посмотреть», календарь */
const SPECIAL = ['Показать пример', 'Посмотреть', 'Открыть календарь'];

function special(label: string): boolean {
  if (label === 'Показать пример') showExample();
  else if (label === 'Посмотреть') viewNotifyCard();
  else if (label === 'Открыть календарь') {
    store.set({ calendarOpen: true });
    onResponse('calendar-open');
  } else return false;
  return true;
}

/** Можно ли нажать подсказку-кнопку прямо сейчас */
export function hintReady(label: string): boolean {
  if (label === 'Посмотреть') return notifyStore.get().card !== null;
  if (SPECIAL.includes(label)) return true;
  return lastButton(label) !== null;
}

export function lastButton(label: string) {
  const messages = store.get().messages;
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const m = messages[i];
    if (m.kind !== 'answer') continue;
    const index = m.response.buttons.findIndex((b) => b.label === label);
    return index >= 0 && m.pressed === null ? { answerId: m.id, button: m.response.buttons[index], index } : null;
  }
  return null;
}

/** «Не сейчас» прячет предложение до следующего входа или нового диалога */
let dismissedFor: string | null = null;
/** Сколько сообщений человека было в диалоге при входе: плашка уходит с первым новым */
let entry: { conversationId: string; users: number } | null = null;

/**
 * Предложение над строкой ввода: в песочнице после входа или нового диалога, пока человек ничего не отправил.
 * Первый раз — «Показать, что я умею?», потом (новый диалог, сброс, повторный вход) — «Пройти обучение?»
 */
function maybeOffer(): void {
  const s = store.get();
  const status = tourStore.get().status;
  if (s.mode !== 'sandbox') {
    // Вышли из песочницы — обучение прячем
    if (status !== 'off') closeTour();
    return;
  }
  const users = s.messages.filter((m) => m.kind === 'user').length;
  // Вход в приложение (и перезагрузка) или новый диалог — отсчёт сообщений заново
  if (s.screen !== 'app') entry = null;
  else if (!entry || entry.conversationId !== s.conversationId) entry = { conversationId: s.conversationId, users };
  const fresh = !!entry && users <= entry.users && s.conversationId !== dismissedFor;
  if (status === 'off' && fresh) tourStore.set({ status: 'offer', again: storage.tourSeen() });
  else if (status === 'offer' && !fresh) tourStore.set({ status: 'off' });
}

export function init(): void {
  onAssistantResponse((response, request) => {
    if (request.action?.id === 'briefing') return;
    const t = tourStore.get();
    if (t.status === 'running' && response.kind === 'created') {
      exampleRecord = response.records.find((r) => r.type === 'reminder' && r.id !== null) ?? exampleRecord;
    }
    onResponse(response.kind);
  });
  onUiActivity((kind) => {
    if (kind === 'notify-viewed' || kind === 'calendar-moved') onResponse(kind);
  });
  store.subscribe(maybeOffer);
  maybeOffer();
}
