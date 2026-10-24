/** Проверка ответов в рантайме по src/api/types.ts (03-behavior.md, раздел 3). */
import { z } from 'zod';
import type { ChatResponse, RecordItem } from './types';

const recordSchema = z.object({
  id: z.number().nullable(),
  type: z.enum(['task', 'reminder', 'meeting', 'note', 'busy']).nullable(),
  text: z.string(),
  date: z.string().nullable(),
  time: z.string().nullable(),
  end_time: z.string().nullable().optional().default(null),
  remind_before: z.number().nullable().optional().default(null),
  date_kind: z.enum(['exact', 'deadline', 'none']),
  status: z.enum(['open', 'done']).nullable(),
  created_at: z.string().nullable(),
  repeat: z.object({ weekdays: z.array(z.number()), until: z.string().nullable() }).nullable().optional().default(null),
  demo: z.boolean().optional().default(false),
  marks: z.array(z.string()).default([]),
  highlight: z.enum(['date', 'time', 'text']).nullable().default(null),
});

const actionSchema = z.object({ id: z.string(), record_id: z.number().optional().nullable() });

const buttonSchema = z.object({
  label: z.string(),
  send: z.union([
    z.object({ action: actionSchema }),
    z.object({ text: z.string() }),
  ]),
  style: z.enum(['primary', 'secondary']).nullable().optional(),
});

const chatSchema = z.object({
  message_id: z.string(),
  kind: z.string(),
  text: z.string(),
  speech: z.string().nullable().default(null),
  records: z.array(recordSchema).default([]),
  nearest: recordSchema.nullable().default(null),
  total: z.number().nullable().default(null),
  awaiting_reply: z.boolean().default(false),
  buttons: z.array(buttonSchema).default([]),
});

function clean<T extends { record_id?: number | null }>(action: T) {
  return action.record_id == null ? { id: (action as unknown as { id: string }).id } : action;
}

/** null — ответ не прошёл схему */
export function parseChat(data: unknown): ChatResponse | null {
  const result = chatSchema.safeParse(data);
  if (!result.success) return null;
  const value = result.data;
  return {
    ...value,
    records: value.records as RecordItem[],
    nearest: value.nearest as RecordItem | null,
    buttons: value.buttons.map((b) => ({
      label: b.label,
      style: b.style ?? undefined,
      send: 'action' in b.send ? { action: clean(b.send.action) as { id: string; record_id?: number } } : b.send,
    })),
  };
}

export function parseRecord(data: unknown): RecordItem | null {
  const result = recordSchema.safeParse(data);
  return result.success ? (result.data as RecordItem) : null;
}
