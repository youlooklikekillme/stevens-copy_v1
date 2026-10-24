/** События для допов (09-extensions.md, раздел 2): onAssistantResponse и onUiActivity. */
import type { ChatRequest, ChatResponse } from '../api/types';

export type UiActivity =
  | 'typing'
  | 'recording-start'
  | 'recording-stop'
  | 'request-start'
  | 'request-end'
  | 'button-press'
  | 'new-dialog'
  | 'tab-hidden'
  /** Услышали «Стивенс» — запись началась без кнопки */
  | 'wake'
  /** Сохранена форма «Исправить» в ленте (Д10) — персонаж «готово» */
  | 'edit-saved'
  /** Запись перенесли перетаскиванием в календаре (Ф13) — шаг обучения «Календарь» */
  | 'calendar-moved'
  /** Нажали «Посмотреть» на карточке уведомления (Ф10) — шаг обучения «Уведомление» */
  | 'notify-viewed';

type ResponseListener = (response: ChatResponse, request: ChatRequest) => void;
type ActivityListener = (kind: UiActivity) => void;

const responseListeners = new Set<ResponseListener>();
const activityListeners = new Set<ActivityListener>();

export function onAssistantResponse(listener: ResponseListener): () => void {
  responseListeners.add(listener);
  return () => responseListeners.delete(listener);
}

export function onUiActivity(listener: ActivityListener): () => void {
  activityListeners.add(listener);
  return () => activityListeners.delete(listener);
}

export function emitResponse(response: ChatResponse, request: ChatRequest): void {
  // Ошибка в допе (озвучка, персонаж) не должна превращать готовый ответ в «сбой на сервере»
  responseListeners.forEach((listener) => {
    try {
      listener(response, request);
    } catch (error) {
      console.error(error);
    }
  });
}

export function emitActivity(kind: UiActivity): void {
  activityListeners.forEach((listener) => listener(kind));
}

/** Календарь перечитывается после своих правок в ленте (Д10) */
const recordsChangedListeners = new Set<() => void>();
export function onRecordsChanged(listener: () => void): () => void {
  recordsChangedListeners.add(listener);
  return () => recordsChangedListeners.delete(listener);
}
export function emitRecordsChanged(): void {
  recordsChangedListeners.forEach((listener) => listener());
}

/** Озвучка (Д12) для автослушания (Д13): start — начали говорить, end — договорили, cancelled — оборвали */
export type SpeechPhase = 'start' | 'end' | 'cancelled';
const speechListeners = new Set<(phase: SpeechPhase) => void>();
let speaking = false;
let speakingText = '';
export function isSpeaking(): boolean {
  return speaking;
}
/** Что Стивенс сейчас говорит вслух — чтобы не принять его голос из динамиков за человека */
export function spokenText(): string {
  return speaking ? speakingText : '';
}
export function onSpeech(listener: (phase: SpeechPhase) => void): () => void {
  speechListeners.add(listener);
  return () => speechListeners.delete(listener);
}
export function emitSpeech(phase: SpeechPhase, text = ''): void {
  speaking = phase === 'start';
  speakingText = speaking ? text : '';
  speechListeners.forEach((listener) => listener(phase));
}
