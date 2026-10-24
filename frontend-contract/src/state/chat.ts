/** Отправка, ответ, ошибки, «Повторить», «Новый диалог» — 03-behavior.md, разделы 2–4 и 10. */
import { ApiFailure, api, errorKey } from '../api';
import type { Action, Button, ChatRequest, RecordItem } from '../api/types';
import { T } from '../content/texts';
import { isoWithOffset, timezone } from '../lib/clock';
import { uuid } from '../lib/uuid';
import { emitActivity, emitRecordsChanged, emitResponse } from './events';
import { clearFeed } from './feed';
import { serverReady, sessionExpired } from './session';
import { store, type AnswerMessage, type FeedMessage } from './store';

const TIMEOUT = 25000;
const TIMEOUT_WAKING = 60000;

function replace(id: string, next: FeedMessage | ((m: FeedMessage) => FeedMessage)): void {
  store.set((s) => ({
    messages: s.messages.map((m) => (m.id === id ? (typeof next === 'function' ? next(m) : next) : m)),
  }));
}

async function run(request: ChatRequest, pendingId: string): Promise<void> {
  const waking = store.get().server !== 'ok';
  store.set({ waiting: true });
  emitActivity('request-start');
  const started = Date.now();
  try {
    if (waking && !(await serverReady())) throw new ApiFailure({ type: 'timeout' });
    const left = (waking ? TIMEOUT_WAKING : TIMEOUT) - (Date.now() - started);
    const response = await api.chat(request, Math.max(left, 1000));
    if (response.kind === 'error') {
      replace(pendingId, { kind: 'error', id: pendingId, text: response.text, request });
    } else {
      const answer: AnswerMessage = {
        kind: 'answer', id: pendingId, response, restored: false, pressed: null,
        voice: request.source === 'voice', edited: {}, flash: null,
      };
      replace(pendingId, answer);
      store.set({ waiting: false });
      emitActivity('request-end');
      emitResponse(response, request);
      return;
    }
  } catch (error) {
    if (error instanceof ApiFailure && error.error.type === 'unauthorized') {
      store.set({ waiting: false });
      sessionExpired();
      return;
    }
    replace(pendingId, { kind: 'error', id: pendingId, text: T[errorKey(error)], request });
  }
  store.set({ waiting: false });
  emitActivity('request-end');
}

/** Текст поля, голос, пример или кнопка ответа */
export function send(
  rawText: string,
  source: ChatRequest['source'],
  options: { action?: Action | null; fromId?: string; buttonIndex?: number; rawText?: string } = {},
): boolean {
  const state = store.get();
  if (state.waiting || state.recording) return false;
  const text = rawText.trim();
  if (!text) return false;
  const request: ChatRequest = {
    conversation_id: state.conversationId,
    text: text.slice(0, 300),
    source,
    action: options.action ?? null,
    client_time: isoWithOffset(),
    timezone: timezone(),
    ...(source === 'voice' && options.rawText ? { raw_text: options.rawText.slice(0, 600) } : {}),
  };
  const pendingId = uuid();
  store.set((s) => ({
    editing: null,
    messages: [
      ...s.messages.map((m) =>
        m.kind === 'answer' && m.id === options.fromId ? { ...m, pressed: options.buttonIndex ?? null } : m,
      ),
      { kind: 'user', id: uuid(), text: request.text, source },
      { kind: 'pending', id: pendingId, request, startedAt: Date.now() },
    ],
  }));
  void run(request, pendingId);
  return true;
}

/** Запрос без сообщения пользователя: утренний брифинг. false — сейчас занято */
export function sendQuiet(action: Action): boolean {
  const state = store.get();
  if (state.waiting || state.recording) return false;
  const request: ChatRequest = {
    conversation_id: state.conversationId,
    text: '',
    source: 'text',
    action,
    client_time: isoWithOffset(),
    timezone: timezone(),
  };
  const pendingId = uuid();
  store.set((s) => ({ messages: [...s.messages, { kind: 'pending', id: pendingId, request, startedAt: Date.now() }] }));
  void run(request, pendingId);
  return true;
}

export function pressButton(answerId: string, button: Button, index: number): void {
  emitActivity('button-press');
  if ('action' in button.send) send(button.label, 'button', { action: button.send.action, fromId: answerId, buttonIndex: index });
  else send(button.send.text, 'button', { fromId: answerId, buttonIndex: index });
}

/** Тот же запрос ещё раз, со свежим client_time; сообщение пользователя не дублируется */
export function retry(errorId: string): void {
  const state = store.get();
  const message = state.messages.find((m) => m.id === errorId);
  if (!message || message.kind !== 'error' || state.waiting) return;
  emitActivity('button-press');
  const request = { ...message.request, client_time: isoWithOffset() };
  replace(errorId, { kind: 'pending', id: errorId, request, startedAt: Date.now() });
  void run(request, errorId);
}

export function newDialog(): void {
  emitActivity('new-dialog');
  clearFeed();
  store.set({ messages: [], conversationId: uuid(), editing: null, unseen: 0 });
}

/** Сохранённая форма правки (Д10): карточка в ленте показывает новые данные */
export function applyEdit(messageId: string, record: RecordItem): void {
  if (record.id === null) return;
  const id = record.id;
  replace(messageId, (m) =>
    m.kind === 'answer' ? { ...m, edited: { ...m.edited, [id]: record }, flash: { id, at: Date.now() } } : m,
  );
}

let hintTimer: ReturnType<typeof setTimeout> | undefined;
/** Подсказка голоса в строке статуса: 6 с или до следующего действия */
export function setHint(key: keyof typeof T | null): void {
  clearTimeout(hintTimer);
  if (!key) {
    store.set({ hint: null });
    return;
  }
  store.set({ hint: { key, until: Date.now() + 6000 } });
  hintTimer = setTimeout(() => store.set({ hint: null }), 6000);
}

export async function resetSandbox(): Promise<void> {
  try {
    await api.resetSandbox();
    emitActivity('new-dialog');
    clearFeed();
    store.set({ messages: [], conversationId: uuid(), editing: null, unseen: 0 });
    emitRecordsChanged();
  } catch (error) {
    store.set({ resetError: { key: errorKey(error), until: Date.now() + 6000 } });
    setTimeout(() => store.set({ resetError: null }), 6000);
  }
}
