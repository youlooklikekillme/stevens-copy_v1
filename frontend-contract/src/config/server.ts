/**
 * Что включено на сервере (контракт v1.5, 1.1): список из GET /api/health.
 * Фичи, которой нет в списке, на экране нет совсем: ни кнопки, ни пункта меню, ни шага обучения.
 * Список держим до перезагрузки. Сервер не прислал список (старая версия) — считаем, что есть всё.
 */
import { createStore } from '../state/store';

export type ServerFeature = `f${number}` | 'stt';

export const serverFeatures = createStore<{ list: string[] | null }>({ list: null });

export function setServerFeatures(list: string[] | null): void {
  if (list === null && serverFeatures.get().list !== null) return;
  serverFeatures.set({ list });
}

export function serverHas(key: ServerFeature): boolean {
  const { list } = serverFeatures.get();
  if (key === 'stt') return !!list?.includes('stt');
  return list === null || list.includes(key);
}
