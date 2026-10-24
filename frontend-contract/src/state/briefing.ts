/**
 * План на сегодня (брифинг) — по предложению, не сам. Предлагаем, если на сегодня есть дела,
 * а человек не заходил несколько часов: в пустом чате вместо примеров, в непустом — в конце ленты.
 * «Да» — действие briefing без сообщения пользователя, «Нет» — плашка уходит до следующего долгого перерыва.
 */
import { serverFeatures, serverHas } from '../config/server';
import { useSyncExternalStore } from 'react';
import { api } from '../api';
import { storage } from '../storage';
import { sendQuiet } from './chat';
import { createStore, store } from './store';

/** Сколько не заходил, чтобы предложить план */
const AWAY_MS = 3 * 60 * 60 * 1000;
/** Как часто отмечаем «был здесь», пока вкладка открыта */
const SEEN_EVERY_MS = 60 * 1000;

export const briefingStore = createStore<{ offer: { count: number } | null }>({ offer: null });

export function useBriefingOffer() {
  const offer = useSyncExternalStore(briefingStore.subscribe, briefingStore.get).offer;
  useSyncExternalStore(serverFeatures.subscribe, serverFeatures.get);
  return serverHas('f18') ? offer : null; // Ф18 выключен на сервере — плашки нет
}

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function markSeen(): void {
  storage.setLastSeen(Date.now());
}

let checking = false;

/** Был долгий перерыв — смотрим, есть ли дела на сегодня */
async function checkOffer(awaySince: number | null): Promise<void> {
  if (checking || (awaySince !== null && Date.now() - awaySince < AWAY_MS)) return;
  checking = true;
  try {
    const day = today();
    const records = await api.records(day, day);
    const count = records.filter((r) => r.status !== 'done' && !r.marks.includes('deleted')).length;
    if (count > 0) briefingStore.set({ offer: { count } });
  } catch {
    /* без предложения */
  } finally {
    checking = false;
  }
}

export function acceptBriefing(): void {
  if (sendQuiet({ id: 'briefing' })) briefingStore.set({ offer: null });
}

export function declineBriefing(): void {
  briefingStore.set({ offer: null });
}

export function initBriefing(): void {
  // Время прошлого визита берём до того, как отметим нынешний
  const previous = storage.lastSeen();
  let started = false;
  store.subscribe(() => {
    const s = store.get();
    if (started || s.screen !== 'app' || s.server !== 'ok') return;
    started = true;
    void checkOffer(previous);
    markSeen();
    setInterval(() => document.visibilityState === 'visible' && markSeen(), SEEN_EVERY_MS);
  });
  if (typeof document === 'undefined') return;
  let hiddenAt: number | null = null;
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') {
      hiddenAt = Date.now();
      markSeen();
    } else if (started) {
      void checkOffer(hiddenAt);
      markSeen();
    }
  });
}
