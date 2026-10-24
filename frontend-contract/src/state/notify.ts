/**
 * Уведомления о напоминаниях и встречах.
 * После первого созданного напоминания под ответом — плашка «Прислать уведомление в 10:00?».
 * Напоминание приходит в своё время, встреча — за 10 минут.
 * Как в мессенджере: человек не на вкладке — пуш (с сервера через public/sw.js, а если вкладка
 * открыта в фоне — ещё и отсюда, с тем же tag, браузер склеит), на вкладке — пуша нет,
 * только сообщение Стивенса в ленте и тихий звук двумя нотами.
 * Вернулся на вкладку или открыл по пушу — в ленте сообщение Стивенса о том, что подошло без него.
 * Звонят только записи, заведённые человеком: демо-записи песочницы помечены demo.
 * У пуша кнопки «Посмотреть» (Стивенс присылает запись в чат) и «Через 10 минут» — см. public/sw.js.
 */
import { api } from '../api';
import type { RecordItem } from '../api/types';
import { T } from '../content/texts';
import { timezone } from '../lib/clock';
import { uuid } from '../lib/uuid';
import { storage } from '../storage';
import { onAssistantResponse, onRecordsChanged } from './events';
import { createStore, lastAnswerId, store } from './store';

type PushMessage = { type?: string; recordId?: number; snoozed?: boolean };

export const notifyStore = createStore<{ offer: { messageId: string; time: string } | null }>({ offer: null });

const MEETING_EARLY_MIN = 10;
const SNOOZE_MS = 10 * 60 * 1000;
const RESCAN_MS = 30 * 60 * 1000;
/** Насколько назад смотреть пропущенное при открытии */
const MISSED_MAX_MS = 12 * 60 * 60 * 1000;
const timers = new Map<string, ReturnType<typeof setTimeout>>();

const supported = () => typeof window !== 'undefined' && 'Notification' in window;
export const notifyGranted = () => supported() && Notification.permission === 'granted';

function isoDay(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fireAt(record: RecordItem): number | null {
  if (!record.date || !record.time || record.demo || record.status === 'done' || record.type === 'task'
    || record.type === 'busy') return null;
  if (record.marks.includes('deleted')) return null;
  const [h, m] = record.time.split(':').map(Number);
  const [y, mo, d] = record.date.split('-').map(Number);
  const at = new Date(y, mo - 1, d, h, m).getTime();
  return record.type === 'meeting' ? at - MEETING_EARLY_MIN * 60 * 1000 : at;
}

function chime(): void {
  try {
    const audio = new AudioContext();
    [660, 880].forEach((freq, i) => {
      const osc = audio.createOscillator();
      const gain = audio.createGain();
      const start = audio.currentTime + i * 0.18;
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, start);
      gain.gain.exponentialRampToValueAtTime(0.12, start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, start + 0.5);
      osc.connect(gain).connect(audio.destination);
      osc.start(start);
      osc.stop(start + 0.55);
    });
    setTimeout(() => void audio.close().catch(() => undefined), 1200);
  } catch {
    /* без звука */
  }
}

const keyOf = (record: RecordItem) => `${record.id}@${record.date}T${record.time}`;

/** Системное уведомление через service worker: так оно не пропадает и склеивается с пушем */
async function systemNotify(body: string, tag: string, recordId: number): Promise<void> {
  const options = {
    body, tag, icon: '/icon-192.png', badge: '/icon-192.png', requireInteraction: true, renotify: true,
    data: { recordId },
    // Кнопки обрабатывает public/sw.js; TypeScript о них пока не знает
    actions: [{ action: 'view', title: 'Посмотреть' }, { action: 'snooze', title: 'Через 10 минут' }],
  } as NotificationOptions;
  try {
    const registration = await navigator.serviceWorker?.getRegistration();
    if (registration) {
      await registration.showNotification('Stevens', options);
      return;
    }
  } catch {
    /* попробуем напрямую */
  }
  try {
    // Без service worker кнопок не бывает — только клик
    const n = new Notification('Stevens', { ...options, actions: undefined } as NotificationOptions);
    n.onclick = () => { window.focus(); n.close(); };
  } catch {
    /* браузер не дал показать */
  }
}

