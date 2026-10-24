/** Режимы, запуск, вход и выход, пробуждение сервера — 03-behavior.md, разделы 1 и 5. */
import { api, ApiFailure, setModeHeaders } from '../api';
import { features } from '../config/features';
import { setServerFeatures } from '../config/server';
import { uuid } from '../lib/uuid';
import { dropOldVersions, storage } from '../storage';
import { emitActivity } from './events';
import { restoreFeed } from './feed';
import { store, type Mode } from './store';

const HEALTH_TIMEOUT = 60000;
const WAKING_AFTER = 1500;

let healthPromise: Promise<boolean> = Promise.resolve(true);

/** GET /api/health: нет ответа за 1,5 с — «Просыпаюсь», не дождались за 60 с — «Не удалось подключиться» */
export function checkHealth(): Promise<boolean> {
  const started = Date.now();
  const waking = setTimeout(() => {
    if (store.get().server !== 'down') store.set({ server: 'waking' });
  }, WAKING_AFTER);
  healthPromise = (async () => {
    while (Date.now() - started < HEALTH_TIMEOUT) {
      const left = HEALTH_TIMEOUT - (Date.now() - started);
      const health = await api.health(left);
      if (health) {
        clearTimeout(waking);
        setServerFeatures(health.features);
        store.set({ server: 'ok' });
        return true;
      }
      await new Promise((r) => setTimeout(r, Math.min(2000, Math.max(0, HEALTH_TIMEOUT - (Date.now() - started)))));
    }
    clearTimeout(waking);
    store.set({ server: 'down' });
    return false;
  })();
  return healthPromise;
}

/** Ждём сервер, если он просыпается. false — не проснулся */
export function serverReady(): Promise<boolean> {
  if (store.get().server === 'ok') return Promise.resolve(true);
  if (store.get().server === 'down') return checkHealth();
  return healthPromise;
}

function clientId(): string {
  let id = storage.clientId();
  if (!id) {
    id = uuid();
    storage.setClientId(id);
  }
  return id;
}

function enterApp(mode: Mode, login: string | null = null, userId: number | null = null): void {
  if (mode === 'account') setModeHeaders({ Authorization: `Bearer ${storage.token() ?? ''}` });
  else setModeHeaders({ 'X-Client-Id': clientId() });
  store.set({
    screen: 'app',
    mode,
    login,
    userId,
    authNotice: null,
    conversationId: uuid(),
    calendarOpen: typeof window !== 'undefined' && window.innerWidth >= 1200,
    editing: null,
    unseen: 0,
  });
  restoreFeed();
}

function showAuth(notice: 'expired' | null = null): void {
  setModeHeaders({});
  store.set({ screen: 'auth', mode: null, login: null, userId: null, authNotice: notice, messages: [], editing: null });
}

export async function boot(): Promise<void> {
  dropOldVersions();
  clientId();
  void checkHealth();
  if (!features.auth) {
    enterApp('sandbox');
    return;
  }
  const token = storage.token();
  if (token) {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        const user = await api.me(token, HEALTH_TIMEOUT);
        storage.setLogin(user.login);
        storage.setUserId(user.id);
        enterApp('account', user.login, user.id);
        return;
      } catch (error) {
        if (error instanceof ApiFailure && error.error.type === 'http' && error.error.status === 401) {
          clearAccount();
          showAuth('expired');
          return;
        }
        if (!(await serverReady())) break;
      }
    }
    showAuth();
    return;
  }
  if (storage.mode() === 'sandbox') {
    enterApp('sandbox');
    return;
  }
  showAuth();
}

function clearAccount(): void {
  storage.setToken(null);
  storage.setLogin(null);
  storage.setUserId(null);
}

export async function authenticate(kind: 'login' | 'register', login: string, password: string): Promise<void> {
  const result = await api.login(kind, login, password);
  storage.setToken(result.token);
  storage.setLogin(result.user.login);
  storage.setUserId(result.user.id);
  storage.setMode(null);
  enterApp('account', result.user.login, result.user.id);
}

export function chooseSandbox(): void {
  storage.setMode('sandbox');
  enterApp('sandbox');
}

/** 401 на любом запросе в аккаунте */
export function sessionExpired(): void {
  clearAccount();
  storage.setMode(null);
  showAuth('expired');
}

export function logout(): void {
  const token = storage.token();
  if (token) api.logout(token);
  clearAccount();
  storage.setMode(null);
  emitActivity('new-dialog');
  showAuth();
}

/** «Войти или зарегистрироваться» из песочницы: песочница и её лента остаются на устройстве */
export function goAuth(): void {
  storage.setMode(null);
  emitActivity('new-dialog');
  showAuth();
}
