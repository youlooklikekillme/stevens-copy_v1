/**
 * Типы API Stevens — по контракту фронт ↔ бэк v1.3 (первый вариант) и v1.4 с допами
 * (docs/reference/contract_v1.3.pdf, contract_v1.4_s_dopami.pdf).
 * Вход и песочница (раздел 14) и допы (раздел 15) — предложение, ждёт подтверждения бэка.
 * Источник правды — контракт. Расходится с контрактом — прав контракт, этот файл поправить.
 * Для проверки ответов в рантайме по этим типам строится схема (zod), см. docs/03-behavior.md.
 */

// ── Общие ─────────────────────────────────────────────

/** "YYYY-MM-DD", местная дата пользователя */
export type LocalDate = string;
/** "HH:MM", 24 часа, с ведущим нулём */
export type LocalTime = string;
/** ISO 8601 со смещением: "2026-10-18T14:05:00+03:00" */
export type IsoMoment = string;

// ── Запрос POST /api/chat ─────────────────────────────

export type MessageSource = 'voice' | 'text' | 'button';

export interface ChatRequest {
  /** UUID, создаёт фронт; новый — при открытии приложения, «Новом диалоге», входе, выходе, сбросе песочницы (03-behavior, раздел 7) */
  conversation_id: string;
  /** До 300 символов. Для кнопки — send.text или label */
  text: string;
  source: MessageSource;
  /** Только для кнопки-действия */
  action: Action | null;
  client_time: IsoMoment;
  /** IANA, например "Europe/Moscow" — Intl.DateTimeFormat().resolvedOptions().timeZone */
  timezone: string;
  /** Голос: что распознал браузер до поправок ослышки — только для журнала (контракт v1.5, Ф4) */
  raw_text?: string | null;
}

// ── Ответ POST /api/chat ──────────────────────────────

export type ResponseKind =
  | 'created'
  | 'list'
  | 'found'
  | 'clarify'
  | 'confirm'
  | 'conflict'
  | 'cancelled'
  | 'not_understood'
  | 'off_topic'
  | 'help'
  | 'thanks'
  | 'error'
  // допы (контракт v1.4, раздел 15.3): deleted — Д2 и «Отменить» Д10, completed — Д3
  | 'deleted'
  | 'completed'
  // резерв: Д1 (изменение фразой) не делаем, бэк его не присылает; если придёт — рисовать по общим полям
  | 'updated'
  // досье на человека: «что у меня с Иваном?» — записи сначала впереди, потом прошлые
  | 'person';

export interface ChatResponse {
  message_id: string;
  /** Незнакомый kind не ошибка: показываем по общим полям */
  kind: ResponseKind | (string & {});
  text: string;
  /** Для озвучки (доп Д12). null — озвучивать text */
  speech: string | null;
  records: RecordItem[];
  /** Только у пустого list: ближайшая будущая запись */
  nearest: RecordItem | null;
  /** list — число записей периода; found — всего совпадений (records может быть меньше) */
  total: number | null;
  /** Ассистент задал вопрос. Отвечать необязательно */
  awaiting_reply: boolean;
  buttons: Button[];
}

export type RecordType = 'task' | 'reminder' | 'meeting' | 'note' | 'busy';
export type DateKind = 'exact' | 'deadline' | 'none';
/** deleted — допы Д2/Д10: запись удалена (карточка «Удалено») */
export type RecordMark = 'new' | 'draft' | 'conflict' | 'deleted';
export type HighlightField = 'date' | 'time' | 'text';

