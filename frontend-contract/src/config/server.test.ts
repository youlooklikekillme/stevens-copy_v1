import { describe, expect, it } from 'vitest';
import { serverHas, setServerFeatures } from './server';

describe('server features (GET /api/health)', () => {
  it('old server without list — everything but stt', () => {
    setServerFeatures(null);
    expect(serverHas('f10')).toBe(true);
    expect(serverHas('stt')).toBe(false);
  });
  it('hides what the server did not list', () => {
    setServerFeatures(['f2', 'f3', 'f13', 'stt']);
    expect(serverHas('f13')).toBe(true);
    expect(serverHas('f10')).toBe(false);
    expect(serverHas('stt')).toBe(true);
    setServerFeatures(null); // повторный ответ без списка не стирает известный
    expect(serverHas('f10')).toBe(false);
  });
});
