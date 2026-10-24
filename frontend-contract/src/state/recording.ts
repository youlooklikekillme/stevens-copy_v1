/** Запись голоса для строки ввода — 03-behavior.md, раздел 6. */
import { api } from '../api';
import { T } from '../content/texts';
import { voice, type VoiceError } from '../voice';
import { applyCorrections } from '../voice/edit';
import { send, setHint } from './chat';
import { emitActivity } from './events';
import { createStore, store } from './store';

/** Быстро меняющееся — отдельно, чтобы не перерисовывать всё приложение */
export const recordingStore = createStore({ transcript: '', level: 0, startedAt: 0 });

/** Ф9: столько ждём первого слова после вопроса (контракт v1.5) */
const AUTO_SILENCE_MS = 4000;

let prefix = '';
let auto = false;
let heard = false;
let autoTimer: ReturnType<typeof setTimeout> | undefined;

const ERROR_HINT: Record<VoiceError, keyof typeof T> = {
  denied: 'voice_denied',
  'no-mic': 'voice_no_mic',
  'no-speech': 'voice_no_speech',
  failed: 'voice_error',
  service: 'voice_service',
};

export function startRecording(options: { auto?: boolean } = {}): void {
  const state = store.get();
  if (state.waiting || state.recording) return;
  if (!voice.isSupported()) {
    api.log('mic_failed', 'unsupported');
    setHint('voice_unsupported');
    return;
  }
  prefix = state.input.trim();
  auto = !!options.auto;
  heard = false;
  setHint(null);
  void voice.start();
}

export function stopRecording(): void {
  voice.stop();
}

export function cancelRecording(): void {
  clearTimeout(autoTimer);
  voice.cancel();
  store.set({ input: prefix });
}

// Вызов по имени: запись началась без кнопки — берём то, что уже набрано, как при нажатии
voice.on('wake', () => {
  prefix = store.get().input.trim();
  auto = false;
  heard = false;
  setHint(null);
  // После recording-start, иначе он погасит кивок персонажа
  setTimeout(() => emitActivity('wake'), 0);
});

voice.on('state', (state) => {
  if (state === 'asking') setHint('voice_permission');
  if (state === 'recording') {
    if (store.get().hint?.key === 'voice_permission') setHint(null);
    recordingStore.set({ transcript: prefix, level: 0, startedAt: Date.now() });
    store.set({ recording: true });
    emitActivity('recording-start');
    if (auto) {
      autoTimer = setTimeout(() => {
        if (!heard) cancelRecording(); // Д13: тихо выключаемся, без «Не расслышал»
      }, AUTO_SILENCE_MS);
    }
  }
  if (state === 'idle') {
    clearTimeout(autoTimer);
    if (store.get().recording) {
      store.set({ recording: false });
      emitActivity('recording-stop');
    }
  }
});

voice.on('interim', (text) => {
  if (text) heard = true;
  // Поправку «не „и вам“, а „Иван“» видно сразу, пока говоришь
  recordingStore.set({ transcript: applyCorrections([prefix, text].filter(Boolean).join(' ')) });
});

voice.on('level', (level) => recordingStore.set({ level }));

/** «Нет», «не хочу» в ответ на «Показать задачи без срока?» */
const REFUSE_RE = /^(?:нет|неа|не\s+хочу|не\s+надо|не\s+нужно|не\s+сейчас|не\s+стоит|потом|позже|обойдусь|хватит)(?:[\s,.!]+(?:спасибо|не\s+надо|потом))*[\s.!]*$/i;

/**
 * Стивенс предложил показать задачи без срока и сам включил микрофон. Услышал отказ — просто выключаемся:
 * в ленту ничего не уходит, предложение гаснет, как будто нажали «Не сейчас».
 */
function refusedOffer(text: string): boolean {
  const { messages } = store.get();
  const last = messages[messages.length - 1];
  if (!last || last.kind !== 'answer' || last.pressed !== null || !REFUSE_RE.test(text.trim())) return false;
  const index = last.response.buttons.findIndex((b) => 'action' in b.send && b.send.action.id === 'skip_undated');
  if (index < 0) return false;
  store.set((s) => ({ messages: s.messages.map((m) => (m.id === last.id ? { ...m, pressed: index } : m)) }));
  return true;
}

voice.on('final', (text) => {
  const raw = [prefix, text].filter(Boolean).join(' ');
  const full = applyCorrections(raw);
  store.set({ input: '' });
  if (!prefix && refusedOffer(full)) return;
  // raw_text — что распознал браузер до поправок ослышки: только для журнала (Ф4)
  if (!send(full, 'voice', { rawText: raw })) store.set({ input: full });
});

voice.on('error', (error) => {
  store.set({ input: prefix });
  if (error !== 'no-speech') api.log('mic_failed', error); // Ф0: любой отказ микрофона — в журнал
  // Д13: сам включился — сам и молчит об ошибках (Safari может не дать слушать без нажатия)
  if (auto) return;
  setHint(ERROR_HINT[error]);
});

if (typeof document !== 'undefined') {
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'hidden') return;
    emitActivity('tab-hidden');
    if (store.get().recording) cancelRecording();
  });
}
