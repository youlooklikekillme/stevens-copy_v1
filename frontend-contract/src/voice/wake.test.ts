import { beforeAll, expect, test, vi } from 'vitest';
import { voice } from './index';

type Handler = ((e: unknown) => void) | null;
class FakeRec {
  static last: FakeRec | null = null;
  lang = ''; interimResults = false; continuous = false;
  onresult: Handler = null; onerror: Handler = null; onend: (() => void) | null = null; onstart: (() => void) | null = null;
  constructor() { FakeRec.last = this; }
  start() {}
  stop() { this.onend?.(); }
  abort() { this.onend?.(); }
  say(text: string) {
    this.onresult?.({ resultIndex: 0, results: [{ isFinal: false, 0: { transcript: text } }] });
  }
}

beforeAll(() => {
  // Тесты идут без браузера: подставляем распознавание и микрофон
  const g = globalThis as unknown as Record<string, unknown>;
  g.window = { SpeechRecognition: FakeRec };
  Object.defineProperty(globalThis, 'navigator', {
    value: { mediaDevices: { getUserMedia: () => Promise.reject(new Error('no')) } },
    configurable: true,
  });
});

test('после имени запись становится обычной, имя в текст не попадает', () => {
  vi.useFakeTimers();
  const events: string[] = [];
  voice.on('wake', () => events.push('wake'));
  voice.on('final', (t) => events.push(`final:${t}`));
  voice.setArmed(true);
  vi.advanceTimersByTime(500);
  const rec = FakeRec.last!;
  rec.say('ну вот');
  expect(events).toEqual([]);
  rec.say('ну вот Стивенсон, завтра в 10 созвон с Иваном');
  expect(events).toEqual(['wake']);
  vi.advanceTimersByTime(2000);
  expect(events).toEqual(['wake', 'final:завтра в 10 созвон с Иваном']);
  voice.setArmed(false);
  vi.useRealTimers();
});

test('«Стивенс, …, отмена» — ничего не уходит, снова слушаем имя', () => {
  vi.useFakeTimers();
  const events: string[] = [];
  const offFinal = voice.on('final', (t) => events.push(`final:${t}`));
  const offState = voice.on('state', (s) => events.push(s));
  voice.setArmed(true);
  vi.advanceTimersByTime(500);
  const rec = FakeRec.last!;
  rec.say('Стивенс, завтра в 10 созвон');
  rec.onresult?.({ resultIndex: 0, results: [{ isFinal: true, 0: { transcript: 'Стивенс, завтра в 10 созвон отмена' } }] });
  vi.advanceTimersByTime(3000);
  expect(events).toEqual(['recording', 'idle']);
  offFinal();
  offState();
  voice.setArmed(false);
  vi.useRealTimers();
});

test('своё имя из динамиков не зовёт Стивенса, после фразы фоновая запись начинается заново', async () => {
  vi.useFakeTimers();
  const events: string[] = [];
  const off = voice.on('wake', () => events.push('wake'));
  voice.setArmed(true);
  vi.advanceTimersByTime(500);
  const first = FakeRec.last!;
  voice.setEcho('Меня зовут Стивенс, я записал встречу');
  first.say('меня зовут Стивенс я записал');
  expect(events).toEqual([]);
  voice.setEcho(null);
  await vi.advanceTimersByTimeAsync(700);
  const second = FakeRec.last!;
  expect(second).not.toBe(first);
  second.say('Стивс, отмени');
  expect(events).toEqual(['wake']);
  off();
  second.stop();
  voice.setArmed(false);
  await vi.advanceTimersByTimeAsync(700);
  vi.useRealTimers();
});

test('пока Стивенс говорит фразу без имени, «Стивс» зовёт его', () => {
  vi.useFakeTimers();
  const events: string[] = [];
  const off = voice.on('wake', () => events.push('wake'));
  voice.setArmed(true);
  vi.advanceTimersByTime(500);
  voice.setEcho('Записал на завтра в 10');
  FakeRec.last!.say('записал на завтра стивз подожди');
  expect(events).toEqual(['wake']);
  voice.setEcho(null);
  off();
  FakeRec.last!.stop();
  voice.setArmed(false);
  vi.useRealTimers();
});

