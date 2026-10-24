/** Строка ввода — 02-screens.md, 2.5–2.6. */
import { serverHas } from '../config/server';
import { ArrowUp, Mic, Trash2 } from 'lucide-react';
import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react';
import { T, fill } from '../content/texts';
import { InputBarOverlay } from '../extensions/points';
import { send } from '../state/chat';
import { emitActivity } from '../state/events';
import { cancelRecording, recordingStore, startRecording, stopRecording } from '../state/recording';
import { store, useApp } from '../state/store';
import { setWakeWord, wakeStore } from '../state/wake';
import { voice } from '../voice';

const LIMIT = 300;
const COUNTER_FROM = 250;
const MAX_LINES = 4;
const BARS = 7;
const PHONE = '(max-width: 760px)';

function useMedia(query: string): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(query);
      m.addEventListener('change', cb);
      return () => m.removeEventListener('change', cb);
    },
    () => window.matchMedia(query).matches,
  );
}

function StopIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden="true">
      <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" />
    </svg>
  );
}

function RecordingRow() {
  const { transcript, level, startedAt } = useSyncExternalStore(recordingStore.subscribe, recordingStore.get);
  const [seconds, setSeconds] = useState(0);
  const textRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - startedAt) / 1000)), 250);
    return () => clearInterval(timer);
  }, [startedAt]);
  useLayoutEffect(() => {
    const el = textRef.current;
    if (el) el.scrollLeft = el.scrollWidth;
  }, [transcript]);
  // Столбики: 7 штук, 6–24 px по уровню звука; середина выше краёв
  const shape = [0.45, 0.7, 0.9, 1, 0.9, 0.7, 0.45];
  return (
    <>
      <button type="button" className="icon-btn icon-btn--ghost" aria-label={T.btn_cancel_record} onClick={() => { emitActivity('button-press'); cancelRecording(); }}>
        <Trash2 size={18} strokeWidth={1.5} aria-hidden="true" />
      </button>
      <div className="bars" aria-hidden="true">
        {Array.from({ length: BARS }, (_, i) => (
          <span key={i} className="bars__bar" style={{ height: `${6 + 18 * level * shape[i]}px` }} />
        ))}
      </div>
      <div className="rec-text" ref={textRef}><span>{transcript}</span></div>
      <span className="rec-timer">{`${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`}</span>
      <button type="button" className="icon-btn icon-btn--dark" aria-label={T.btn_stop_send} onClick={() => { emitActivity('button-press'); stopRecording(); }}>
        <StopIcon />
      </button>
    </>
  );
}

/**
 * Вызов по имени: точка в углу микрофона. Пустая — выключено, закрашенная — включено
 * (дышит, пока слушает фоном). По нажатию — плашка с объяснением и переключателем.
 */
function WakeDot() {
  const { enabled, armed } = useSyncExternalStore(wakeStore.subscribe, wakeStore.get);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLSpanElement>(null);
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!rootRef.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);
  if (!voice.isSupported()) return null;
  // Переключатель, а не разовый вопрос: плашка остаётся открытой, видно, что режим включился или выключился
  const toggle = () => {
    emitActivity('button-press');
    setWakeWord(!enabled);
  };
  return (
    <span className="wake" ref={rootRef}>
      <button type="button" className="wake__hit" aria-label={enabled ? T.wake_label_on : T.wake_label_off}
        aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        <span className={`wake__dot ${enabled ? 'wake__dot--on' : ''} ${enabled && armed ? 'wake__dot--armed' : ''}`} aria-hidden="true" />
      </button>
      {open && (
        <span className="wake__pop" role="dialog" aria-label={T.wake_label_off}>
          <span className="wake__head">
            <span className="wake__title" id="wake-title">{T.wake_title}</span>
            <button type="button" role="switch" aria-checked={enabled} aria-labelledby="wake-title" className="switch" onClick={toggle}>
              <span className="switch__knob" aria-hidden="true" />
            </button>
          </span>
          <span className="wake__state">{enabled ? T.wake_state_on : T.wake_state_off}</span>
          <span className="wake__text">{T.wake_text}</span>
        </span>
      )}
    </span>
  );
}

export function Composer() {
  const input = useApp((s) => s.input);
  const waiting = useApp((s) => s.waiting);
  const recording = useApp((s) => s.recording);
  const phone = useMedia(PHONE);
  const ref = useRef<HTMLTextAreaElement>(null);

  // Высота поля: 1–4 строки, дальше прокрутка
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    const line = parseFloat(getComputedStyle(el).lineHeight) || 22;
    const padding = parseFloat(getComputedStyle(el).paddingTop) * 2;
    el.style.height = `${Math.min(el.scrollHeight, line * MAX_LINES + padding)}px`;
  }, [input, recording]);

  const canSend = input.trim().length > 0 && !waiting;

  const submit = () => {
    if (!canSend) return;
    const text = input;
    if (send(text, 'text')) {
      store.set({ input: '' });
      if (phone) ref.current?.blur();
    }
  };

  const length = input.length;
  return (
    <div className="composer">
      <div className={`composer__box ${recording ? 'composer__box--rec anim-rec' : ''}`}>
        <InputBarOverlay />
        {recording ? (
          <RecordingRow />
        ) : (
          <>
            <span className="mic-wrap">
              <button type="button" className="icon-btn icon-btn--dark" aria-label={T.btn_mic} disabled={waiting}
                onClick={() => { emitActivity('button-press'); startRecording(); }}>
                <Mic size={18} strokeWidth={1.5} aria-hidden="true" />
              </button>
              {serverHas('f20') && <WakeDot />}
            </span>
            <div className="composer__field">
              <textarea
                ref={ref}
                rows={1}
                value={input}
                maxLength={LIMIT}
                placeholder={phone ? T.input_placeholder_short : T.input_placeholder}
                autoFocus={!phone}
                onChange={(e) => {
                  store.set({ input: e.target.value.slice(0, LIMIT) });
                  emitActivity('typing');
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                    e.preventDefault();
                    submit();
                  }
                }}
              />
              {length >= COUNTER_FROM && (
                <span className={`counter ${length >= LIMIT ? 'counter--limit' : ''}`}>{fill(T.char_counter, { count: length })}</span>
              )}
            </div>
            <button type="button" className={`icon-btn ${canSend ? 'icon-btn--dark' : 'icon-btn--off'}`} aria-label={T.btn_send}
              disabled={!canSend} onClick={submit}>
              <ArrowUp size={18} strokeWidth={1.75} aria-hidden="true" />
            </button>
          </>
        )}
      </div>
    </div>
  );
}
