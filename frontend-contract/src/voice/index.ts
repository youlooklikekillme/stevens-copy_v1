/**
 * Голос — 03-behavior.md, раздел 6. Интерфейс: isSupported(), start(), stop(), cancel()
 * и события state / interim / final / error / level. Движок — Web Speech API;
 * если его заменят на серверный (POST /api/stt), остальной код не меняется.
 *
 * Вызов по имени: arm() слушает фоном, пока не услышит «Стивенс». Тогда эта же запись
 * становится обычной (событие wake, затем state recording), и всё сказанное после имени
 * уходит в final. Так нет паузы между именем и фразой: «Стивенс, завтра в 10 созвон».
 *
 * «стоп» / «отмена» последним словом — отмена: микрофон выключается, в final ничего не уходит.
 * Сразу — когда браузер закрепил это слово (isFinal), иначе по паузе: «отмена встречи» так не оборвётся.
 *
 * Перебить по имени: пока Стивенс говорит, фоновая запись идёт дальше (кроме Safari), и «Стивс» обрывает озвучку.
 * Его собственный голос из динамиков тоже попадает в микрофон: если в его фразе есть имя, пока он говорит,
 * имя не ловим (setEcho), а после фразы перезапускаем фоновую запись, чтобы старый текст не сработал.
 */
import { api } from '../api';
import { serverHas } from '../config/server';
import { isCancel } from './edit';

export type VoiceError = 'denied' | 'no-mic' | 'no-speech' | 'failed' | 'service';
export type VoiceState = 'idle' | 'asking' | 'recording';

type Events = {
  state: (state: VoiceState) => void;
  interim: (text: string) => void;
  final: (text: string) => void;
  error: (error: VoiceError) => void;
  level: (level: number) => void;
  /** Услышали имя: дальше идёт обычная запись */
  wake: () => void;
  /** Фоновое прослушивание имени: включилось или выключилось */
  armed: (armed: boolean) => void;
  /** Фоновому прослушиванию закрыли микрофон */
  wakeDenied: () => void;
};

interface RecognitionResult { isFinal: boolean; 0: { transcript: string } }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
interface Recognition {
  lang: string;
  interimResults: boolean;
  continuous: boolean;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  onstart: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
}
type RecognitionCtor = new () => Recognition;

const MAX_MS = 20000;
/** Столько ждём onend после stop/abort/ошибки. Отказ в доступе к микрофону браузер иногда так и не завершает */
const END_WAIT_MS = 1500;
/** Пауза, после которой считаем, что человек договорил: хватает, чтобы подумать посреди фразы */
const SILENCE_MS = 1800;
/**
 * Как браузер пишет имя: «Стивенс», «Стивенсон», «Стивен», короткое «Стивс» («стивз», «стифс», «стивес»),
 * «Stevens», «Steves». «Стив» тоже ловим: так браузер часто слышит «Стивс». «Стиль» и «стивидор» — нет
 */
const WAKE_WORD = /(?:^|[\s,.!?])(стив[еи]н\S*|ст[иы][вф]е?[сзc]?(?![а-яё])|stevens?\S*|steven\S*|steve?s?(?![a-z])|stivs?(?![a-z]))[\s,.!?—-]*/i;
/** Сказали только имя и молчат — ждём фразу столько, потом снова слушаем фоном */
const WAKE_WAIT_MS = 6000;

const ERRORS: Record<string, VoiceError> = {
  'not-allowed': 'denied',
  'audio-capture': 'no-mic',
  // Safari: распознавание недоступно (выключена Диктовка или Siri, нет разрешения на распознавание речи)
  'service-not-allowed': 'service',
  'language-not-supported': 'service',
};

/** Строки в консоли браузера (F12) — по ним видно, почему не слышит */
const log = (...args: unknown[]) => console.info('[Stevens · микрофон]', ...args);

const isSafari = () => typeof navigator !== 'undefined' && /^((?!chrome|chromium|android|crios|fxios|edg).)*safari/i.test(navigator.userAgent);