/** Сообщение Стивенса в ленте с карточками записей */
function remindInFeed(records: RecordItem[], text: string): void {
  if (store.get().screen !== 'app') return;
  store.set((s) => ({
    messages: [...s.messages, {
      kind: 'answer', id: uuid(), restored: false, pressed: null, voice: false, edited: {}, flash: null,
      response: {
        message_id: '', kind: 'reminder', text, speech: null, records, nearest: null,
        total: null, awaiting_reply: false, buttons: [],
      },
    }],
  }));
}

function fire(record: RecordItem, key: string): void {
  const fired = storage.notifyFired();
  if (fired.includes(key)) return;
  storage.setNotifyFired([...fired, key]);
  const body = record.type === 'meeting' ? `Через ${MEETING_EARLY_MIN} минут: ${record.text}` : record.text;
  remindInFeed([record], record.type === 'meeting' ? T.remind_meeting : T.remind_one);
  if (document.visibilityState === 'visible') chime();
  else if (notifyGranted() && record.id !== null) void systemNotify(body, key, record.id);
}

async function findRecord(id: number): Promise<RecordItem | null> {
  const now = Date.now();
  try {
    const records = await api.records(isoDay(new Date(now - 24 * 60 * 60 * 1000)), isoDay(new Date(now + 24 * 60 * 60 * 1000)));
    return records.find((r) => r.id === id) ?? null;
  } catch {
    return null;
  }
}

/** «Посмотреть» в пуше: Стивенс присылает запись в чат (если только что не прислал её сам) */
async function showRecord(id: number, ring = false): Promise<void> {
  const record = await findRecord(id);
  if (!record) return;
  const last = store.get().messages.at(-1);
  if (last?.kind === 'answer' && last.response.kind === 'reminder' && last.response.records.some((r) => r.id === id)) return;
  const key = keyOf(record);
  const fired = storage.notifyFired();
  if (!fired.includes(key)) storage.setNotifyFired([...fired, key]);
  remindInFeed([record], record.type === 'meeting' && !ring ? T.remind_meeting : T.remind_one);
  if (ring) chime();
}

/** «Через 10 минут», когда пуша с сервера нет: откладывает сама вкладка */
function snoozeHere(id: number): void {
  setTimeout(() => {
    void findRecord(id).then((record) => {
      if (!record || record.status === 'done' || record.marks.includes('deleted')) return;
      remindInFeed([record], T.remind_one);
      if (document.visibilityState === 'visible') chime();
      else if (notifyGranted()) void systemNotify(record.text, keyOf(record), id);
    });
  }, SNOOZE_MS);
}

/** Подписка на пуш с сервера: нужен service worker и разрешение на уведомления */
async function subscribePush(): Promise<void> {
  if (!notifyGranted() || !('serviceWorker' in navigator) || !('PushManager' in window)) return;
  try {
    const registration = await navigator.serviceWorker.register('/sw.js');
    await navigator.serviceWorker.ready;
    let subscription = await registration.pushManager.getSubscription();
    if (!subscription) {
      const key = await api.pushKey();
      subscription = await registration.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToBytes(key),
      });
    }
    await api.pushSubscribe(subscription.toJSON(), timezone());
  } catch {
    /* без пушей: остаются уведомления, пока вкладка открыта */
  }
}

function urlBase64ToBytes(value: string): Uint8Array<ArrayBuffer> {
  const base64 = (value + '='.repeat((4 - (value.length % 4)) % 4)).replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(base64);
  const bytes = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i += 1) bytes[i] = raw.charCodeAt(i);
  return bytes;
}

/** Что подошло, пока человека не было: одно сообщение Стивенса при открытии */
async function remindMissed(since: number | null, away = true): Promise<void> {
  if (since === null) return;
  const from = Math.max(since, Date.now() - MISSED_MAX_MS);
  const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000);
  let records: RecordItem[];
  try {
    records = await api.records(isoDay(yesterday), isoDay(new Date()));
  } catch {
    return;
  }
  const fired = storage.notifyFired();
  const missed = records.filter((r) => {
    const at = fireAt(r);
    return at !== null && r.id !== null && at > from && at <= Date.now() && !fired.includes(keyOf(r));
  });
  if (!missed.length) return;
  storage.setNotifyFired([...fired, ...missed.map(keyOf)]);
  if (away) {
    remindInFeed(missed, T.remind_missed);
    return;
  }
  remindInFeed(missed, missed.length === 1 && missed[0].type === 'meeting' ? T.remind_meeting : T.remind_one);
  chime();
}

