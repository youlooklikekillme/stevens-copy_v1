/**
 * Ф9: автослушание после вопроса (контракт v1.5). Ответ на голосовое, awaiting_reply, вкладка видна,
 * микрофон уже разрешён — микрофон включается всегда: через 400 мс после озвучки (без неё — через 0,6 с).
 * Не включился — ещё раз через 300 мс; снова нет — autolisten_failed в журнал и подсказка voice_error.
 */
import { api } from '../../api';
import { setHint } from '../../state/chat';
import { isSpeaking, onAssistantResponse, onSpeech, onUiActivity } from '../../state/events';
import { startRecording } from '../../state/recording';
import { store } from '../../state/store';
import { voice } from '../../voice';

const DELAY_MS = 600;
const AFTER_SPEECH_MS = 400;
const RETRY_MS = 300;

let timer: ReturnType<typeof setTimeout> | undefined;
let waitingForSpeech = false;

function cancel(): void {
  clearTimeout(timer);
  waitingForSpeech = false;
}

function listen(retry = false): void {
  waitingForSpeech = false;
  if (document.visibilityState !== 'visible') return;
  startRecording({ auto: true });
  timer = setTimeout(() => {
    const s = store.get();
    if (s.recording || voice.isActive()) return;
    if (s.waiting || document.visibilityState !== 'visible') return; // человек уже что-то сделал
    if (!retry) {
      listen(true);
      return;
    }
    api.log('autolisten_failed', 'microphone did not start');
    setHint('voice_error');
  }, RETRY_MS);
}

export function init(): void {
  onAssistantResponse((response, request) => {
    cancel();
    if (request.source !== 'voice' || !response.awaiting_reply) return;
    if (document.visibilityState !== 'visible' || !voice.hasPermission()) return;
    // Озвучка (Д12) могла начаться в этом же ответе — проверяем после неё
    setTimeout(() => {
      if (isSpeaking()) waitingForSpeech = true;
      else timer = setTimeout(() => listen(), DELAY_MS);
    }, 0);
  });

  onSpeech((phase) => {
    if (phase === 'end' && waitingForSpeech) {
      waitingForSpeech = false;
      timer = setTimeout(listen, AFTER_SPEECH_MS);
    }
    if (phase === 'cancelled') cancel();
  });

  // Любое действие человека до старта отменяет автослушание
  onUiActivity((kind) => {
    if (['typing', 'button-press', 'recording-start', 'new-dialog', 'tab-hidden', 'request-start'].includes(kind)) cancel();
  });
}
