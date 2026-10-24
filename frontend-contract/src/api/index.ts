/** Все запросы — только здесь (AGENTS.md, правило 5). Режим «настоящий бэк». */
import { apiUrl, features } from '../config/features';
import { parseChat, parseRecord } from './schema';
import type {
  AuthResponse, BackendFeature, ChatRequest, ChatResponse, RecordItem, RecordPatch, User,
} from './types';
import { FEATURES_HEADER } from './types';

export type ApiError =
  | { type: 'network' }
  | { type: 'timeout' }
  | { type: 'server' }
  | { type: 'unauthorized' }
  | { type: 'http'; status: number; code: string | null };

export class ApiFailure extends Error {
  constructor(public readonly error: ApiError) {
    super(error.type);
  }
}

/** Заголовок режима: Bearer в аккаунте, X-Client-Id в песочнице */
let modeHeaders: Record<string, string> = {};
export function setModeHeaders(headers: Record<string, string>): void {
  modeHeaders = headers;
}

const base = apiUrl.replace(/\/$/, '');

/** Допы, которым нужен бэк (09-extensions.md, правило 7) */
function backendFeatures(): BackendFeature[] {
  const list: BackendFeature[] = [];
  if (features.d2_delete) list.push('d2');
  if (features.d3_complete) list.push('d3');
  if (features.d10_undo) list.push('d10');
  return list;
}

type Options = {
  method?: string;
  body?: unknown;
  timeout?: number;
  auth?: boolean;
  extra?: Record<string, string>;
};

async function request(path: string, { method = 'GET', body, timeout = 25000, auth = true, extra }: Options): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  const headers: Record<string, string> = { ...(auth ? modeHeaders : {}), ...extra };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  let response: Response;
  try {
    response = await fetch(base + path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal,
    });
  } catch {
    throw new ApiFailure(controller.signal.aborted ? { type: 'timeout' } : { type: 'network' });
  } finally {
    clearTimeout(timer);
  }
  if (response.status === 401 && auth && 'Authorization' in modeHeaders) {
    throw new ApiFailure({ type: 'unauthorized' });
  }
  if (response.status >= 500) throw new ApiFailure({ type: 'server' });
  if (!response.ok) {
    let code: string | null = null;
    try {
      code = ((await response.json()) as { error?: string }).error ?? null;
    } catch {
      /* без тела */
    }
    throw new ApiFailure({ type: 'http', status: response.status, code });
  }
  return response;
}

async function json(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiFailure({ type: 'server' });
  }
}