/** Перестроить таймеры на сегодня и завтра */
export async function reschedule(): Promise<void> {
  if (store.get().screen !== 'app') return;
  const now = new Date();
  const tomorrow = new Date(now.getTime() + 24 * 60 * 60 * 1000);
  let records: RecordItem[];
  try {
    records = await api.records(isoDay(now), isoDay(tomorrow));
  } catch {
    return;
  }
  timers.forEach((t) => clearTimeout(t));
  timers.clear();
  const fired = storage.notifyFired();
  for (const record of records) {
    const at = fireAt(record);
    if (at === null || record.id === null) continue;
    const key = keyOf(record);
    const wait = at - Date.now();
    if (wait < -60 * 1000 || wait > 25 * 60 * 60 * 1000 || fired.includes(key)) continue;
    timers.set(key, setTimeout(() => fire(record, key), Math.max(0, wait)));
  }
}

export async function acceptOffer(): Promise<void> {
  notifyStore.set({ offer: null });
  if (!supported()) return;
  const permission = await Notification.requestPermission();
  if (permission === 'granted') {
    void reschedule();
    void subscribePush();
  } else storage.setNotifyDeclined();
}

export function declineOffer(): void {
  notifyStore.set({ offer: null });
  storage.setNotifyDeclined();
}

export function initNotify(): void {
  // Время прошлого визита — до того, как его отметит брифинг
  const previous = storage.lastSeen();
  onAssistantResponse((response) => {
    // Перенос, удаление и отметка фразой тоже меняют, когда звонить
    if (response.kind === 'updated' || response.kind === 'deleted' || response.kind === 'completed') {
      void reschedule();
      return;
    }
    if (response.kind !== 'created') return;
    void reschedule();
    if (notifyGranted()) return;
    if (!supported() || Notification.permission !== 'default' || storage.notifyDeclined()) return;
    const record = response.records.find((r) => fireAt(r) !== null && (fireAt(r) ?? 0) > Date.now());
    const messageId = lastAnswerId(store.get().messages);
    if (record?.time && messageId) notifyStore.set({ offer: { messageId, time: record.time } });
  });
  onRecordsChanged(() => void reschedule());
  store.subscribe(() => {
    const offer = notifyStore.get().offer;
    if (offer && lastAnswerId(store.get().messages) !== offer.messageId) notifyStore.set({ offer: null });
  });
  if (typeof document !== 'undefined') {
    let hiddenAt: number | null = null;
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'hidden') {
        hiddenAt = Date.now();
        return;
      }
      // Пока вкладка была в фоне, таймеры могли проспать (сон компьютера) — досказываем
      void remindMissed(hiddenAt).then(() => reschedule());
    });
  }
  setInterval(() => void reschedule(), RESCAN_MS);
  // Пуш пришёл, когда человек на вкладке: пуша не было, показываем сообщение (если таймер его ещё не показал)
  navigator.serviceWorker?.addEventListener('message', (event: MessageEvent<PushMessage>) => {
    const data = event.data ?? {};
    const id = typeof data.recordId === 'number' ? data.recordId : null;
    if (data.type === 'stevens-reminder' && data.snoozed && id !== null) void showRecord(id, true);
    else if (data.type === 'stevens-reminder') void remindMissed(Date.now() - 15 * 60 * 1000, false);
    else if (data.type === 'stevens-open' && id !== null) void showRecord(id);
    else if (data.type === 'stevens-snooze' && id !== null) snoozeHere(id);
  });
  // Приложение открыли кнопкой «Посмотреть» из пуша: /?remind=<номер записи>
  const remindParam = typeof location !== 'undefined' ? new URLSearchParams(location.search).get('remind') : null;
  let remindId = remindParam && /^\d+$/.test(remindParam) ? Number(remindParam) : null;
  if (remindParam !== null) {
    const url = new URL(location.href);
    url.searchParams.delete('remind');
    history.replaceState(history.state, '', url);
  }
  let booted = false;
  let owner: string | null = null;
  store.subscribe(() => {
    const { screen, mode, userId } = store.get();
    if (screen !== 'app') return;
    if (!booted) {
      booted = true;
      const id = remindId;
      remindId = null;
      void remindMissed(previous).then(() => (id === null ? undefined : showRecord(id)));
    }
    // Вошли в аккаунт или вернулись в песочницу — пуши и таймеры переезжают к новому владельцу
    if (owner !== `${mode}:${userId}`) {
      owner = `${mode}:${userId}`;
      void reschedule();
      void subscribePush();
    }
  });
}