function ctor(): RecognitionCtor | null {
  const w = window as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

const listeners: { [K in keyof Events]: Set<Events[K]> } = {
  state: new Set(), interim: new Set(), final: new Set(), error: new Set(), level: new Set(),
  wake: new Set(), armed: new Set(), wakeDenied: new Set(),
};

function emit<K extends keyof Events>(name: K, ...args: Parameters<Events[K]>): void {
  listeners[name].forEach((l) => (l as (...a: Parameters<Events[K]>) => void)(...args));
}

let recognition: Recognition | null = null;
let stream: MediaStream | null = null;
let audio: AudioContext | null = null;
let levelTimer: ReturnType<typeof setInterval> | undefined;
let maxTimer: ReturnType<typeof setTimeout> | undefined;
let silenceTimer: ReturnType<typeof setTimeout> | undefined;
let endWatch: ReturnType<typeof setTimeout> | undefined;
let finalText = '';
let interimText = '';
let cancelled = false;
let permissionGranted = false;
// После перезагрузки страницы разрешение уже может быть выдано — тогда автослушание работает с первого ответа
if (typeof navigator !== 'undefined' && navigator.permissions?.query) {
  navigator.permissions.query({ name: 'microphone' as PermissionName })
    .then((status) => {
      if (status.state === 'granted') permissionGranted = true;
    })
    .catch(() => undefined);
}
/** Фоновая запись, которая ещё ждёт имя */
let armedRec: Recognition | null = null;
let armedEnded: Promise<void> | null = null;
let wantArmed = false;
let rearmTimer: ReturnType<typeof setTimeout> | undefined;
/** Фраза, которую Стивенс сейчас говорит вслух, и есть ли в ней имя */
let echoHasName = false;
let echoHeard = false;
/** Пока он говорит, фоновая запись что-то услышала — это его голос, после фразы начинаем запись заново */
let echoOn = false;
let echoAny = false;

function cleanupAudio(): void {
  clearInterval(levelTimer);
  clearTimeout(maxTimer);
  clearTimeout(silenceTimer);
  stream?.getTracks().forEach((t) => t.stop());
  stream = null;
  void audio?.close().catch(() => undefined);
  audio = null;
}

/**
 * Запись закончилась: отпускаем микрофон и кнопку. true — впервые; false — уже отпустили
 * (по ошибке или по сторожу), второй onend ничего не делает.
 */
function release(rec: Recognition): boolean {
  clearTimeout(endWatch);
  if (recognition !== rec) return false;
  recognition = null;
  cleanupAudio();
  emit('state', 'idle');
  clearTimeout(rearmTimer);
  if (wantArmed) rearmTimer = setTimeout(arm, 400);
  return true;
}

/** Попросили остановить, а браузер молчит — сами доводим запись до конца, чтобы кнопка не залипала */
function watchEnd(rec: Recognition): void {
  clearTimeout(endWatch);
  endWatch = setTimeout(() => {
    if (recognition !== rec) return;
    log('браузер не закончил запись — отпускаю сам');
    rec.onend?.();
    try { rec.abort(); } catch { /* уже остановлена */ }
  }, END_WAIT_MS);
}

function startLevel(media: MediaStream): void {
  const reduced = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  if (reduced) return;
  try {
    audio = new AudioContext();
    const analyser = audio.createAnalyser();
    analyser.fftSize = 256;
    audio.createMediaStreamSource(media).connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    levelTimer = setInterval(() => {
      analyser.getByteTimeDomainData(data);
      let sum = 0;
      for (const v of data) sum += ((v - 128) / 128) ** 2;
      emit('level', Math.min(1, Math.sqrt(sum / data.length) * 4));
    }, 80);
  } catch {
    /* без столбиков */
  }
}

/** Куски распознанного по порядку. Склеиваем через пробел: «16.0» + «16:00» не должны слипаться в «16.016:00» */
function piecesOf(event: RecognitionEvent): string[] {
  const pieces: string[] = [];
  for (let i = 0; i < event.results.length; i += 1) pieces.push(event.results[i][0].transcript.trim());
  return pieces;
}

/** В каком куске имя: всё, что было до этого куска (его же озвучка, чужая речь), во фразу не попадает */
function wakePiece(pieces: string[]): number {
  for (let i = 0; i < pieces.length; i += 1) {
    if (WAKE_WORD.test(pieces.slice(i).join(' ')) && !WAKE_WORD.test(pieces.slice(i + 1).join(' '))) return i;
  }
  return -1;
}

/**
 * Safari может не дать запустить распознавание вне нажатия. Тогда не выключаем вызов по имени,
 * а ждём любого следующего касания страницы и запускаем фоновую запись прямо в нём.
 */
let waitingGesture = false;
function armOnNextGesture(): void {
  if (waitingGesture || typeof document === 'undefined') return;
  waitingGesture = true;
  const go = () => {
    document.removeEventListener('pointerdown', go, true);
    document.removeEventListener('keydown', go, true);
    waitingGesture = false;
    arm(true);
  };
  document.addEventListener('pointerdown', go, true);
  document.addEventListener('keydown', go, true);
}

/**
 * Фоновая запись до имени. Браузер сам обрывает её раз в минуту или на тишине — запускаем заново.
 * inGesture — запуск прямо в нажатии: если и тогда отказ, значит, распознавание действительно запрещено.
 */
function arm(inGesture = false): void {
  const Ctor = ctor();
  if (!Ctor || !wantArmed || recognition || armedRec) return;
  const rec = new Ctor();
  rec.lang = 'ru-RU';
  rec.interimResults = true;
  // Safari в непрерывном режиме может не отдавать результаты: там одна фраза за запуск,
  // поэтому имя и просьбу говорим на одном дыхании: «Стивенс, завтра в 10 созвон»
  rec.continuous = !isSafari();
  let woke = false;
  /** С какого куска распознанного начинается фраза после имени */
  let wakeFrom = 0;
  let resolveEnded: () => void = () => undefined;
  armedEnded = new Promise((resolve) => { resolveEnded = resolve; });

  const finish = () => {
    clearTimeout(silenceTimer);
    clearTimeout(maxTimer);
    rec.stop();
  };

  rec.onresult = (event) => {
    const pieces = piecesOf(event);
    if (!woke) {
      if (echoOn && pieces.some(Boolean)) echoAny = true;
      const hit = WAKE_WORD.exec(pieces.join(' '));
      if (!hit) return;
      if (echoHasName) {
        // Скорее всего, это он сам себя услышал из динамиков
        echoHeard = true;
        return;
      }
      // Имя услышано: эта запись становится обычной
      woke = true;
      wakeFrom = Math.max(0, wakePiece(pieces));
      armedRec = null;
      emit('armed', false);
      recognition = rec;
      cancelled = false;
      finalText = '';
      interimText = '';
      emit('wake');
      emit('state', 'recording');
      maxTimer = setTimeout(finish, MAX_MS);
      silenceTimer = setTimeout(finish, WAKE_WAIT_MS);
      // Столбики — вторым захватом микрофона; Safari от этого сбивает распознавание
      if (!isSafari()) void navigator.mediaDevices?.getUserMedia({ audio: true }).then((media) => {
        if (recognition !== rec) { media.getTracks().forEach((t) => t.stop()); return; }
        stream = media;
        startLevel(media);
      }).catch(() => undefined);
    }
    // Браузер может переписать уже распознанное и потерять имя — тогда берём от того же куска, а не весь текст
    const tail = pieces.slice(wakeFrom).join(' ');
    const hit = WAKE_WORD.exec(tail);
    const shown = (hit ? tail.slice(hit.index + hit[0].length) : tail).replace(/\s+/g, ' ').trim();
    if (event.results[event.results.length - 1]?.isFinal && isCancel(shown)) {
      log('отмена голосом');
      cancelled = true;
      finish();
      return;
    }
    finalText = shown;
    interimText = '';
    emit('interim', shown);
    clearTimeout(silenceTimer);
    silenceTimer = setTimeout(finish, shown ? SILENCE_MS : WAKE_WAIT_MS);
  };
  rec.onerror = (event) => {
    if (event.error === 'not-allowed' || event.error === 'service-not-allowed') {
      if (inGesture || !isSafari()) {
        wantArmed = false;
        emit('wakeDenied');
      } else {
        refusedOutsideGesture = true;
      }
    }
    if (woke && event.error !== 'no-speech' && event.error !== 'aborted') cancelled = true;
  };
  rec.onend = () => {
    resolveEnded();
    if (!woke) {
      armedRec = null;
      armedEnded = null;
      emit('armed', false);
    } else {
      if (recognition === rec) {
        clearTimeout(endWatch);
        recognition = null;
        cleanupAudio();
        emit('state', 'idle');
        // Тишина после имени — без «Не расслышал»: просто снова слушаем фоном
        if (!cancelled && finalText.trim() && !isCancel(finalText)) emit('final', finalText.trim());
      } else if (recognition !== null) {
        return; // уже отпустили сторожем, а следом началась новая запись — её не трогаем
      }
    }
    clearTimeout(rearmTimer);
    if (!wantArmed) return;
    if (refusedOutsideGesture) armOnNextGesture();
    else rearmTimer = setTimeout(arm, 400);
  };
  let refusedOutsideGesture = false;
  // Фоновая запись запустилась — микрофон разрешён, автослушание (Д13) после ответа можно включать
  rec.onstart = () => { permissionGranted = true; };
  try {
    rec.start();
    armedRec = rec;
    emit('armed', true);
  } catch {
    armedEnded = null;
    if (!inGesture && isSafari()) armOnNextGesture();
  }
}

// ── Распознавание на сервере (Ф4): браузер не умеет, а сервер умеет (stt в features) ──
let serverRec: MediaRecorder | null = null;
/** Уровень громкости выше этого — человек говорит; после речи 1,8 с тишины — конец фразы */
const SPEECH_LEVEL = 0.12;

function serverSttReady(): boolean {
  return serverHas('stt') && typeof MediaRecorder !== 'undefined' && !!navigator.mediaDevices?.getUserMedia;
}

async function startServer(reason: string): Promise<void> {
  if (serverRec || recognition) return;
  api.log('stt_fallback', reason);
  cancelled = false;
  let media: MediaStream;
  try {
    media = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true } });
  } catch (error) {
    const name = error instanceof DOMException ? error.name : '';
    emit('error', name === 'NotAllowedError' ? 'denied' : name === 'NotFoundError' ? 'no-mic' : 'failed');
    return;
  }
  permissionGranted = true;
  const type = ['audio/ogg;codecs=opus', 'audio/webm;codecs=opus'].find((t) => MediaRecorder.isTypeSupported(t));
  const rec = new MediaRecorder(media, type ? { mimeType: type } : undefined);
  serverRec = rec;
  stream = media;
  const chunks: Blob[] = [];
  let spoke = false;
  rec.ondataavailable = (e) => { if (e.data.size) chunks.push(e.data); };
  rec.onstop = () => {
    serverRec = null;
    cleanupAudio();
    emit('state', 'idle');
    if (cancelled) return;
    if (!spoke || !chunks.length) {
      emit('error', 'no-speech');
      return;
    }
    void api.stt(new Blob(chunks, { type: rec.mimeType || type || 'audio/webm' }))
      .then((text) => (text.trim() ? emit('final', text.trim()) : emit('error', 'no-speech')))
      .catch(() => emit('error', 'failed'));
  };
  const off = voice.on('level', (level) => {
    if (serverRec !== rec) { off(); return; }
    if (level > SPEECH_LEVEL) {
      spoke = true;
      clearTimeout(silenceTimer);
      silenceTimer = setTimeout(() => { if (rec.state !== 'inactive') rec.stop(); }, SILENCE_MS);
    }
  });
  rec.start();
  startLevel(media);
  maxTimer = setTimeout(() => { if (rec.state !== 'inactive') rec.stop(); }, MAX_MS);
  emit('state', 'recording');
}