export const api = {
  /** Жив ли сервер и какие фичи включены. null — не ответил */
  async health(timeout: number): Promise<{ features: string[] | null } | null> {
    try {
      const response = await request('/api/health', { auth: false, timeout });
      const data = (await json(response)) as { status?: string; features?: unknown };
      if (data.status !== 'ok') return null;
      const list = Array.isArray(data.features) ? data.features.filter((f): f is string => typeof f === 'string') : null;
      return { features: list };
    } catch {
      return null;
    }
  },

  /** Сбои сайта для журнала (Ф0): не мешают работе, ответа не ждём */
  log(event: 'mic_failed' | 'autolisten_failed' | 'speech_failed' | 'stt_fallback', detail: string): void {
    void request('/api/log', {
      method: 'POST', timeout: 10000, body: { event, detail: detail.slice(0, 2000), client_time: new Date().toISOString() },
    }).catch(() => undefined);
  },

  /** Распознавание на сервере (Ф4): аудио одной фразы → текст */
  async stt(audio: Blob): Promise<string> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25000);
    try {
      const response = await fetch(`${base}/api/stt`, {
        method: 'POST', body: audio, signal: controller.signal,
        headers: { ...modeHeaders, 'Content-Type': audio.type.split(';')[0] || 'audio/webm' },
      });
      if (!response.ok) throw new ApiFailure({ type: 'server' });
      const text = ((await response.json()) as { text?: unknown }).text;
      return typeof text === 'string' ? text : '';
    } catch (error) {
      throw error instanceof ApiFailure ? error : new ApiFailure({ type: 'network' });
    } finally {
      clearTimeout(timer);
    }
  },

  /** Ответ не прошёл схему: есть text — ответ из одного текста, нет — err_server */
  async chat(body: ChatRequest, timeout: number): Promise<ChatResponse> {
    const list = backendFeatures();
    const response = await request('/api/chat', {
      method: 'POST',
      body,
      timeout,
      extra: list.length ? { [FEATURES_HEADER]: list.join(',') } : undefined,
    });
    const data = await json(response);
    const parsed = parseChat(data);
    if (parsed) return parsed;
    const text = (data as { text?: unknown } | null)?.text;
    if (typeof text === 'string') {
      return {
        message_id: '', kind: 'unknown', text, speech: null, records: [], nearest: null,
        total: null, awaiting_reply: false, buttons: [],
      };
    }
    throw new ApiFailure({ type: 'server' });
  },

  async login(kind: 'login' | 'register', login: string, password: string): Promise<AuthResponse> {
    const response = await request(`/api/auth/${kind}`, { method: 'POST', body: { login, password }, auth: false });
    return (await json(response)) as AuthResponse;
  },

  async me(token: string, timeout: number): Promise<User> {
    const response = await request('/api/auth/me', {
      auth: false, timeout, extra: { Authorization: `Bearer ${token}` },
    });
    return ((await json(response)) as { user: User }).user;
  },

  logout(token: string): void {
    void request('/api/auth/logout', {
      method: 'POST', auth: false, extra: { Authorization: `Bearer ${token}` },
    }).catch(() => undefined);
  },

  /** Ключ сервера для пуш-подписки (applicationServerKey) */
  async pushKey(): Promise<string> {
    const response = await request('/api/push/key', { auth: false });
    const key = ((await json(response)) as { key?: unknown }).key;
    if (typeof key !== 'string') throw new ApiFailure({ type: 'server' });
    return key;
  },

  async pushSubscribe(subscription: PushSubscriptionJSON, timezone: string): Promise<void> {
    await request('/api/push/subscribe', { method: 'POST', body: { ...subscription, timezone } });
  },

  async pushUnsubscribe(endpoint: string): Promise<void> {
    await request('/api/push/unsubscribe', { method: 'POST', body: { endpoint }, auth: false });
  },

  async pushSnooze(endpoint: string, recordId: number): Promise<void> {
    await request('/api/push/snooze', { method: 'POST', body: { endpoint, record_id: recordId }, auth: false });
  },

  async resetSandbox(): Promise<void> {
    await request('/api/sandbox/reset', { method: 'POST' });
  },

  async records(from: string, to: string): Promise<RecordItem[]> {
    const response = await request(`/api/records?from=${from}&to=${to}`, {});
    const data = (await json(response)) as { records?: unknown[] };
    return (data.records ?? []).map(parseRecord).filter((r): r is RecordItem => r !== null);
  },

  async patchRecord(id: number, patch: RecordPatch): Promise<RecordItem> {
    const response = await request(`/api/records/${id}`, { method: 'PATCH', body: patch });
    const record = parseRecord(((await json(response)) as { record?: unknown }).record);
    if (!record) throw new ApiFailure({ type: 'server' });
    return record;
  },

  async deleteRecord(id: number): Promise<void> {
    await request(`/api/records/${id}`, { method: 'DELETE' });
  },

  async restoreRecord(id: number): Promise<RecordItem> {
    const response = await request(`/api/records/${id}/restore`, { method: 'POST' });
    const record = parseRecord(((await json(response)) as { record?: unknown }).record);
    if (!record) throw new ApiFailure({ type: 'server' });
    return record;
  },
};

/** Текст ошибки запроса для пользователя — ключ texts.ts */
export function errorKey(error: unknown): 'err_network' | 'err_timeout' | 'err_server' {
  if (error instanceof ApiFailure) {
    if (error.error.type === 'network') return 'err_network';
    if (error.error.type === 'timeout') return 'err_timeout';
  }
  return 'err_server';
}
