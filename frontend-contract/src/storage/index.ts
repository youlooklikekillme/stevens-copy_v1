/** localStorage — только здесь, каждое обращение в try/catch (03-behavior.md, раздел 7). */
const PREFIX = 'stevens:v1:';

function read(key: string): string | null {
  try {
    return localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

function write(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(PREFIX + key);
    else localStorage.setItem(PREFIX + key, value);
  } catch {
    /* хранилище недоступно — работаем без сохранения */
  }
}

/** Ключи другой версии (stevens:v0:… и т. п.) удаляются при старте */
export function dropOldVersions(): void {
  try {
    const stale: string[] = [];
    for (let i = 0; i < localStorage.length; i += 1) {
      const key = localStorage.key(i);
      if (key && key.startsWith('stevens:') && !key.startsWith(PREFIX)) stale.push(key);
    }
    stale.forEach((key) => localStorage.removeItem(key));
  } catch {
    /* нет хранилища */
  }
}

export const storage = {
  lastSeen: () => {
    const v = Number(read('last_seen'));
    return Number.isFinite(v) && v > 0 ? v : null;
  },
  setLastSeen: (at: number) => write('last_seen', String(at)),
  tourSeen: () => read('tour_seen') === '1',
  setTourSeen: () => write('tour_seen', '1'),
  briefingDay: () => read('briefing_day'),
  setBriefingDay: (day: string) => write('briefing_day', day),
  notifyDeclined: () => read('notify_declined') === '1',
  setNotifyDeclined: () => write('notify_declined', '1'),
  /** Меню «Уведомления: включить» снимает отказ */
  clearNotifyDeclined: () => write('notify_declined', null),
  notifyFired: (): string[] => {
    try {
      return JSON.parse(read('notify_fired') ?? '[]') as string[];
    } catch {
      return [];
    }
  },
  setNotifyFired: (keys: string[]) => write('notify_fired', JSON.stringify(keys.slice(-50))),
  wakeWord: () => read('wake_word') === 'on',
  setWakeWord: (on: boolean) => write('wake_word', on ? 'on' : null),
  clientId: () => read('client_id'),
  setClientId: (id: string) => write('client_id', id),
  mode: () => (read('mode') === 'sandbox' ? 'sandbox' : null),
  setMode: (mode: 'sandbox' | null) => write('mode', mode),
  token: () => read('token'),
  setToken: (token: string | null) => write('token', token),
  login: () => read('login'),
  setLogin: (login: string | null) => write('login', login),
  userId: () => {
    const v = read('user_id');
    return v ? Number(v) : null;
  },
  setUserId: (id: number | null) => write('user_id', id === null ? null : String(id)),
  feed: <T>(key: string): { savedAt: number; messages: T[] } | null => {
    const raw = read(`feed:${key}`);
    if (!raw) return null;
    try {
      return JSON.parse(raw) as { savedAt: number; messages: T[] };
    } catch {
      return null;
    }
  },
  setFeed: <T>(key: string, messages: T[] | null) =>
    write(`feed:${key}`, messages === null ? null : JSON.stringify({ savedAt: Date.now(), messages })),
};
