/** Д15 Обучение в песочнице: шаги идут по ответам Stevens. 07-extras.md, раздел 11. */
import type { ChatResponse } from '../../api/types';
import { TOUR_STEPS } from '../../content/texts';
import { declineBriefing } from '../../state/briefing';
import { pressButton, resetSandbox, send } from '../../state/chat';
import { emitActivity, onAssistantResponse } from '../../state/events';
import { createStore, store } from '../../state/store';
import { storage } from '../../storage';

type Kind = ChatResponse['kind'];
export type TourStatus = 'off' | 'offer' | 'running' | 'done';

/** Каким ответом закрывается каждая подсказка шага; последний ответ шага закрывает и сам шаг */
const EXPECT: Kind[][] = [
  ['list'],
  ['created'],
  ['clarify', 'created'],
  ['conflict', 'clarify', 'created'],
  ['updated'],
  ['clarify'],
  ['confirm', 'created'],
  ['found'],
  ['found'],
  ['created', 'found'],
  ['created', 'completed'],
];
const STEP_DONE_MS = 1400;

export const tourStore = createStore({ status: 'off' as TourStatus, step: 0, sub: 0, stepDone: false, again: false });

let doneTimer: ReturnType<typeof setTimeout> | undefined;

function next(): void {
  const { step } = tourStore.get();
  if (step + 1 >= TOUR_STEPS.length) tourStore.set({ status: 'done', stepDone: false });
  else tourStore.set({ step: step + 1, sub: 0, stepDone: false });
}

function onResponse(kind: Kind): void {
  const t = tourStore.get();
  if (t.status !== 'running' || t.stepDone) return;
  const expect = EXPECT[t.step];
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

export async function startTour(): Promise<void> {
  storage.setTourSeen();
  clearTimeout(doneTimer);
  declineBriefing();
  // С чистых демо-данных: иначе встречи из прошлого прохода сбили бы шаги про совпадение и правку
  await resetSandbox();
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
  const hint = TOUR_STEPS[step].subs[sub];
  if ('say' in hint) {
    emitActivity('button-press');
    send(hint.say, 'text');
    return;
  }
  const found = lastButton(hint.press);
  if (found) pressButton(found.answerId, found.button, found.index);
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
    onResponse(response.kind);
  });
  store.subscribe(maybeOffer);
  maybeOffer();
}