export interface RecordItem {
  /** null — ещё не сохранена (черновик в confirm / conflict) */
  id: number | null;
  /** null — тип неизвестен (записи из CSV): карточка без значка и слова типа */
  type: RecordType | null;
  text: string;
  date: LocalDate | null;
  time: LocalTime | null;
  /** До скольки: у занятости всегда, у встречи — если сказали «с … до …» */
  end_time?: LocalTime | null;
  date_kind: DateKind;
  status: 'open' | 'done' | null;
  created_at: IsoMoment | null;
  /** Повтор по дням недели (пн = 0) и последний день; null — запись не повторяется */
  repeat: Repeat | null;
  /** Демо-запись песочницы из CSV: уведомления по ней не звонят */
  demo: boolean;
  marks: RecordMark[];
  highlight: HighlightField | null;
  /** Только у встреч: за сколько минут прислать уведомление, по умолчанию 15 (контракт v1.5, Ф10) */
  remind_before?: number | null;
}

export interface Repeat {
  weekdays: number[];
  until: LocalDate | null;
}

export interface Button {
  label: string;
  /** Ровно одно из двух */
  send: { text: string } | { action: Action };
  /** По умолчанию primary */
  style?: 'primary' | 'secondary';
}

export type ActionId =
  | 'cancel'
  | 'confirm_yes'
  | 'confirm_no'
  | 'keep_both'
  | 'change_new_time'
  | 'move_existing'
  // допы (docs/07-extras.md):
  | 'undo'
  | 'restore'
  | 'complete'
  | 'delete';

export interface Action {
  id: ActionId | (string & {});
  record_id?: number;
}

// ── GET /api/health ───────────────────────────────────

export interface HealthResponse {
  status: 'ok';
  /** Включённые фичи: «f0»…«f25» и «stt» (контракт v1.5, 1.1). Нет поля — старый сервер, считаем, что есть всё */
  features?: string[];
}

// ── Вход и песочница (раздел 14 контракта v1.3, ждёт подтверждения бэка) ──
//
// Каждый запрос, кроме /api/health и /api/auth/register|login, несёт ОДИН из заголовков:
//   Authorization: Bearer <token>   — аккаунт
//   X-Client-Id: <uuid>             — песочница (своя копия демо-данных на устройство)
// Если пришли оба — бэк использует Authorization.
// 401 на любом запросе с токеном — токен недействителен: фронт выходит на экран входа.

export interface AuthRequest {
  login: string;
  password: string;
}

export interface User {
  id: number;
  login: string;
}

/** POST /api/auth/register и POST /api/auth/login → 200 */
export interface AuthResponse {
  token: string;
  user: User;
}

/** Ошибки авторизации: 401 invalid_credentials, 409 login_taken, 422 invalid_login | invalid_password */
export interface AuthError {
  error: 'invalid_credentials' | 'login_taken' | 'invalid_login' | 'invalid_password';
}

/** GET /api/auth/me → 200 { user } | 401 */
export interface MeResponse {
  user: User;
}

// POST /api/auth/logout → 204 (токен перестаёт действовать)
// POST /api/sandbox/reset (с X-Client-Id) → 204 (демо-данные этого client_id копируются заново)

// ── Допы: записи для календаря и правки (контракт v1.4, раздел 15.5; docs/07-extras.md, раздел 9) ──

/** GET /api/records?from=YYYY-MM-DD&to=YYYY-MM-DD → без удалённых, по дате и времени */
export interface RecordsResponse {
  records: RecordItem[];
}

/** PATCH /api/records/{id} — только изменённые поля */
export interface RecordPatch {
  text?: string;
  date?: LocalDate | null;
  time?: LocalTime | null;
  end_time?: LocalTime | null;
  status?: 'open' | 'done';
  /** Только у встречи: 0–1440 минут */
  remind_before?: number;
}

/** PATCH /api/records/{id} и POST /api/records/{id}/restore → 200 */
export interface RecordResponse {
  record: RecordItem;
}

// DELETE /api/records/{id} → 204 (мягкое удаление)

/** Заголовок POST /api/chat со включёнными допами, которым нужен бэк. Не слать, если список пуст */
export type BackendFeature = 'd2' | 'd3' | 'd10';
export const FEATURES_HEADER = 'X-Stevens-Features';