/** Остановить фоновую запись и дождаться, пока браузер отпустит микрофон */
async function disarmAndWait(): Promise<void> {
  clearTimeout(rearmTimer);
  const ended = armedEnded;
  if (!armedRec) return;
  armedRec.abort();
  armedRec = null;
  if (ended) await Promise.race([ended, new Promise((resolve) => setTimeout(resolve, 600))]);
}

export const voice = {
  isSupported(): boolean {
    if (typeof window === 'undefined') return false;
    return (ctor() !== null && !!navigator.mediaDevices?.getUserMedia) || serverSttReady();
  },

  /** Запись идёт (браузерная или серверная) */
  isActive(): boolean {
    return recognition !== null || serverRec !== null;
  },

  /** Разрешение на микрофон уже дано (для Д13) */
  hasPermission(): boolean {
    return permissionGranted;
  },

  async start(): Promise<void> {
    const Ctor = ctor();
    if (!Ctor && serverSttReady()) {
      await startServer('no SpeechRecognition');
      return;
    }
    if (!Ctor || recognition || serverRec) return;
    // Только если фоном слушали имя: иначе ждать нечего, а Safari запускает распознавание лишь прямо в нажатии
    if (armedRec) await disarmAndWait();
    if (recognition) return;
    cancelled = false;
    finalText = '';
    interimText = '';
    const rec = new Ctor();
    recognition = rec;
    rec.lang = 'ru-RU';
    rec.interimResults = true;
    // Слушаем сами до паузы SILENCE_MS: браузер в обычном режиме обрывает запись уже через ~1 с тишины.
    // В Safari непрерывный режим ненадёжен (результаты могут не приходить до конца) — там браузер решает сам
    rec.continuous = !isSafari();
    rec.onstart = () => {
      log('слушаю');
      permissionGranted = true;
      // Столбики громкости — вторым захватом микрофона. Safari от этого сбивает распознавание, там без столбиков
      if (isSafari()) return;
      void navigator.mediaDevices.getUserMedia({ audio: true }).then((media) => {
        if (recognition !== rec) { media.getTracks().forEach((t) => t.stop()); return; }
        stream = media;
        startLevel(media);
      }).catch(() => undefined);
    };
    rec.onresult = (event) => {
      if (cancelled || recognition !== rec) return;
      let interim = '';
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const result = event.results[i];
        if (result.isFinal) finalText = `${finalText} ${result[0].transcript.trim()}`.trim();
        else interim = `${interim} ${result[0].transcript.trim()}`.trim();
      }
      interimText = interim;
      const shown = `${finalText} ${interim}`.trim();
      if (!interim && isCancel(shown)) {
        log('отмена голосом');
        cancelled = true;
        rec.abort();
        return;
      }
      emit('interim', shown);
      clearTimeout(silenceTimer);
      if (shown) silenceTimer = setTimeout(() => rec.stop(), SILENCE_MS);
    };
    rec.onerror = (event) => {
      log('ошибка', event.error);
      if (cancelled) return;
      if (event.error === 'no-speech') return; // сообщим в onend
      if (event.error === 'aborted') return;
      cancelled = true;
      if (ERRORS[event.error] === 'service' && serverSttReady()) {
        // Safari отказал в русской речи — переходим на распознавание на сервере
        release(rec);
        try { rec.abort(); } catch { /* уже остановлена */ }
        void startServer(event.error);
        return;
      }
      emit('error', ERRORS[event.error] ?? 'failed');
      // Не разрешили микрофон: запись не начнётся, а onend может и не прийти — отпускаем сразу,
      // иначе кнопка остаётся в записи и не выключается до перезагрузки страницы
      release(rec);
      try { rec.abort(); } catch { /* уже остановлена */ }
    };
    rec.onend = () => {
      if (!release(rec)) return;
      log('закончил, текст:', JSON.stringify((finalText + ' ' + interimText).trim()));
      if (cancelled) return;
      const text = (finalText + ' ' + interimText).trim();
      if (isCancel(text)) log('отмена голосом');
      else if (text) emit('final', text);
      else emit('error', 'no-speech');
    };
    maxTimer = setTimeout(() => rec.stop(), MAX_MS);
    try {
      // Синхронно, в том же нажатии: разрешение на микрофон браузер спросит сам
      rec.start();
      emit('state', 'recording');
    } catch (error) {
      log('не запустился', error);
      recognition = null;
      cleanupAudio();
      emit('state', 'idle');
      emit('error', 'failed');
    }
  },

  /** Остановить и отдать распознанное */
  stop(): void {
    if (serverRec) {
      if (serverRec.state !== 'inactive') serverRec.stop();
      return;
    }
    const rec = recognition;
    if (!rec) return;
    rec.stop();
    watchEnd(rec);
  },

  /** Отменить: ничего не отдавать */
  cancel(): void {
    cancelled = true;
    if (serverRec) {
      if (serverRec.state !== 'inactive') serverRec.stop();
      return;
    }
    const rec = recognition;
    if (!rec) {
      cleanupAudio();
      return;
    }
    rec.abort();
    watchEnd(rec);
  },

  /** Перебивать по имени, пока Стивенс говорит: Safari при включённом микрофоне глушит и уводит звук в разговорный динамик */
  canBargeIn(): boolean {
    return !isSafari();
  },

  /** Что Стивенс говорит вслух (null — замолчал). Его голос из динамиков не должен звать его самого */
  setEcho(text: string | null): void {
    const wasHeard = echoHeard || echoAny;
    echoHasName = !!text && WAKE_WORD.test(text);
    echoHeard = false;
    echoOn = !!text;
    if (text) return;
    echoAny = false;
    // Его фраза уже в тексте фоновой записи — начинаем её заново: иначе она попадёт в следующую просьбу,
    // а длинная запись хуже слышит имя
    if (wasHeard && armedRec && wantArmed) {
      void disarmAndWait().then(() => {
        if (wantArmed) rearmTimer = setTimeout(arm, 100);
      });
    }
  },

  /** Слушать имя фоном (true) или нет. Пока идёт обычная запись, фоновая не мешает: включится после */
  setArmed(on: boolean, inGesture = false): void {
    wantArmed = on;
    clearTimeout(rearmTimer);
    // Прямо в нажатии «Да» — сразу: Safari разрешает распознавание только так.
    // Иначе с задержкой: сразу после записи уходит запрос, и включать фоновую запись на миг незачем
    if (on && inGesture) arm(true);
    else if (on) rearmTimer = setTimeout(arm, 400);
    else void disarmAndWait();
  },

  on<K extends keyof Events>(name: K, listener: Events[K]): () => void {
    listeners[name].add(listener);
    return () => listeners[name].delete(listener);
  },
};