test('микрофон не разрешили: запись сразу выключается и включается снова, даже если браузер не прислал onend', async () => {
  vi.useFakeTimers();
  const events: string[] = [];
  const offState = voice.on('state', (s) => events.push(s));
  const offError = voice.on('error', (e) => events.push(`error:${e}`));
  await voice.start();
  const denied = FakeRec.last!;
  denied.abort = () => {}; // как в браузере: после отказа onend не приходит
  denied.onerror?.({ error: 'not-allowed' });
  expect(events).toEqual(['recording', 'error:denied', 'idle']);
  await voice.start();
  expect(FakeRec.last).not.toBe(denied);
  expect(events).toEqual(['recording', 'error:denied', 'idle', 'recording']);
  FakeRec.last!.stop();
  offState();
  offError();
  vi.useRealTimers();
});

test('браузер завис и не заканчивает запись — «стоп» всё равно её выключает', async () => {
  vi.useFakeTimers();
  const events: string[] = [];
  const offState = voice.on('state', (s) => events.push(s));
  await voice.start();
  const stuck = FakeRec.last!;
  stuck.stop = () => {};
  stuck.abort = () => {};
  voice.stop();
  expect(events).toEqual(['recording']);
  vi.advanceTimersByTime(1600);
  expect(events).toEqual(['recording', 'idle']);
  offState();
  vi.useRealTimers();
});

test('короткое имя «Стивс» в разных написаниях зовёт, похожие слова — нет', () => {
  vi.useFakeTimers();
  for (const [text, hit] of [
    ['Стивс, завтра в 10 созвон', true], ['стивз завтра созвон', true], ['стифс, напомни', true],
    ['Стивес, запиши', true], ['Стив, запиши', true], ['steves remind me', true],
    ['стиль у встречи деловой', false], ['стивидор придёт', false], ['стивсон', false],
  ] as const) {
    const events: string[] = [];
    const off = voice.on('wake', () => events.push('wake'));
    voice.setArmed(true);
    vi.advanceTimersByTime(500);
    FakeRec.last!.say(text);
    expect(events, text).toEqual(hit ? ['wake'] : []);
    off();
    FakeRec.last!.stop();
    voice.setArmed(false);
    vi.advanceTimersByTime(700);
  }
  vi.useRealTimers();
});

test('его фраза без имени из динамиков не попадает в следующую просьбу', async () => {
  vi.useFakeTimers();
  const events: string[] = [];
  const offWake = voice.on('wake', () => events.push('wake'));
  const offFinal = voice.on('final', (t) => events.push(`final:${t}`));
  voice.setArmed(true);
  vi.advanceTimersByTime(500);
  const first = FakeRec.last!;
  voice.setEcho('Добавил встречу на завтра в 16:00');
  first.say('добавил встречу на завтра в 16:00');
  voice.setEcho(null);
  await vi.advanceTimersByTimeAsync(700);
  const second = FakeRec.last!;
  expect(second).not.toBe(first);
  second.say('Стивенс, отредактируй последнюю запись');
  vi.advanceTimersByTime(2000);
  expect(events).toEqual(['wake', 'final:отредактируй последнюю запись']);
  offWake();
  offFinal();
  voice.setArmed(false);
  await vi.advanceTimersByTimeAsync(700);
  vi.useRealTimers();
});

test('браузер переписал распознанное и потерял имя — старые куски во фразу не попадают, куски не слипаются', () => {
  vi.useFakeTimers();
  const events: string[] = [];
  const offFinal = voice.on('final', (t) => events.push(`final:${t}`));
  voice.setArmed(true);
  vi.advanceTimersByTime(500);
  const rec = FakeRec.last!;
  const send = (...pieces: string[]) => rec.onresult?.({
    resultIndex: 0, results: pieces.map((p, i) => ({ isFinal: i < pieces.length - 1, 0: { transcript: p } })),
  });
  send('встреча завтра в 16:00', 'Стивенс встреча с Ариной');
  send('встреча завтра в 16:00', 'стебель встреча с Ариной', '16.0');
  send('встреча завтра в 16:00', 'стебель встреча с Ариной', '16.0', '16:00');
  vi.advanceTimersByTime(2000);
  expect(events).toEqual(['final:стебель встреча с Ариной 16.0 16:00']);
  offFinal();
  voice.setArmed(false);
  vi.advanceTimersByTime(700);
  vi.useRealTimers();
});
