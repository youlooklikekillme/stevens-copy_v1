"""POST /api/chat — разделы 4–8 и 15 контракта v1.4.

Разбор фразы — app/nlu.py (правила) и, если правила не поняли, LLM (brain.py).
Даты, проверки, действия и тексты ответов — здесь, по шаблонам раздела 9.
"""
from __future__ import annotations

import asyncio
import os
import re
import time as clock
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import chatlog, nlu, series
from app.database import get_db
from app.models import DateKind, Record, TaskStatus, TaskType
from app.owner import Owner, get_owner
from app.schemas import ChatRequest
from app.templates import render
from app.textfmt import (
    MONTHS_GEN, TYPE_FORMS, TYPE_GEN_PLURAL, TYPE_WORD, capitalize, client_now, date_speech, date_text, parse_date, plural,
    shift_end, week_bounds, when_text,
)

router = APIRouter(prefix="/api", tags=["chat"])

EXAMPLES = [
    "Напомни завтра в 10 утра позвонить Ивану",
    "Что у меня на следующей неделе?",
    "Найди задачу про презентацию",
]
HELP_EXAMPLES = EXAMPLES + ["Поставь встречу с Иваном завтра", "Что у меня с Иваном?", "Запиши идею: открыть кофейню"]
FOUND_LIMIT = 50
WHICH_LIMIT = 5


# ── Память диалога: незавершённое уточнение по conversation_id (Р-45) ──

@dataclass
class Pending:
    kind: str                      # meeting_delete | draft | confirm | conflict | new_time | confirm_no | multi | multi_ask | move | move_when | move_all | edit | bulk_delete | note | series_delete | undated
    draft: dict = field(default_factory=dict)


@dataclass
class Conversation:
    pending: Pending | None = None
    variants: dict[str, int] = field(default_factory=dict)
    focus: int | None = None       # о какой записи шла речь: «перенеси её», «удали это»
    found: list[int] = field(default_factory=list)  # что нашёл поиск: «удали её» после нескольких — спросим какую
    bulk_deleted: list[int] = field(default_factory=list)  # «Вернуть все» после массового удаления
    undo: dict | None = None       # кнопка «Отменить»/«Вернуть» последнего ответа: «отмени», «верни» голосом
    choices: dict | None = None    # «Какую из них?»: действие и записи на выбор — «первую», «ту, что в субботу»
    touched: float = field(default_factory=clock.time)


CONVERSATIONS: dict[tuple[str, str], Conversation] = {}
CONVERSATION_TTL = 2 * 3600


def conversation(owner: Owner, conversation_id: str) -> Conversation:
    now = clock.time()
    if len(CONVERSATIONS) > 5000:
        for key in [k for k, v in CONVERSATIONS.items() if now - v.touched > CONVERSATION_TTL]:
            del CONVERSATIONS[key]
    item = CONVERSATIONS.setdefault((owner.key, conversation_id), Conversation())
    item.touched = now
    return item


def forget(owner_key: str) -> None:
    """Сброс песочницы — забываем и незавершённые уточнения."""
    for key in [k for k in CONVERSATIONS if k[0] == owner_key]:
        del CONVERSATIONS[key]


# ── Сборка ответа ─────────────────────────────────────

def button_text(label: str, text: str | None = None, style: str | None = None) -> dict:
    button = {"label": label, "send": {"text": text or label}}
    if style:
        button["style"] = style
    return button


def button_action(label: str, action_id: str, record_id: int | None = None, style: str | None = None) -> dict:
    action = {"id": action_id}
    if record_id is not None:
        action["record_id"] = record_id
    button = {"label": label, "send": {"action": action}}
    if style:
        button["style"] = style
    return button


CANCEL = button_action("Отменить", "cancel", style="secondary")


def reply(kind: str, text: str, speech: str | None = None, *, records=None, nearest=None, total=None,
          awaiting: bool = False, buttons=None) -> dict:
    return {
        "message_id": f"m-{uuid4().hex[:10]}",
        "kind": kind,
        "text": text,
        "speech": speech,
        "records": records or [],
        "nearest": nearest,
        "total": total,
        "awaiting_reply": awaiting,
        "buttons": buttons or [],
    }


@dataclass
class Ctx:
    owner: Owner
    db: AsyncSession
    conv: Conversation
    now: datetime
    features: set[str]
    found: list[int] = field(default_factory=list)  # найденное прошлым ответом
    trace: dict = field(default_factory=dict)       # для журнала: разбор, шаблон, LLM

    @property
    def today(self) -> date:
        return self.now.date()

    def say(self, template_id: str, values: dict | None = None, speech_values: dict | None = None,
            count_forms=None) -> tuple[str, str | None]:
        self.trace.setdefault("template", template_id)
        return render(template_id, values, speech_values, count_forms, self.conv.variants)

    def record_values(self, record: dict) -> tuple[dict, dict]:
        """{text} {when} {date} {time} для text и для speech."""
        value = parse_date(record.get("date"))
        text_vals = {"text": record["text"], "when": when_text(record, self.today), "time": record.get("time") or ""}
        speech_vals = {"when": when_text(record, self.today, speech=True)}
        if value:
            text_vals["date"] = date_text(value, self.today)
            speech_vals["date"] = date_speech(value, self.today)
        return text_vals, speech_vals


# ── Записи ────────────────────────────────────────────

def draft_record(draft: dict, marks: list[str]) -> dict:
    """Черновик в формате Record: id null, метка draft (раздел 6)."""
    return {
        "id": None, "type": draft["type"], "text": draft["text"], "date": draft.get("date"),
        "time": draft.get("time"), "end_time": draft.get("end_time"), "date_kind": draft["date_kind"],
        "status": None if draft["type"] in ("meeting", "busy") else "open",
        "created_at": None, "marks": marks, "highlight": None,
    }


async def owner_records(ctx: Ctx, *conditions) -> list[Record]:
    rows = await ctx.db.execute(select(Record).where(ctx.owner.filter(), Record.deleted_at.is_(None), *conditions))
    return list(rows.scalars().all())


def moment(record: Record) -> str:
    return f"{record.date or '9999-99-99'} {record.time or '00:00'}"


def is_future(record: Record, now: datetime) -> bool:
    if not record.date:
        return False
    if record.date != now.strftime("%Y-%m-%d"):
        return record.date > now.strftime("%Y-%m-%d")
    return not record.time or record.time >= now.strftime("%H:%M")


def record_words(record: Record) -> list[str]:
    return [w.strip(".,:;!?«»\"()") for w in record.text.replace("-", " ").split()]


def matches(record: Record, keys: list[str]) -> int:
    words = record_words(record)
    return sum(1 for key in keys if nlu.record_has(words, key))


def nearest_first(records: list[Record], now: datetime) -> list[Record]:
    """Ближайшие будущие по возрастанию, затем прошедшие от поздних к ранним."""
    future = sorted([r for r in records if is_future(r, now)], key=moment)
    past = sorted([r for r in records if not is_future(r, now)], key=moment, reverse=True)
    return future + past


def one_per_series(records: list[Record], now: datetime) -> list[Record]:
    """Из каждого повтора — только ближайший день: поиск не должен выдавать 50 планёрок."""
    keep, seen = set(), set()
    for record in nearest_first(records, now):
        if record.series_id:
            if record.series_id in seen:
                continue
            seen.add(record.series_id)
        keep.add(record.id)
    return [r for r in records if r.id in keep]


async def find(ctx: Ctx, p: nlu.Parse, types: set[str] | None = None) -> list[Record]:
    conditions = []
    if p.filter_type:
        conditions.append(Record.type == TaskType(p.filter_type))
    elif types:
        conditions.append(Record.type.in_([TaskType(t) for t in types]))
    if p.period:
        conditions += [Record.date >= p.period.start.isoformat(), Record.date <= p.period.end.isoformat()]
    if p.date:
        conditions.append(Record.date == p.date.isoformat())
    rows = await owner_records(ctx, *conditions)
    if p.next:
        # «Какое следующее дело» — не «Работа» по будням: занятость только если о ней спросили
        rows = [r for r in rows if is_future(r, ctx.now) and (r.type != TaskType.busy or p.filter_type == "busy")]
    if not p.keywords:
        return rows if (p.filter_type or p.period or p.date or p.next) else []
    return by_keywords(rows, p)


def by_keywords(rows: list[Record], p: nlu.Parse) -> list[Record]:
    if p.required:
        rows = [r for r in rows if all(nlu.record_has(record_words(r), key) for key in p.required)]
    scored = [(matches(r, p.keywords), r) for r in rows]
    best = [r for score, r in scored if score == len(p.keywords)]
    return best or [r for score, r in scored if score > 0]


# ── Создание: проверки раздела 8 ──────────────────────

def clarify(ctx: Ctx, template_id: str, draft: dict, quick: list[tuple[str, str]] | None = None,
            values: dict | None = None) -> dict:
    # Вопрос храним в черновике: непонятный ответ — задаём его ещё раз, контекст не теряем
    draft = {**draft, "ask": {"template": template_id, "values": values, "quick": quick}}
    ctx.conv.pending = Pending("draft", draft)
    text, speech = ctx.say(template_id, values)
    buttons = [button_text(label, send) for label, send in (quick or [])] + [CANCEL]
    return reply("clarify", text, speech, awaiting=True, buttons=buttons)


def ask_again(ctx: Ctx, pending: Pending) -> dict | None:
    """Ответ на уточнение не понят: повторяем тот же вопрос. None — вопроса нет, разбираем как новый запрос."""
    ask = pending.draft.get("ask")
    if not ask:
        return None
    return clarify(ctx, ask["template"], pending.draft, ask["quick"], ask["values"])


async def free_times(ctx: Ctx, day: str | None, kind: str) -> list[tuple[str, str]]:
    """Кнопки к вопросу «во сколько»: ближайшие свободные часы в этот день."""
    target = parse_date(day) if day else ctx.today
    if not target or target < ctx.today:
        target = ctx.today
    start = DAY_START
    if target == ctx.today:
        now = ctx.now.hour * 60 + ctx.now.minute
        start = max(DAY_START, (now // 60 + 1) * 60)
    rows = await owner_records(ctx, Record.date == target.isoformat(), Record.time.is_not(None))
    busy = [span(r) for r in rows if r.status != TaskStatus.done]
    need = LENGTH.get(kind, 30)
    starts = [t for t in window_starts(free_windows(busy, start, need), need) if t % 60 == 0][:FREE_BUTTONS]
    return [(hhmm(t), f"в {hhmm(t)}") for t in starts]


FREE_BUTTONS = 3


async def process_draft(ctx: Ctx, draft: dict) -> dict:
    if draft.get("repeat"):
        # Повтор: первый день — ближайший подходящий, сегодняшний только если время ещё впереди
        start = max(parse_date(draft.get("date")) or ctx.today, ctx.today)
        # У занятости сегодняшний день подходит, пока она не кончилась: «по будням работа с 9 до 18» в 10 утра
        until_time = draft.get("end_time") if draft["type"] == "busy" and draft.get("end_time", "") > (draft.get("time") or "") else draft.get("time")
        draft = {**draft, "date": nlu.first_day(draft["repeat"]["days"], start, ctx.now, until_time).isoformat(),
                 "date_kind": "exact"}
    kind, has_date, has_time = draft["type"], bool(draft.get("date")), bool(draft.get("time"))
    times = await free_times(ctx, draft.get("date"), kind) if not has_time else []
    days = [("Сегодня", "сегодня"), ("Завтра", "завтра")]
    if has_time and draft["time"] <= ctx.now.strftime("%H:%M"):
        days = days[1:]  # «в 9 утра» в полдень — сегодняшнее 9:00 уже прошло

    if not draft.get("text") and kind == "busy":
        return clarify(ctx, "clarify_busy_what", draft)
    if not draft.get("text"):
        ctx.conv.pending = None
        text, speech = ctx.say("clarify_generic")
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    if kind == "meeting" and not draft.get("has_subject", True):
        return clarify(ctx, "clarify_meeting_person", draft)
    if kind == "busy":
        # Занятость — всегда интервал: начало, конец и день (или повтор)
        if not has_time:
            return clarify(ctx, "clarify_busy_time", draft)
        if not draft.get("end_time"):
            return clarify(ctx, "clarify_busy_end", draft, values={"time": draft["time"]})
        if not has_date:
            return clarify(ctx, "clarify_day", draft, days)
    if kind == "reminder":
        if not has_date and not has_time:
            return clarify(ctx, "clarify_reminder_when", draft)
        if not has_time:
            return clarify(ctx, "clarify_reminder_time", draft, times)
        if not has_date:
            return clarify(ctx, "clarify_day", draft, days)
    if kind == "meeting":
        if not has_time:
            return clarify(ctx, "clarify_meeting_time", draft, times)
        if not has_date:
            return clarify(ctx, "clarify_day", draft, days)
    if kind == "task" and has_time and not has_date:
        return clarify(ctx, "clarify_day", draft, days)
    if kind == "task" and not has_date:
        draft["date_kind"], draft["time"] = "none", None
    elif draft.get("date_kind") == "none":
        draft["date_kind"] = "exact"

    # Дата или время в прошлом (Р-36)
    today = ctx.today.isoformat()
    # Занятость уже идёт («сегодня с 9 до 18», а сейчас 10) — не прошлое, пока не кончилась
    ends = draft.get("end_time") if kind == "busy" and draft.get("end_time", "") > (draft.get("time") or "") else draft.get("time")
    if has_date and (draft["date"] < today or (draft["date"] == today and has_time
                                              and draft["date_kind"] == "exact"
                                              and ends <= ctx.now.strftime("%H:%M"))):
        past = dict(draft)
        values, _ = ctx.record_values(draft_record(draft, []))
        if draft["date"] < today:
            past["date"] = None
        past["time"] = None if kind != "task" else past.get("time")
        # «Сегодня в 8 утра», а уже 10 — предлагаем завтра в то же время
        quick = [(f"Завтра в {draft['time']}", f"завтра в {draft['time']}")] \
            if draft["date"] == today and has_time else None
        return clarify(ctx, "clarify_date_past", past, quick, values={"when": values["when"]})

    # Высокий риск ошибки — «правильно понял?» (Р-19); разбор неуверенный — «верно понял?» с причиной
    if (draft.get("ambiguous") or draft.get("doubt")) and not draft.get("confirmed"):
        ctx.conv.pending = Pending("confirm", draft)
        values, speech_values = ctx.record_values(draft_record(draft, []))
        values["type"] = TYPE_WORD[kind]
        if draft.get("doubt"):
            values["hint"] = doubt_hint(draft["doubt"])
            text, speech = ctx.say("confirm_doubt", values, speech_values)
        else:
            text, speech = ctx.say("confirm", values, speech_values)
        return reply("confirm", text, speech, records=[draft_record(draft, ["draft"])], awaiting=True, buttons=[
            button_action("Да, верно", "confirm_yes"),
            button_action("Нет", "confirm_no", style="secondary"),
        ])

    # Встреча попадает на занятость: «В это время у вас «Работа» (09:00–18:00)»
    if kind == "meeting" and not draft.get("keep_both") and not draft.get("busy_ok"):
        start = minutes(draft["time"])
        end = minutes(draft["end_time"]) if draft.get("end_time") else start + LENGTH["meeting"]
        end = end if end > start else 24 * 60
        overlap = [r for r in await owner_records(ctx, Record.type == TaskType.busy, Record.date == draft["date"],
                                                  Record.time.is_not(None))
                   if span(r)[0] < end and start < span(r)[1]]
        if overlap:
            ctx.conv.pending = Pending("conflict", {**draft, "busy_clash": True})
            first = sorted(overlap, key=moment)[0]
            text, speech = ctx.say("conflict_busy", {"text": first.text, "time": first.time,
                                                     "end": first.end_time or ""})
            records = [draft_record(draft, ["draft"])] + [r.to_contract(marks=["conflict"]) for r in overlap]
            return reply("conflict", text, speech, records=records, awaiting=True, buttons=[
                button_action("Другое время", "change_new_time"),
                button_action("Всё равно записать", "keep_both"),
                CANCEL,
            ])

    # Совпадение встреч: только встречи, по точному времени начала (Р-26)
    if kind == "meeting" and not draft.get("keep_both"):
        busy = await owner_records(ctx, Record.type == TaskType.meeting, Record.date == draft["date"],
                                   Record.time == draft["time"])
        if busy:
            ctx.conv.pending = Pending("conflict", draft)
            records = [draft_record(draft, ["draft"])] + [r.to_contract(marks=["conflict"]) for r in busy]
            if len(busy) == 1:
                text, speech = ctx.say("conflict_one", {"time": draft["time"], "text": busy[0].text})
            else:
                values, _ = ctx.record_values(draft_record(draft, []))
                text, speech = ctx.say("conflict_many", {"time": draft["time"], "count": len(busy),
                                                         "when": values["when"]})
            # «Перенести существующую» не присылаем: изменение фразой (Д1) не делаем (Р-28)
            return reply("conflict", text, speech, records=records, awaiting=True, buttons=[
                button_action("Другое время для новой", "change_new_time"),
                button_action("Оставить обе", "keep_both"),
                CANCEL,
            ])

    return await save(ctx, draft)


DENSE_MINUTES = 30  # порог плотного графика из отчёта аналитика: 12% пар встреч


def minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


async def dense_neighbours(ctx: Ctx, draft: dict) -> list[Record]:
    """Ближайшие встречи до и после новой, если до них меньше 30 минут. Точное совпадение — это конфликт."""
    if draft["type"] != "meeting" or not draft.get("time") or draft.get("keep_both"):
        return []
    same_day = await owner_records(ctx, Record.type == TaskType.meeting, Record.date == draft["date"],
                                   Record.time.is_not(None))
    start = minutes(draft["time"])
    before = [r for r in same_day if 0 < start - minutes(r.time) < DENSE_MINUTES]
    after = [r for r in same_day if 0 < minutes(r.time) - start < DENSE_MINUTES]
    near = []
    if before:
        near.append(max(before, key=lambda r: minutes(r.time)))
    if after:
        near.append(min(after, key=lambda r: minutes(r.time)))
    return near


async def save(ctx: Ctx, draft: dict) -> dict:
    ctx.conv.pending = None
    if draft.get("repeat"):
        return await save_series(ctx, draft)
    kind = draft["type"]
    near = await dense_neighbours(ctx, draft)
    record = ctx.owner.new_record(
        type=TaskType(kind), text=draft["text"][:300], date=draft.get("date"), time=draft.get("time"),
        end_time=draft.get("end_time") if draft.get("time") else None, date_kind=DateKind(draft["date_kind"]),
        status=None if kind in ("meeting", "busy") else TaskStatus.open,
        created_at=datetime.now(timezone.utc),
    )
    ctx.db.add(record)
    await ctx.db.commit()
    await ctx.db.refresh(record)
    ctx.conv.focus = record.id
    data = record.to_contract(marks=["new"])
    values, speech_values = ctx.record_values(data)
    if draft.get("keep_both"):
        template = "conflict_keep_both"
    elif kind == "busy":
        template = "created_busy"
    elif kind == "task":
        template = {"exact": "created_task", "deadline": "created_task_deadline", "none": "created_task_nodate"}[draft["date_kind"]]
    else:
        template = f"created_{kind}"
    text, speech = ctx.say(template, values, speech_values)
    if near:
        # Плотный график: встречу сохраняем, показываем её и соседей — любую можно открыть и поправить
        gap = min(abs(minutes(r.time) - minutes(record.time)) for r in near)
        tail, tail_speech = ctx.say("dense_schedule", {"count": gap})
        text = f"{text} {tail}"
        speech = f"{speech} {tail_speech}" if speech and tail_speech else speech
        others = sorted([data] + [r.to_contract() for r in near], key=lambda r: r["time"])
        return reply("created", text, speech, records=others, buttons=undo_button(ctx, record))
    return reply("created", text, speech, records=[data], buttons=undo_button(ctx, record))


async def save_series(ctx: Ctx, draft: dict) -> dict:
    """«Каждый вторник в 10 планёрка»: все дни сразу, в ответе — ближайший."""
    days = draft["repeat"]["days"]
    rule = series.rule_text(days, draft.get("time"), draft.get("end_time"))
    until = parse_date(draft["repeat"].get("until"))
    created, records = await series.create(ctx.db, ctx.owner, draft, ctx.today)
    if not records:
        text, speech = ctx.say("series_none", {"until": series.until_text(until, ctx.today) if until else "",
                                               "rule": rule})
        return reply("clarify", text, speech)
    first = records[0]
    ctx.conv.focus = first.id
    day = parse_date(first.date)
    values = {"text": first.text, "rule": rule, "first": date_text(day, ctx.today), "count": len(records)}
    speech_values = {"first": date_speech(day, ctx.today)}
    if until:
        values["until"] = series.until_text(until, ctx.today)
    text, speech = ctx.say("created_series_until" if until else "created_series", values, speech_values)
    return reply("created", text, speech, records=[first.to_contract(marks=["new"])], total=len(records),
                 buttons=undo_button(ctx, first))


# ── Несколько дел одной фразой ────────────────────────

def ready_draft(ctx: Ctx, draft: dict) -> bool:
    """Можно записать без уточнений: всё нужное есть и не в прошлом."""
    kind = draft["type"]
    if not draft.get("text") or (kind == "meeting" and not draft.get("has_subject", True)):
        return False
    if kind in ("meeting", "reminder") and not (draft.get("date") and draft.get("time")):
        return False
    if kind == "busy" and not (draft.get("date") and draft.get("time") and draft.get("end_time")):
        return False
    if kind == "task" and draft.get("time") and not draft.get("date"):
        return False
    today = ctx.today.isoformat()
    if draft.get("date") and (draft["date"] < today or (draft["date"] == today and draft.get("time")
                                                        and draft["time"] <= ctx.now.strftime("%H:%M"))):
        return False
    if kind == "task" and not draft.get("date"):
        draft["date_kind"], draft["time"] = "none", None
    elif draft.get("date_kind") == "none":
        draft["date_kind"] = "exact"
    return True


def needs_only_time(ctx: Ctx, draft: dict) -> bool:
    """Встреча или напоминание, где есть всё, кроме времени: спросим его в общем списке."""
    if draft["type"] not in ("meeting", "reminder") or draft.get("time") or not draft.get("date"):
        return False
    return ready_draft(ctx, {**draft, "time": "23:59"})


async def ask_many(ctx: Ctx, drafts: list[dict]) -> dict:
    """Спрашиваем время по очереди: «Во сколько встреча с Машей завтра?». Все ответили — записываем."""
    missing = [i for i, d in enumerate(drafts) if not d.get("time")]
    if not missing:
        return await save_many(ctx, drafts)
    i = missing[0]
    draft = drafts[i]
    ctx.conv.pending = Pending("multi_ask", {"drafts": drafts, "i": i})
    day = parse_date(draft["date"])
    diff = (day - ctx.today).days
    when = {0: "сегодня", 1: "завтра", 2: "послезавтра"}.get(diff) or f"{day.day} {MONTHS_GEN[day.month - 1]}"
    what = draft["text"][:1].lower() + draft["text"][1:]
    template = "clarify_many_time" if draft["type"] == "meeting" else "clarify_many_reminder"
    text, speech = ctx.say(template, {"text": what, "day": when})
    records = [draft_record(d, ["draft"]) for d in drafts]
    buttons = [button_text(label, send) for label, send in await free_times(ctx, draft["date"], draft["type"])]
    return reply("clarify", text, speech, records=records, awaiting=True, buttons=buttons + [CANCEL])


async def answer_multi_ask(ctx: Ctx, pending: Pending, raw: str) -> dict | None:
    """Ответ на «Во сколько …?» в списке дел. None — времени в ответе нет."""
    time, _, _, _ = nlu.parse_when(raw, ctx.now)
    if not time:
        time, _ = nlu.reply_time(raw)
    if not time:
        return None
    drafts = [dict(d) for d in pending.draft["drafts"]]
    drafts[pending.draft["i"]]["time"] = time
    return await ask_many(ctx, drafts)


async def answer_many(ctx: Ctx, parses: list[nlu.Parse]) -> dict | None:
    """Несколько дел: показываем все карточки и спрашиваем «Записать все?». None — не вышло, разбираем как одно."""
    drafts = [draft_from(p, ctx.today) for p in parses]
    if any(d.get("repeat") for d in drafts):
        return None  # повтор записываем по одному
    if not all(ready_draft(ctx, d) for d in drafts):
        if all(ready_draft(ctx, d) or needs_only_time(ctx, d) for d in drafts):
            return await ask_many(ctx, drafts)
        return None
    ctx.conv.pending = Pending("multi", {"drafts": drafts})
    text, speech = ctx.say("multi_confirm", {"count": len(drafts)})
    return reply("confirm", text, speech, records=[draft_record(d, ["draft"]) for d in drafts], awaiting=True,
                 buttons=[button_action("Да, записать все", "confirm_yes"),
                          button_action("Нет", "cancel", style="secondary")])


async def save_many(ctx: Ctx, drafts: list[dict]) -> dict:
    ctx.conv.pending = None
    saved = []
    for draft in drafts:
        record = ctx.owner.new_record(
            type=TaskType(draft["type"]), text=draft["text"][:300], date=draft.get("date"), time=draft.get("time"),
            date_kind=DateKind(draft["date_kind"]),
            end_time=draft.get("end_time") if draft.get("time") else None,
            status=None if draft["type"] in ("meeting", "busy") else TaskStatus.open,
            created_at=datetime.now(timezone.utc),
        )
        ctx.db.add(record)
        saved.append(record)
    await ctx.db.commit()
    for record in saved:
        await ctx.db.refresh(record)
    text, speech = ctx.say("multi_created", {"count": len(saved)})
    return reply("created", text, speech, records=[r.to_contract(marks=["new"]) for r in saved])


# ── Свободное время ───────────────────────────────────

DAY_START, DAY_END = 9 * 60, 21 * 60   # ищем окна с 9 до 21
STEP = 30                               # кандидаты каждые полчаса
LENGTH = {"meeting": 60}                # встреча занимает час, остальное с временем — полчаса


def span(record: Record) -> tuple[int, int]:
    """Сколько минут дня занимает запись: занятость и встреча «с … до …» — весь интервал, иначе по LENGTH.
    Интервал через полночь («с 22 до 6») в этот день занимает время до конца суток."""
    start = minutes(record.time)
    if record.end_time:
        end = minutes(record.end_time)
        return start, end if end > start else 24 * 60
    return start, start + LENGTH.get(record.type.value if record.type else "", 30)


def hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def free_windows(busy: list[tuple[int, int]], start: int, need: int) -> list[tuple[int, int]]:
    """Все свободные промежутки дня (начало, конец), куда помещается дело длиной need."""
    windows: list[tuple[int, int]] = []
    t = start
    for b, e in sorted(busy) + [(DAY_END, DAY_END)]:
        if b - t >= need:
            windows.append((t, min(b, DAY_END)))
        t = max(t, e)
        if t >= DAY_END:
            break
    return windows


def window_starts(windows: list[tuple[int, int]], need: int) -> list[int]:
    """Кнопки: начало каждого окна и дальше каждый ровный час, пока дело помещается."""
    starts: list[int] = []
    for b, e in windows:
        t = b
        while t + need <= e:
            starts.append(t)
            t = (t // 60 + 1) * 60
    return starts


def length_text(total: int) -> str:
    """Длина окна словами: «полчаса», «час», «3 часа», «2 часа 30 минут»."""
    if total in (30, 60, 90):
        return {30: "полчаса", 60: "час", 90: "полтора часа"}[total]
    hours, mins = divmod(total, 60)
    parts = [plural(hours, "час", "часа", "часов")] if hours else []
    if mins:
        parts.append(plural(mins, "минута", "минуты", "минут"))
    return " ".join(parts)


def spans_text(windows: list[tuple[int, int]]) -> str:
    spans = [f"{hhmm(b)}–{hhmm(e)}" for b, e in windows]
    return spans[0] if len(spans) == 1 else ", ".join(spans[:-1]) + " и " + spans[-1]


def day_send(ctx: Ctx, day: date) -> str:
    """Как назвать день в тексте кнопки: «сегодня», «завтра», «23 октября»."""
    return {0: "сегодня", 1: "завтра"}.get((day - ctx.today).days) or f"{day.day} {MONTHS_GEN[day.month - 1]}"


def first_minute(ctx: Ctx, day: date) -> int:
    """С какой минуты ищем окна: сегодня — с ближайшего получаса, иначе с 9:00."""
    if day == ctx.today:
        return max(DAY_START, (ctx.now.hour * 60 + ctx.now.minute) // STEP * STEP + STEP)
    return DAY_START


def busy_of(rows: list[Record]) -> list[tuple[int, int]]:
    return [span(r) for r in rows if r.time and r.status != TaskStatus.done]


async def answer_free(ctx: Ctx, p: nlu.Parse) -> dict:
    if p.free_mode in ("day", "week"):
        return await answer_free_days(ctx, p)
    day = max(p.date or ctx.today, ctx.today)
    rows = await owner_records(ctx, Record.date == day.isoformat(), Record.time.is_not(None))
    windows = free_windows(busy_of(rows), first_minute(ctx, day), p.window)
    day_word = date_text(day, ctx.today)
    send_day = f"{day.day} {MONTHS_GEN[day.month - 1]}"
    what = p.subject or "Встреча"
    if p.time and p.free_mode == "windows" and day >= ctx.today:
        return free_at(ctx, p, day, rows, windows, what, send_day)
    if not windows:
        text, speech = ctx.say("free_none", {"day": day_word, "need": length_text(p.window)})
        return reply("found", text, speech, total=0)
    if p.free_mode == "longest":
        # «Самые свободные часы завтра», «найди большое окно»: самое длинное первым, остальные — по порядку
        best = max(windows, key=lambda w: (w[1] - w[0], -w[0]))
        others = [w for w in windows if w != best]
        values = {"day": day_word, "slot": spans_text([best]), "length": length_text(best[1] - best[0]),
                  "slots": spans_text(others) if others else ""}
        text, speech = ctx.say("free_longest_more" if others else "free_longest", values)
        need = min(p.duration, best[1] - best[0])
        buttons = [button_text(hhmm(t), f"{what} {send_day} в {hhmm(t)}") for t in window_starts([best], need)]
        return reply("clarify", text, speech, buttons=buttons)
    # В тексте — все окна промежутками, кнопки — каждый час внутри тех, куда помещается встреча
    times = [hhmm(t) for t in window_starts([w for w in windows if w[1] - w[0] >= p.duration], p.duration)]
    text, speech = ctx.say("free_slots" if p.subject else "free_slots_plain",
                           {"day": day_word, "slots": spans_text(windows), "need": length_text(p.duration),
                            "text": p.subject or ""})
    buttons = [button_text(t, f"{what} {send_day} в {t}") for t in times]
    return reply("clarify", text, speech, buttons=buttons)


def free_at(ctx: Ctx, p: nlu.Parse, day: date, rows: list[Record], windows: list[tuple[int, int]], what: str,
            send_day: str) -> dict:
    """«Я свободен в пятницу в 15?» — да или нет, а если занято, то с чем и где окна."""
    start = minutes(p.time)
    end = start + p.duration
    clash = [r for r in rows if r.time and r.status != TaskStatus.done
             and span(r)[0] < end and start < span(r)[1]]
    values = {"day": date_text(day, ctx.today), "time": p.time}
    if not clash:
        text, speech = ctx.say("free_at_yes", values)
        return reply("clarify", text, speech, buttons=[button_text(f"Поставить на {p.time}", f"{what} {send_day} в {p.time}")])
    first = sorted(clash, key=moment)[0]
    values["text"] = first.text
    if not windows:
        text, speech = ctx.say("free_at_full", values)
        return reply("found", text, speech, records=[first.to_contract()])
    values["slots"] = spans_text(windows)
    text, speech = ctx.say("free_at_no", values)
    times = [hhmm(t) for t in window_starts([w for w in windows if w[1] - w[0] >= p.duration], p.duration)]
    return reply("clarify", text, speech, records=[first.to_contract()],
                 buttons=[button_text(t, f"{what} {send_day} в {t}") for t in times])


@dataclass
class DayLoad:
    day: date
    count: int                       # дел на день, кроме выполненных
    windows: list[tuple[int, int]]   # свободные окна от получаса
    score: int                       # свободные минуты минус полчаса на каждое дело без времени


def free_period(ctx: Ctx, p: nlu.Parse) -> nlu.Period:
    """Где сравниваем: сказанный период, иначе для дней — эта неделя, для недель — 4 недели вперёд."""
    if p.period:
        return p.period
    if p.date:
        s, e = week_bounds(p.date)
        return nlu.describe_period(s, e, ctx.today)
    if p.free_mode == "week":
        return nlu.Period(ctx.today, ctx.today + timedelta(days=27), "в ближайшие 4 недели", "в ближайшие 4 недели")
    s, e = week_bounds(ctx.today)
    if ctx.today.weekday() >= 5 and not p.weekends:
        s, e = week_bounds(ctx.today + timedelta(days=7))
        return nlu.Period(s, e, "на следующей неделе", "на следующей неделе")
    return nlu.Period(s, e, "на этой неделе", "на этой неделе")


async def day_loads(ctx: Ctx, start: date, end: date) -> list[DayLoad]:
    rows = await owner_records(ctx, Record.date >= start.isoformat(), Record.date <= end.isoformat())
    by_day: dict[str, list[Record]] = {}
    for r in rows:
        if r.status != TaskStatus.done:
            by_day.setdefault(r.date, []).append(r)
    busy_by_day: dict[str, list[Record]] = {}
    for day_rows in by_day.values():
        for r in [x for x in day_rows if x.type == TaskType.busy]:
            busy_by_day.setdefault(r.date, []).append(r)
            day_rows.remove(r)
    loads = []
    for n in range((end - start).days + 1):
        day = start + timedelta(days=n)
        records = by_day.get(day.isoformat(), [])
        # Занятость не дело, но время её занято: окна считаем с ней
        windows = free_windows(busy_of(records + busy_by_day.get(day.isoformat(), [])), first_minute(ctx, day), STEP)
        untimed = sum(1 for r in records if not r.time)
        loads.append(DayLoad(day, len(records), windows, sum(e - b for b, e in windows) - STEP * untimed))
    return loads


def load_text(load: DayLoad) -> str:
    if not load.count:
        return "ни одного дела"
    text = plural(load.count, "дело", "дела", "дел")
    if load.windows:
        best = max(load.windows, key=lambda w: (w[1] - w[0], -w[0]))
        return f"{text}, самое большое окно {spans_text([best])}"
    return f"{text}, свободного времени почти нет"


async def answer_free_days(ctx: Ctx, p: nlu.Parse) -> dict:
    """«Какой день самый свободный на этой неделе», «самая свободная неделя в октябре»."""
    period = free_period(ctx, p)
    start = max(period.start, ctx.today)
    if start > period.end:
        text, speech = ctx.say("free_period_past", {"period": period.label})
        return reply("found", text, speech, total=0)
    loads = await day_loads(ctx, start, period.end)
    # Выходные сравниваем, только если о них спросили или будних не осталось
    workdays = [x for x in loads if x.day.weekday() < 5]
    only_work = bool(workdays) and not p.weekends and len(workdays) < len(loads)

    def best_days(days: list[DayLoad]) -> list[DayLoad]:
        pool = [x for x in days if x.day.weekday() < 5] if not p.weekends else days
        return sorted(pool or days, key=lambda x: (-x.score, x.day))

    if p.free_mode == "week":
        weeks: dict[date, list[DayLoad]] = {}
        for x in loads:
            weeks.setdefault(week_bounds(x.day)[0], []).append(x)

        def week_score(days: list[DayLoad]) -> float:
            pool = best_days(days)
            return sum(x.score for x in pool) / len(pool)

        chosen = max(weeks.values(), key=lambda days: (week_score(days), -days[0].day.toordinal()))
        top = best_days(chosen)
        first, last = chosen[0].day, chosen[-1].day
        week = date_text(first, ctx.today) if first == last else nlu._range_label(first, last)[0]
        week_speech = date_speech(first, ctx.today) if first == last else nlu._range_label(first, last)[1]
        count = sum(x.count for x in chosen)
        values = {"period": period.label, "week": week, "day": date_text(top[0].day, ctx.today),
                  "load": plural(count, "дело", "дела", "дел") if count else "ни одного дела"}
        text, speech = ctx.say("free_week", values, {"period": period.label_speech, "week": week_speech})
    else:
        top = best_days(loads)
        values = {"period": period.label, "day": date_text(top[0].day, ctx.today), "load": load_text(top[0]),
                  "note": " Выходные не считал." if only_work else ""}
        text, speech = ctx.say("free_day", values, {"period": period.label_speech, "day": date_speech(top[0].day, ctx.today)})
    buttons = [button_text(capitalize(date_text(x.day, ctx.today)), f"Найди свободное окно {day_send(ctx, x.day)}")
               for x in top[:3]]
    return reply("clarify", text, speech, buttons=buttons)


def undo_button(ctx: Ctx, record: Record) -> list[dict]:
    """Д10: «Отменить» под каждым created с одной сохранённой записью (15.4)."""
    return [button_action("Отменить", "undo", record.id, style="secondary")] if "d10" in ctx.features else []


DOUBT_HINTS = {
    "typo": "«{word}» — полагаю, «{right}».",
    "or": "Вы назвали два варианта, я взял первый.",
    "fix": "Беру то, что вы назвали последним.",
    "vague_time": "«{word}» понял как {time}.",
    "vague": "«{word}» — это без точного срока.",
    "hedge": "Мне послышалось сомнение.",
    "unclear": "Разобрал не всё.",
    "llm": "Фраза непростая, хочу убедиться.",
}


def doubt_hint(doubt: list[dict]) -> str:
    """Почему переспрашиваем — первая причина словами Стивенса."""
    first = doubt[0]
    key = "vague_time" if first["why"] == "vague" and first.get("time") else first["why"]
    return DOUBT_HINTS.get(key, DOUBT_HINTS["unclear"]).format(**first)


def draft_from(p: nlu.Parse, today: date) -> dict:
    draft = {
        "type": p.type or "task",
        "text": p.text,
        "date": p.date.isoformat() if p.date else None,
        "time": p.time,
        "end_time": p.end_time,
        "date_kind": p.date_kind if p.date else "none",
        "ambiguous": p.time_ambiguous,
        "has_subject": p.has_subject,
    }
    if p.doubt:
        draft["doubt"] = p.doubt
    if p.repeat_days:
        draft["repeat"] = {"days": p.repeat_days, "until": p.until.isoformat() if p.until else None}
    if p.date:
        draft["date_kind"] = p.date_kind
    elif draft["type"] == "task" and p.vague_period and p.period is None:
        draft["date_kind"] = "none"
    return draft


async def answer_confirm_fix(ctx: Ctx, pending: Pending, rest: str) -> dict | None:
    """«Верно понял?» → «нет, в 11» / «нет, позвонить папе»: сразу поправляем черновик, без «что поправить?»."""
    draft = {**pending.draft, "ambiguous": False, "doubt": []}
    if merged := merge_reply(Pending("confirm_no", draft), rest, ctx.now):
        return await process_draft(ctx, merged)
    p = nlu.parse(rest, ctx.now)
    if p.intent != "create" or not p.text:
        return None
    new = draft_from(p, ctx.today)
    if new["type"] == "task":
        new["type"] = draft["type"]  # «нет, позвонить папе» — тип не назван, остаётся прежний
    for key in ("date", "time", "date_kind"):
        if not new.get(key) or new.get(key) == "none":
            new[key] = draft.get(key)
    return await process_draft(ctx, {**new, "confirmed": True})


def merge_reply(pending: Pending, raw: str, now: datetime) -> dict | None:
    """Ответ на уточнение: «в 15», «завтра», «с Иваном». Не ответ — None."""
    draft = dict(pending.draft)
    if draft["type"] == "busy":
        return merge_busy(draft, raw, now)
    time, ambiguous, time_spans, info = nlu.parse_when(raw, now)
    rest = " ".join(nlu._cut(raw, info.spans + time_spans).split()).strip(" ,.!?")
    changed = False
    if info.date:
        draft["date"] = info.date.isoformat()
        draft["date_kind"] = info.kind if draft["type"] == "task" else "exact"
        changed = True
    half = re.fullmatch(r"(?:(?:нет|да)[\s,]+)?(утра|утром|вечера|вечером|дня|днём|днем|ночи)[\s.!]*", raw.strip(), re.I)
    if half and draft.get("time") and not time:
        # «Напомни в 7» → «вечера»: уточнили половину суток
        h, m = map(int, draft["time"].split(":"))
        h = h % 12 + (12 if half.group(1).lower().startswith(("веч", "дн", "дё")) else 0)
        draft["time"], draft["ambiguous"] = f"{h:02d}:{m:02d}", False
        return draft
    if not time and not draft.get("time"):
        # Сбивчивый ответ «есть 10… стой… поставь 10» — берём последнее время
        time, ambiguous = nlu.reply_time(raw)
    if time:
        draft["time"] = time
        draft["ambiguous"] = ambiguous and pending.kind != "confirm_no"
        # Время назвали сами — расплывчатое «попозже» больше не смущает
        draft["doubt"] = [d for d in draft.get("doubt", []) if d["why"] != "vague"]
        if draft.get("date_kind") == "none":
            draft["date_kind"] = "exact"
        changed = True
    if draft["type"] == "meeting" and not draft.get("has_subject", True) and rest and not ACK_RE.match(rest):
        noun = draft.get("text") or "Встреча"
        joined = rest if rest.lower().split()[0] in ("с", "со", "по", "о", "об", "про") else f"с {rest}"
        draft["text"] = f"{noun} {joined}"
        draft["has_subject"] = True
        changed = True
    if pending.kind == "confirm_no":
        draft["confirmed"] = True
    return draft if changed else None


BUSY_END_RE = re.compile(r"^(?:(?:да|ну)[\s,]+)?(?:до\s+)?(\d{1,2})(?:[:.](\d{2}))?(?:\s*(?:час\w*))?(?:\s+(утра|дня|вечера|ночи))?[\s.!]*$", re.I)


def merge_busy(draft: dict, raw: str, now: datetime) -> dict | None:
    """Ответ на вопросы о занятости: «с 9 до 18», «до 18», «пары», «завтра». Не ответ — None."""
    changed = False
    if interval := nlu.parse_interval(raw):
        draft["time"], draft["end_time"], draft["ambiguous"] = interval[0], interval[1], interval[2]
        raw = nlu._cut(raw, [interval[3]])
        changed = True
    elif draft.get("time") and not draft.get("end_time") and (m := BUSY_END_RE.match(raw.strip())):
        h, mm = int(m.group(1)), int(m.group(2) or 0)
        part = (m.group(3) or "").lower()
        start = minutes(draft["time"])
        if part in ("вечера", "дня") and h < 12 or (not part and h < 12 and h * 60 + mm <= start and (h + 12) * 60 + mm > start):
            h += 12  # «с 9» → «до 6» — это 18:00
        if h < 24 and mm < 60 and h * 60 + mm != start:
            draft["end_time"] = f"{h:02d}:{mm:02d}"
            return draft
    time, _, time_spans, info = nlu.parse_when(raw, now)
    if info.date:
        draft["date"], draft["date_kind"] = info.date.isoformat(), "exact"
        changed = True
    if time and not draft.get("time"):
        draft["time"] = time
        changed = True
    rest = " ".join(nlu._cut(raw, info.spans + time_spans).split()).strip(" ,.!?:—-")
    if not draft.get("text") and rest and not ACK_RE.match(rest):
        p = nlu.parse("занят " + rest, now)
        if p.text:
            draft["text"] = p.text
            changed = True
    return draft if changed else None


# ── Разбор: правила, затем LLM ────────────────────────

def llm_enabled() -> bool:
    return bool(os.getenv("YANDEX_API_KEY") and os.getenv("YANDEX_FOLDER_ID"))


LLM_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)(?::\d{2})?$")


def llm_time(value: str | None) -> str | None:
    """Время от LLM: «9:30», «10:00:00» → «09:30», «10:00»; «25:99» и прочее — None."""
    m = LLM_TIME_RE.match((value or "").strip())
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else None


LLM_PAUSE = 60          # Яндекс не ответил — минуту не спрашиваем, сразу «не понял»
_llm_down_until = 0.0


async def llm_parse(text: str, now: datetime) -> nlu.Parse:
    """Если правила не поняли — спрашиваем LLM (brain.py) и переводим её ответ в Parse.
    Запрос к Яндексу синхронный — в отдельном потоке, чтобы остальные запросы не ждали."""
    global _llm_down_until
    if clock.time() < _llm_down_until:
        return nlu.Parse()
    try:
        from brain import parse_user_input
        intent = await asyncio.to_thread(parse_user_input, text)
    except Exception as error:  # noqa: BLE001 — LLM недоступна: «не понял»
        print(f"LLM недоступна: {error!r}")
        _llm_down_until = clock.time() + LLM_PAUSE
        return nlu.Parse()
    if not intent or intent.intent == "unknown":
        return nlu.Parse()
    today = now.date()
    tokens = {"today": 0, "tomorrow": 1, "day_after_tomorrow": 2, "yesterday": -1}
    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]

    def to_date(token: str | None) -> date | None:
        if not token:
            return None
        token = token.lower()
        if token in tokens:
            return today + timedelta(days=tokens[token])
        if token in weekdays:
            return today + timedelta(days=(weekdays.index(token) - today.weekday()) % 7)
        if token == "end_of_week":
            return nlu.week_bounds(today)[1]
        return parse_date(token)

    p = nlu.Parse()
    if intent.intent.startswith("create_"):
        p.intent, p.type = "create", intent.intent.removeprefix("create_")
        p.text = intent.text[:300] if intent.text else None
        p.date = to_date(intent.date)
        p.time = llm_time(intent.time)
        # «До» бывает только у задачи: встреча и напоминание — в точное время
        p.date_kind = (intent.date_kind if p.type == "task" else "exact") if p.date else "none"
        p.time_ambiguous = bool(intent.time_ambiguous)
        # Правила фразу не поняли, разобрала нейросеть — запись показываем черновиком с «верно понял?»
        p.doubt = [{"why": "llm"}]
    elif intent.intent in ("list", "search"):
        p.intent = intent.intent
        p.filter_type = intent.filter_type if intent.filter_type in TYPE_WORD else None
        if intent.intent == "search":
            words = " ".join(filter(None, [intent.keyword, *(intent.people or []), *(intent.organizations or [])]))
            p.keywords = nlu.keywords(words)
            p.ask, p.tense = intent.ask, intent.tense
        period = (intent.period or "today").lower()
        if period in ("this_week", "next_week"):
            start = nlu.week_bounds(today + timedelta(days=7 if period == "next_week" else 0))[0]
            label = "на этой неделе" if period == "this_week" else "на следующей неделе"
            p.period = nlu.Period(start, start + timedelta(days=6), label, label)
        elif intent.intent == "list":
            day = to_date(period) or today
            p.period = nlu.describe_period(day, day, today)
    else:
        p.intent = intent.intent
    return p


# ── Ответы по типу запроса ────────────────────────────

def examples(kind: str, template_id: str, ctx: Ctx, items: list[str]) -> dict:
    text, speech = ctx.say(template_id)
    return reply(kind, text, speech, buttons=[button_text(x) for x in items])


DAY_PARTS = {"утром": ("05:00", "11:59"), "днём": ("12:00", "16:59"), "вечером": ("17:00", "23:59")}


async def answer_list(ctx: Ctx, p: nlu.Parse) -> dict:
    period = p.period or nlu.describe_period(ctx.today, ctx.today, ctx.today)
    conditions = [Record.date.is_not(None), Record.date >= period.start.isoformat(), Record.date <= period.end.isoformat()]
    if p.filter_type:
        conditions.append(Record.type == TaskType(p.filter_type))
    rows = sorted(await owner_records(ctx, *conditions), key=lambda r: (r.date, r.time or "99:99", r.id))
    if p.day_part:
        # «Что у меня сегодня вечером» — только дела с временем в эту часть дня
        start, end = DAY_PARTS[p.day_part]
        rows = [r for r in rows if r.time and start <= r.time <= end]
        period = nlu.Period(period.start, period.end, f"{period.label} {p.day_part}", f"{period.label_speech} {p.day_part}")
    # Занятость не считаем делами: «На сегодня — 2 дела… Помимо этого вы заняты: «Работа» с 09:00 до 18:00»
    shown = rows
    busy_rows = [r for r in rows if r.type == TaskType.busy] if not p.filter_type else []
    rows = [r for r in rows if r.type != TaskType.busy] if busy_rows else rows
    values = {"period": period.label, "count": len(rows)}
    speech_values = {"period": period.label_speech}
    if busy_rows:
        values["items"] = busy_items(busy_rows, single_day=period.start == period.end)
    if not rows and busy_rows:
        text, speech = ctx.say("list_busy_only", values, speech_values)
        remember_shown(ctx, busy_rows)
        return await offer_undated(ctx, p, period, reply("list", text, speech, records=[r.to_contract() for r in busy_rows],
                                                         total=0))
    if rows:
        if p.ask == "exists" and p.filter_type:
            text, speech = ctx.say("list_type_yes", values, speech_values, count_forms=TYPE_FORMS[p.filter_type])
        else:
            # Про один тип — его формы вместо «дело|дела|дел» (раздел 9)
            forms = TYPE_FORMS[p.filter_type] if p.filter_type else None
            text, speech = ctx.say("list_some", values, speech_values, count_forms=forms)
        if busy_rows:
            tail, tail_speech = ctx.say("list_busy_tail", values)
            head = re.sub(r":\s*$", ".", text)
            text = f"{head} {tail}"
            speech = f"{speech or text} {tail_speech}" if tail_speech else speech
        remember_shown(ctx, shown)
        return await offer_undated(ctx, p, period,
                                   reply("list", text, speech, records=[r.to_contract() for r in shown], total=len(rows)))

    # Пусто — ближайшая будущая запись (того же типа, если спрашивали про тип) (Р-17)
    after = max(period.end + timedelta(days=1), ctx.today).isoformat()
    later = [r for r in await owner_records(ctx, Record.date.is_not(None), Record.date >= after,
                                            Record.type == TaskType(p.filter_type) if p.filter_type
                                            else Record.type.is_distinct_from(TaskType.busy))
             if is_future(r, ctx.now)]
    nearest = min(later, key=moment) if later else None
    if nearest:
        remember_shown(ctx, [nearest])  # «удали эту задачу» — про показанную ближайшую
        rec_values, rec_speech = ctx.record_values(nearest.to_contract())
        values.update(rec_values)
        speech_values.update({"when": rec_speech["when"]})
    if p.filter_type:
        values["types_gen"] = TYPE_GEN_PLURAL[p.filter_type]
        text, speech = ctx.say("list_type_empty" if nearest else "list_type_empty_alone", values, speech_values)
    else:
        text, speech = ctx.say("list_empty_nearest" if nearest else "list_empty_none", values, speech_values)
    return await offer_undated(ctx, p, period,
                               reply("list", text, speech, nearest=nearest.to_contract() if nearest else None, total=0))


def busy_items(rows: list[Record], single_day: bool) -> str:
    """«Работа» с 09:00 до 18:00 — за день; за несколько дней — только что: «Работа», «Пары»."""
    if single_day:
        items = [f"«{r.text}» с {r.time} до {r.end_time}" if r.time and r.end_time else f"«{r.text}»" for r in rows]
    else:
        items = list(dict.fromkeys(f"«{r.text}»" for r in rows))
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " и " + items[-1]


def remember_shown(ctx: Ctx, rows: list[Record]) -> None:
    """Показали записи — «удали её», «удали эти две» дальше про них, а не про последнюю созданную."""
    if len(rows) == 1:
        ctx.conv.focus = rows[0].id
    else:
        ctx.conv.focus = None
        ctx.conv.found = [r.id for r in rows[:FOUND_LIMIT]]


UNDATED_AHEAD = 14  # о задачах без срока напоминаем к спискам на сегодня, завтра, эту и следующую неделю


async def undated_tasks(ctx: Ctx) -> list[Record]:
    """Открытые задачи без срока, свежие сверху. Заметки сюда не входят."""
    rows = await owner_records(ctx, Record.type == TaskType.task, Record.date.is_(None),
                               Record.status == TaskStatus.open)
    return sorted(rows, key=lambda r: (r.created_at or datetime.min.replace(tzinfo=timezone.utc), r.id), reverse=True)


async def offer_undated(ctx: Ctx, p: nlu.Parse, period: nlu.Period, answer: dict) -> dict:
    """«…Кроме того, у вас 5 задач без срока. Показать?» — к спискам на ближайшие дни, ждём ответа голосом."""
    if p.filter_type not in (None, "task") or period.end < ctx.today:
        return answer
    if period.start > ctx.today + timedelta(days=UNDATED_AHEAD) or (period.end - period.start).days > 7:
        return answer
    count = len(await undated_tasks(ctx))
    if not count:
        return answer
    tail, tail_speech = ctx.say("undated_offer", {"count": count})
    spoken = answer["speech"] or answer["text"]
    # «Завтра 3 дела:» — двоеточие вело к карточкам, а теперь после него ещё фраза
    head = re.sub(r":\s*$", ".", answer["text"])
    answer["text"] = f"{head} {tail}"
    answer["speech"] = f"{spoken} {tail_speech}"
    answer["awaiting_reply"] = True
    answer["buttons"] = answer["buttons"] + [button_action("Показать", "show_undated"),
                                             button_action("Не сейчас", "skip_undated", style="secondary")]
    ctx.conv.pending = Pending("undated")
    return answer


async def answer_undated(ctx: Ctx) -> dict:
    ctx.conv.pending = None
    rows = await undated_tasks(ctx)
    if not rows:
        text, speech = ctx.say("undated_none")
        return reply("found", text, speech, total=0)
    if len(rows) == 1:
        ctx.conv.focus = rows[0].id
    else:
        ctx.conv.found = [r.id for r in rows[:FOUND_LIMIT]]
    text, speech = ctx.say("undated_some", {"count": len(rows)})
    return reply("found", text, speech, records=[r.to_contract() for r in rows[:FOUND_LIMIT]], total=len(rows))


async def answer_repeats(ctx: Ctx) -> dict:
    """«Покажи повторы» — по одной карточке на повтор, ближайший его день."""
    rows = [r for r in await owner_records(ctx, Record.series_id.is_not(None)) if is_future(r, ctx.now)]
    rows = nearest_first(one_per_series(rows, ctx.now), ctx.now)
    if not rows:
        text, speech = ctx.say("repeats_none")
        return reply("found", text, speech, total=0)
    if len(rows) == 1:
        ctx.conv.focus = rows[0].id
    else:
        ctx.conv.found = [r.id for r in rows[:FOUND_LIMIT]]
    items = []
    for r in rows[:FOUND_LIMIT]:
        rule = r.repeat_contract() or {"weekdays": [parse_date(r.date).weekday()]}
        items.append(f"{r.text} — {series.rule_text(rule['weekdays'], r.time, r.end_time)}")
    text, speech = ctx.say("repeats_some", {"count": len(rows)}, {"count": len(rows), "items": "; ".join(items)})
    return reply("found", text, speech, records=[r.to_contract() for r in rows[:FOUND_LIMIT]], total=len(rows))


def skip_undated(ctx: Ctx) -> dict:
    ctx.conv.pending = None
    text, speech = ctx.say("undated_skip")
    return reply("cancelled", text, speech)


async def answer_briefing(ctx: Ctx) -> dict:
    """Утренний брифинг: при первом открытии за день фронт присылает действие briefing без текста."""
    hour = ctx.now.hour
    greeting = "Доброе утро" if hour < 12 else "Добрый день" if hour < 18 else "Добрый вечер"
    rows = sorted([r for r in await owner_records(ctx, Record.date == ctx.today.isoformat())
                   if r.status != TaskStatus.done], key=lambda r: (r.time or "99:99", r.id))
    # Занятость — не дело: в план не считаем, а говорим отдельной фразой в конце
    busy_rows = [r for r in rows if r.type == TaskType.busy and r.end_time and r.end_time > ctx.now.strftime("%H:%M")]
    rows = [r for r in rows if r.type != TaskType.busy]
    if not rows and busy_rows:
        text, speech = ctx.say("briefing_busy_only", {"greeting": greeting,
                                                       "items": busy_items(busy_rows, single_day=True)})
        return reply("list", text, speech, records=[r.to_contract() for r in busy_rows], total=0)
    if not rows:
        text, speech = ctx.say("briefing_empty", {"greeting": greeting})
        return reply("list", text, speech, total=0)
    upcoming = [r for r in rows if r.time and r.time > ctx.now.strftime("%H:%M")]
    values = {"greeting": greeting, "count": len(rows)}
    if upcoming:
        values.update({"time": upcoming[0].time, "text": upcoming[0].text})
        text, speech = ctx.say("briefing_next", values)
    else:
        text, speech = ctx.say("briefing", values)
    # Плотные места: встречи, между началами которых меньше 30 минут
    meetings = [r for r in rows if r.type == TaskType.meeting and r.time]
    dense = [(a, b) for a, b in zip(meetings, meetings[1:]) if 0 < minutes(b.time) - minutes(a.time) < DENSE_MINUTES]
    if dense:
        a, b = dense[0]
        tail, tail_speech = ctx.say("briefing_dense", {"start": a.time, "end": b.time,
                                                       "count": minutes(b.time) - minutes(a.time)})
        text = f"{text} {tail}"
        speech = f"{speech} {tail_speech}" if speech and tail_speech else speech
    return with_busy_tail(ctx, reply("list", text, speech, records=[r.to_contract() for r in rows], total=len(rows)),
                          busy_rows)


def with_busy_tail(ctx: Ctx, answer: dict, busy_rows: list[Record]) -> dict:
    """«…Помимо этого вы заняты: «Работа» с 09:00 до 18:00.» — к брифингу, карточки занятости в конце."""
    if not busy_rows:
        return answer
    tail, tail_speech = ctx.say("list_busy_tail", {"items": busy_items(busy_rows, single_day=True)})
    head = re.sub(r":\s*$", ".", answer["text"])
    answer["text"] = f"{head} {tail}"
    answer["speech"] = f"{answer['speech'] or head} {tail_speech or tail}"
    answer["records"] = answer["records"] + [r.to_contract() for r in busy_rows]
    return answer


async def answer_search(ctx: Ctx, p: nlu.Parse) -> dict:
    found = one_per_series(await find(ctx, p), ctx.now)
    if not found:
        text, speech = ctx.say("found_none")
        return reply("found", text, speech, total=0)
    highlight = "date" if p.ask == "when" else None
    if p.ask == "when":
        past = sorted([r for r in found if not is_future(r, ctx.now) and r.date], key=moment, reverse=True)
        future = sorted([r for r in found if is_future(r, ctx.now)], key=moment)
        want_past = p.tense == "past" or not future
        ordered = (past + future) if want_past and past else (future + past)
        if p.next:
            ordered = ordered[:3]
        first = ordered[0]
        template = "found_when_last" if (want_past and past) else "found_when_next"
        values, speech_values = ctx.record_values(first.to_contract())
        text, speech = ctx.say(template, values, speech_values)
    else:
        ordered = nearest_first(found, ctx.now)
        if len(found) == 1:
            values, speech_values = ctx.record_values(found[0].to_contract())
            text, speech = ctx.say("found_one", values, speech_values)
        else:
            text, speech = ctx.say("found_some", {"count": len(found)})
    if len(found) == 1 or p.ask == "when":
        ctx.conv.focus = ordered[0].id
    else:
        ctx.conv.found = [r.id for r in ordered[:FOUND_LIMIT]]  # «удали её» — спросим, какую из найденных
    records = [r.to_contract(highlight=highlight) for r in ordered[:FOUND_LIMIT]]
    return reply("found", text, speech, records=records, total=len(found))


async def answer_note(ctx: Ctx, p: nlu.Parse) -> dict:
    """Заметка: сохраняем сразу, без даты, времени и проверок расписания."""
    if not p.text:
        ctx.conv.pending = Pending("note")  # «Запиши идею» — саму идею скажут следующей фразой
        text, speech = ctx.say("note_empty")
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    ctx.conv.pending = None
    record = ctx.owner.new_record(type=TaskType.note, text=p.text[:300], date=None, time=None,
                                  date_kind=DateKind.none, status=None, created_at=datetime.now(timezone.utc))
    ctx.db.add(record)
    await ctx.db.commit()
    await ctx.db.refresh(record)
    ctx.conv.focus = record.id
    text, speech = ctx.say("note_saved", {"text": record.text})
    return reply("created", text, speech, records=[record.to_contract(marks=["new"])],
                 buttons=[button_text("Мои заметки", "Покажи заметки")] + undo_button(ctx, record))


async def answer_notes(ctx: Ctx, p: nlu.Parse) -> dict:
    """Список заметок, свежие сверху; «заметки про кофе» — только подходящие."""
    rows = await owner_records(ctx, Record.type == TaskType.note)
    if p.keywords:
        rows = by_keywords(rows, p)
    if not rows:
        text, speech = ctx.say("notes_none_about" if p.keywords else "notes_none")
        return reply("found", text, speech, total=0)
    rows.sort(key=lambda r: (r.created_at or datetime.min.replace(tzinfo=timezone.utc), r.id), reverse=True)
    if len(rows) == 1:
        ctx.conv.focus = rows[0].id
    else:
        ctx.conv.found = [r.id for r in rows[:FOUND_LIMIT]]
    text, speech = ctx.say("notes_some", {"count": len(rows)})
    return reply("found", text, speech, records=[r.to_contract() for r in rows[:FOUND_LIMIT]], total=len(rows))


def is_ahead(record: Record, now: datetime) -> bool:
    """Впереди: будущее по дате или открытое дело без даты."""
    return is_future(record, now) or (not record.date and record.status != TaskStatus.done)


async def answer_person(ctx: Ctx, p: nlu.Parse) -> dict:
    """Досье: все встречи и дела с человеком — сначала впереди, потом прошлые."""
    conditions = [Record.type == TaskType(p.filter_type)] if p.filter_type else []
    if p.period:
        conditions += [Record.date >= p.period.start.isoformat(), Record.date <= p.period.end.isoformat()]
    rows = [r for r in await owner_records(ctx, *conditions)
            if any(nlu.record_has(record_words(r), key) for key in p.keywords)]
    if not p.period:
        rows = one_per_series(rows, ctx.now)
    ahead = sorted([r for r in rows if is_ahead(r, ctx.now)], key=moment)
    past = sorted([r for r in rows if not is_ahead(r, ctx.now)], key=moment, reverse=True)
    values = {"name": p.person, "count": len(rows)}
    meet = button_text("Найти время для встречи", f"Найди время встретиться с {p.person}")
    if p.period:
        values["period"] = p.period.label
        speech_values = {"period": p.period.label_speech}
        if not rows:
            text, speech = ctx.say("person_period_none", values, speech_values)
            return reply("person", text, speech, total=0, buttons=[meet])
        text, speech = ctx.say("person_period", values, speech_values,
                               count_forms=TYPE_FORMS[p.filter_type] if p.filter_type else None)
        ahead, past = sorted(rows, key=moment), []  # за период — просто по порядку, как в списке
    elif not rows:
        text, speech = ctx.say("person_none", values)
        return reply("person", text, speech, total=0, buttons=[meet])
    else:
        first = ahead[0] if ahead and ahead[0].date else past[0] if past else ahead[0]
        rec_values, rec_speech = ctx.record_values(first.to_contract())
        forms = TYPE_FORMS[p.filter_type] if p.filter_type else ("дело", "дела", "дел")
        values.update(rec_values, ahead=plural(len(ahead), *forms))
        text, speech = ctx.say("person_ahead" if first in ahead else "person_past", values,
                               {"when": rec_speech["when"]})
    ordered = ahead + past
    ctx.conv.focus = ordered[0].id  # «перенеси её на завтра» — про ближайшее
    records = [r.to_contract() for r in ordered[:FOUND_LIMIT]]
    return reply("person", text, speech, records=records, total=len(rows),
                 buttons=[meet] if not ahead else [])


def which_one(ctx: Ctx, candidates: list[Record], action_id: str) -> dict:
    ctx.conv.pending = None
    chosen = nearest_first(one_per_series(candidates, ctx.now), ctx.now)[:WHICH_LIMIT]
    text, speech = ctx.say("which_one")
    ctx.conv.choices = {"action": action_id, "ids": [r.id for r in chosen]}
    buttons = []
    for record in chosen:
        value = parse_date(record.date)
        label = f"{record.text} · {date_text(value, ctx.today) if value else 'без срока'}"
        buttons.append(button_action(label, action_id, record.id))
    return reply("clarify", text, speech, records=[r.to_contract() for r in chosen], awaiting=True,
                 buttons=buttons + [CANCEL])


async def delete_one(ctx: Ctx, record: Record, template: str = "deleted") -> dict:
    ctx.conv.pending = None
    record.deleted_at = datetime.now(timezone.utc)
    await ctx.db.commit()
    data = record.to_contract(marks=["deleted"])
    values, speech_values = ctx.record_values(data)
    text, speech = ctx.say(template, values, speech_values)
    buttons = [] if template == "undo_done" else [button_action("Вернуть", "restore", record.id, style="secondary")]
    return reply("deleted", text, speech, records=[data], buttons=buttons)


def meeting_not_done(ctx: Ctx, meetings: list[Record]) -> dict:
    """Встречу не отмечают выполненной — предлагаем удалить; «да» голосом или кнопка удаляют её."""
    text, speech = ctx.say("completed_meeting_no")
    if len(meetings) != 1 or "d2" not in ctx.features:
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    ctx.conv.focus = meetings[0].id
    ctx.conv.pending = Pending("meeting_delete", {"id": meetings[0].id})
    return reply("clarify", text, speech, records=[meetings[0].to_contract()], awaiting=True,
                 buttons=[button_action("Удалить", "delete", meetings[0].id), CANCEL])


async def complete_one(ctx: Ctx, record: Record) -> dict:
    if record.type == TaskType.meeting:
        return meeting_not_done(ctx, [record])
    if record.type == TaskType.busy:
        text, speech = ctx.say("completed_busy_no", {"text": record.text})
        return reply("clarify", text, speech, records=[record.to_contract()])
    record.status = TaskStatus.done
    await ctx.db.commit()
    ctx.conv.focus = record.id
    data = record.to_contract()
    values, speech_values = ctx.record_values(data)
    text, speech = ctx.say("completed", values, speech_values)
    return reply("completed", text, speech, records=[data])


async def complete_all(ctx: Ctx, p: nlu.Parse) -> dict:
    """«Отметь всё на сегодня выполненным» — открытые задачи и напоминания дня или периода; встречи не трогаем."""
    period = p.period or nlu._day_period(p.date or ctx.today, ctx.today)
    rows = await owner_records(ctx, Record.type.in_([TaskType.task, TaskType.reminder]), Record.status != TaskStatus.done,
                               Record.date >= period.start.isoformat(), Record.date <= period.end.isoformat())
    if not rows:
        text, speech = ctx.say("completed_many_none", {"period": period.label})
        return reply("found", text, speech, total=0)
    for row in rows:
        row.status = TaskStatus.done
    await ctx.db.commit()
    rows = sorted(rows, key=moment)
    text, speech = ctx.say("completed_many", {"count": len(rows), "period": period.label})
    return reply("completed", text, speech, records=[r.to_contract() for r in rows[:FOUND_LIMIT]], total=len(rows))


def exact(found: list[Record], p: nlu.Parse) -> list[Record]:
    """«Встреча с Машей» — запись ровно с этими словами, а не «Встреча с Марией по сайту»."""
    want = set(p.keywords) | {"~встреч", "~звон"}
    return [r for r in found if set(nlu.keywords(r.text)) <= want]


def single(ctx: Ctx, found: list[Record]) -> Record | None:
    """Одна запись — она. Среди нескольких та, о которой только что шла речь. Повторы — ближайшая."""
    if ctx.conv.focus is not None and (focus := [r for r in found if r.id == ctx.conv.focus]):
        return focus[0]
    if len({r.text.lower() for r in found}) == 1:
        return nearest_first(found, ctx.now)[0]
    return None


async def focused(ctx: Ctx, p: nlu.Parse, types: set[str] | None = None) -> list[Record]:
    """«Её», «это» — запись, о которой только что шла речь; «последнюю» без контекста — последняя созданная."""
    if not p.ref or p.keywords:
        return []
    if ctx.found:
        rows = await ctx.db.execute(select(Record).where(ctx.owner.filter(), Record.deleted_at.is_(None),
                                                         Record.id.in_(ctx.found)))
        order = {rid: i for i, rid in enumerate(ctx.found)}
        found = sorted([r for r in rows.scalars().all() if not types or r.type.value in types],
                       key=lambda r: order[r.id])
        if p.filter_type:
            found = [r for r in found if r.type.value == p.filter_type] or found
        if found:
            return found
    record = await owned_record(ctx, ctx.conv.focus)
    if record and record.deleted_at is None and (not types or record.type.value in types):
        return [record]
    if ctx.conv.focus is None:
        conditions = [Record.type == TaskType(p.filter_type)] if p.filter_type else []
        if types and not p.filter_type:
            conditions.append(Record.type.in_([TaskType(t) for t in types]))
        rows = await ctx.db.execute(select(Record).where(ctx.owner.filter(), Record.deleted_at.is_(None),
                                                         Record.created_at.is_not(None), *conditions)
                                    .order_by(Record.created_at.desc(), Record.id.desc()).limit(1))
        return list(rows.scalars().all())
    return []


async def answer_delete(ctx: Ctx, p: nlu.Parse) -> dict:
    if p.bulk and p.keywords and not (p.date or p.period):
        # «Убери все планёрки» — если это один повтор, убираем его предстоящие дни
        if one := only_series(ctx, await find(ctx, p)):
            return await delete_series(ctx, one)
    if p.bulk:
        return await answer_bulk_delete(ctx, p)
    if p.group:
        return await answer_group(ctx, p, "delete")
    if near := await focused(ctx, p):
        if len(near) == 1 or only_series(ctx, near):
            return await delete_record(ctx, only_series(ctx, near) or near[0], p)
        return which_one(ctx, near, "delete")
    if not (p.keywords or p.date or p.filter_type):
        # Просто «удали» сразу после записи — её, «Вернуть» под ответом
        focus = await owned_record(ctx, ctx.conv.focus)
        if focus and focus.deleted_at is None:
            return await delete_record(ctx, focus, p)
        text, speech = ctx.say("clarify_generic")
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    found = await find(ctx, p)
    if p.time:
        found = [r for r in found if r.time == p.time] or found
    if not found:
        text, speech = ctx.say("found_none")
        return reply("found", text, speech, total=0)
    if one := only_series(ctx, exact(found, p) or found):
        return await delete_record(ctx, one, p)
    if one := single(ctx, exact(found, p) or found):
        return await delete_record(ctx, one, p)
    return which_one(ctx, found, "delete")


def only_series(ctx: Ctx, rows: list[Record]) -> Record | None:
    """Все записи — дни одного повтора: его ближайший день (или тот, о котором шла речь)."""
    ids = {r.series_id for r in rows}
    if len(ids) != 1 or None in ids:
        return None
    if ctx.conv.focus is not None and (focus := [r for r in rows if r.id == ctx.conv.focus]):
        return focus[0]
    return nearest_first(rows, ctx.now)[0]


async def delete_record(ctx: Ctx, record: Record, p: nlu.Parse) -> dict:
    """Один день повтора, если назван день; «вообще» — весь повтор; без дня — спрашиваем."""
    if not record.series_id:
        return await delete_one(ctx, record)
    if p.series_all:
        return await delete_series(ctx, record)
    if p.date or p.series_one:
        return await delete_one(ctx, record, "deleted_occurrence")
    return ask_series_delete(ctx, record)


def ask_series_delete(ctx: Ctx, record: Record) -> dict:
    ctx.conv.pending = Pending("series_delete", {"record_id": record.id})
    ctx.conv.focus = record.id
    day = parse_date(record.date)
    repeat = record.repeat_contract() or {"weekdays": [day.weekday()]}
    values = {"text": record.text, "rule": series.rule_text(repeat["weekdays"]), "date": date_text(day, ctx.today)}
    text, speech = ctx.say("series_which_delete", values, {"date": date_speech(day, ctx.today)})
    return reply("clarify", text, speech, records=[record.to_contract()], awaiting=True, buttons=[
        button_action(f"Только {date_text(day, ctx.today)}", "delete_occurrence", record.id),
        button_action("Все предстоящие", "delete_series", record.id),
        CANCEL,
    ])


async def delete_series(ctx: Ctx, record: Record) -> dict:
    """Все предстоящие дни повтора; прошедшие остаются в истории. «Вернуть все» — как у массового удаления."""
    ctx.conv.pending = None
    rows = await series.upcoming(ctx.db, ctx.owner, record.series_id, ctx.today)
    stamp = datetime.now(timezone.utc)
    for row in rows:
        row.deleted_at = stamp
    await series.set_ended(ctx.db, ctx.owner, {record.series_id}, True)
    await ctx.db.commit()
    ctx.conv.bulk_deleted = [r.id for r in rows]
    ctx.conv.focus = None
    text, speech = ctx.say("deleted_series", {"text": record.text, "count": len(rows)})
    shown = rows[:1] or [record]
    return reply("deleted", text, speech, records=[r.to_contract(marks=["deleted"]) for r in shown], total=len(rows),
                 buttons=[button_action("Вернуть все", "restore_all", style="secondary")])


SERIES_ALL_REPLY_RE = re.compile(r"\b(?:вс[её]|все\s+дни|вообще|совсем|целиком|серию|предстоящ\w*|навсегда)\b", re.I)
SERIES_ONE_REPLY_RE = re.compile(r"\b(?:только|одн[уа]|один|эту|этот|ближайш\w*|сегодня|завтра)\b", re.I)


async def answer_series_delete(ctx: Ctx, pending: Pending, raw: str) -> dict | None:
    """Ответ на «только ближайший или все?»: «все», «только завтра». Не ответ — None."""
    record = await owned_record(ctx, pending.draft.get("record_id"))
    if not record or record.deleted_at:
        return None
    if SERIES_ONE_REPLY_RE.search(raw):
        return await delete_one(ctx, record, "deleted_occurrence")
    if SERIES_ALL_REPLY_RE.search(raw):
        return await delete_series(ctx, record)
    return None


async def answer_group(ctx: Ctx, p: nlu.Parse, action: str) -> dict:
    """«Удали эти две», «отметь их выполненными» — все записи, показанные в прошлом ответе."""
    rows = await focused(ctx, p) if ctx.found or ctx.conv.focus is not None else []
    if not rows:
        text, speech = ctx.say("group_none")
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    if action == "delete":
        if len(rows) == 1:
            return await delete_record(ctx, rows[0], p)
        forms = TYPE_FORMS[p.filter_type] if p.filter_type else None
        pending = Pending("bulk_delete", {"ids": [r.id for r in rows], "forms": forms})
        if p.group_count and p.group_count != len(rows):
            # «Эти две», а показано три — не угадываем, какие
            ctx.conv.pending = pending
            text, speech = ctx.say("group_count_mismatch", {"count": len(rows)})
            return reply("confirm", text, speech, records=[r.to_contract() for r in rows], total=len(rows),
                         awaiting=True, buttons=[button_action("Да, удалить все", "confirm_yes"),
                                                 button_action("Нет", "cancel", style="secondary")])
        return await bulk_delete(ctx, pending)
    open_rows = [r for r in rows if r.type != TaskType.meeting and r.status != TaskStatus.done]
    if not open_rows:
        meetings = [r for r in rows if r.type == TaskType.meeting]
        if meetings:
            return meeting_not_done(ctx, meetings)
        text, speech = ctx.say("completed_many_none", {"period": "среди них"})
        return reply("found", text, speech, total=0)
    if len(open_rows) == 1:
        return await complete_one(ctx, open_rows[0])
    for row in open_rows:
        row.status = TaskStatus.done
    await ctx.db.commit()
    text, speech = ctx.say("completed_group", {"count": len(open_rows)})
    return reply("completed", text, speech, records=[r.to_contract() for r in sorted(open_rows, key=moment)],
                 total=len(open_rows))


async def answer_complete(ctx: Ctx, p: nlu.Parse) -> dict:
    if p.group:
        return await answer_group(ctx, p, "complete")
    if p.bulk and not p.keywords:
        return await complete_all(ctx, p)
    if p.filter_type == "meeting":
        if p.keywords or p.date:
            meetings = [r for r in await find(ctx, p) if is_future(r, ctx.now)]
        else:
            focus = await owned_record(ctx, ctx.conv.focus)
            meetings = [focus] if focus and focus.deleted_at is None and focus.type == TaskType.meeting else []
        return meeting_not_done(ctx, nearest_first(meetings, ctx.now)[:1] if single(ctx, meetings) else meetings)
    if near := await focused(ctx, p):
        open_near = [r for r in near if r.status != TaskStatus.done] or near
        return await complete_one(ctx, open_near[0]) if len(open_near) == 1 else which_one(ctx, open_near, "complete")
    if not (p.keywords or p.date or p.filter_type):
        text, speech = ctx.say("clarify_generic")
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    found = await find(ctx, p, types={"task", "reminder"})
    open_ones = [r for r in found if r.status != TaskStatus.done] or found
    if not open_ones:
        others = await find(ctx, nlu.Parse(keywords=p.keywords, date=p.date), types={"meeting", "busy"})
        if meetings := [r for r in others if r.type == TaskType.meeting]:
            return meeting_not_done(ctx, meetings)
        if others:
            return await complete_one(ctx, others[0])  # занятость: «не отмечается, можно удалить»
        text, speech = ctx.say("found_none")
        return reply("found", text, speech, total=0)
    if one := single(ctx, open_ones):
        return await complete_one(ctx, one)
    return which_one(ctx, open_ones, "complete")


# ── Массовое удаление: «удали все дела на завтра», «убери всё за все дни» ──

YES_RE = re.compile(r"^(?:(?:стив\w*|stevens?)[\s,]+)?(?:(?:да|ага|угу|давай|подтверждаю|конечно|точно|ок|окей|верно|правильно|хорошо|записывай|сохраняй|можно)\b[^?]*"
                    r"|(?:удаляй|удали)(?:\s+(?:все|всё|их))?[\s.!]*)$", re.I)
NO_RE = re.compile(r"^(?:(?:стив\w*|stevens?)[\s,]+)?(?:нет|неа|не-а|не\s+надо|не\s+удаляй|не\s+нужно|отмена|отмени|стоп|оставь)\b", re.I)


def bulk_scope(ctx: Ctx, p: nlu.Parse) -> str:
    if p.period:
        return p.period.label
    if p.date:
        return nlu.describe_period(p.date, p.date, ctx.today).label
    return "" if p.filter_type == "note" else "за все дни"


async def answer_bulk_delete(ctx: Ctx, p: nlu.Parse) -> dict:
    """Сначала показываем, что удалится, и спрашиваем; одну запись удаляем сразу (с «Вернуть»)."""
    rows = await focused(ctx, p) if p.ref and ctx.found else []
    if not rows:
        conditions = []
        if p.filter_type:
            conditions.append(Record.type == TaskType(p.filter_type))
        if p.period:
            conditions += [Record.date >= p.period.start.isoformat(), Record.date <= p.period.end.isoformat()]
        if p.date:
            conditions.append(Record.date == p.date.isoformat())
        rows = await owner_records(ctx, *conditions)
        if p.keywords:
            rows = by_keywords(rows, p)
    forms = TYPE_FORMS[p.filter_type] if p.filter_type else None
    scope = bulk_scope(ctx, p)
    if not rows and not scope:
        text, speech = ctx.say("notes_none")
        return reply("found", text, speech, total=0)
    if not rows:
        text, speech = ctx.say("bulk_none", {"scope": scope})
        return reply("found", text, speech, total=0)
    if len(rows) == 1:
        return await delete_one(ctx, rows[0])
    ordered = sorted(rows, key=lambda r: (r.date or "9999-99-99", r.time or "99:99", r.id))
    ctx.conv.pending = Pending("bulk_delete", {"ids": [r.id for r in ordered], "forms": forms})
    text, speech = ctx.say("bulk_delete_confirm", {"count": len(rows), "scope": scope}, count_forms=forms)
    if not scope:  # «удали все заметки»: у заметок нет дней
        text, speech = (re.sub(r"\s+\?", "?", x) if x else x for x in (text, speech))
    return reply("confirm", text, speech, records=[r.to_contract() for r in ordered[:FOUND_LIMIT]], total=len(rows),
                 awaiting=True, buttons=[button_action("Да, удалить все", "confirm_yes"),
                                         button_action("Нет", "cancel", style="secondary")])


async def bulk_delete(ctx: Ctx, pending: Pending) -> dict:
    ctx.conv.pending = None
    rows = await owner_records(ctx, Record.id.in_(pending.draft["ids"]))
    stamp = datetime.now(timezone.utc)
    for record in rows:
        record.deleted_at = stamp
    await ctx.db.commit()
    ctx.conv.bulk_deleted = [r.id for r in rows]
    ctx.conv.focus = None
    forms = pending.draft.get("forms")
    text, speech = ctx.say("bulk_deleted", {"count": len(rows)}, count_forms=tuple(forms) if forms else None)
    ordered = sorted(rows, key=lambda r: (r.date or "9999-99-99", r.time or "99:99", r.id))
    return reply("deleted", text, speech, records=[r.to_contract(marks=["deleted"]) for r in ordered[:FOUND_LIMIT]],
                 total=len(rows), buttons=[button_action("Вернуть все", "restore_all", style="secondary")])


async def restore_all(ctx: Ctx) -> dict:
    ids = ctx.conv.bulk_deleted
    rows = await ctx.db.execute(select(Record).where(ctx.owner.filter(), Record.id.in_(ids or [-1]),
                                                     Record.deleted_at.is_not(None)))
    rows = list(rows.scalars().all())
    for record in rows:
        record.deleted_at = None
    await series.set_ended(ctx.db, ctx.owner, {r.series_id for r in rows if r.series_id}, False)
    await ctx.db.commit()
    ctx.conv.bulk_deleted = []
    if not rows:
        text, speech = ctx.say("found_none")
        return reply("found", text, speech, total=0)
    ordered = sorted(rows, key=lambda r: (r.date or "9999-99-99", r.time or "99:99", r.id))
    # Вернули повтор — одна карточка, как при удалении, а не все его дни подряд
    if len({r.series_id for r in rows}) == 1 and rows[0].series_id:
        ordered = ordered[:1]
    text, speech = ctx.say("bulk_restored", {"count": len(rows)})
    return reply("created", text, speech, records=[r.to_contract(marks=["new"]) for r in ordered[:FOUND_LIMIT]],
                 total=len(rows))


# ── Правка фразой: название, тип, заметка, слово в тексте ──

def edit_changes(p: nlu.Parse) -> dict:
    return {"text": p.new_text, "type": p.new_type, "note": p.note}


async def answer_edit(ctx: Ctx, p: nlu.Parse) -> dict:
    changes = edit_changes(p)
    types = {p.filter_type} if p.filter_type else None
    found = await focused(ctx, p, types)
    if not found and (p.keywords or p.date):
        found = await find(ctx, nlu.Parse(keywords=p.keywords, required=p.required, filter_type=p.filter_type,
                                           date=p.date))
        if p.time:
            found = [r for r in found if r.time == p.time] or found
        found = exact(found, p) or [r for r in found if is_future(r, ctx.now) or not r.date] or found
    if not found:
        if p.ref:
            text, speech = ctx.say("clarify_generic")
            return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
        text, speech = ctx.say("found_none")
        return reply("found", text, speech, total=0)
    if one := single(ctx, found):
        return await edit_one(ctx, one, changes)
    answer = which_one(ctx, found, "edit")
    ctx.conv.pending = Pending("edit", changes)
    return answer


async def edit_one(ctx: Ctx, record: Record, changes: dict) -> dict:
    ctx.conv.pending = None
    ctx.conv.focus = record.id
    template = "fixed"
    if changes.get("replace"):
        record.text = (replace_words(record.text, *changes["replace"]) or record.text)[:300]
    if changes.get("text"):
        record.text = changes["text"][:300]
        template = "renamed"
    if changes.get("note"):
        record.text = f"{record.text} — {changes['note']}"[:300]
        template = "noted"
    new_type = changes.get("type")
    if new_type and record.type.value != new_type:
        record.type = TaskType(new_type)
        record.status = None if new_type == "meeting" else (record.status or TaskStatus.open)
        if new_type != "task" and record.date_kind != DateKind.exact:
            record.date_kind = DateKind.exact
        template = "retyped"
    await ctx.db.commit()
    data = record.to_contract(marks=["new"])
    values, speech_values = ctx.record_values(data)
    values["type"] = TYPE_WORD[record.type.value]
    text, speech = ctx.say(template, values, speech_values)
    # Встрече и напоминанию нужны день и время — спрашиваем, как при переносе
    if new_type and new_type != "task" and not (record.date and record.time):
        day = parse_date(record.date)
        ask = await (ask_move_time(ctx, record, day) if day and day >= ctx.today else ask_move_day(ctx, record))
        ask["text"] = f"{text} {ask['text']}"
        return ask
    return reply("updated", text, speech, records=[data])


# ── Перенос фразой: «перенеси её на завтра», «сдвинь встречу с Иваном на час позже» ──

def move_changes(p: nlu.Parse) -> dict:
    return {"date": p.date.isoformat() if p.date else None, "time": p.time, "shift": p.shift}


async def answer_move(ctx: Ctx, p: nlu.Parse) -> dict:
    if p.all:
        return await answer_move_all(ctx, p)
    changes = move_changes(p)
    found = await focused(ctx, p)
    if not found and p.keywords:
        found = await find(ctx, nlu.Parse(keywords=p.keywords, required=p.required, filter_type=p.filter_type))
    if not found:
        if p.ref:
            text, speech = ctx.say("clarify_generic")
            return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
        text, speech = ctx.say("found_none")
        return reply("found", text, speech, total=0)
    future = [r for r in found if is_future(r, ctx.now) or not r.date] or found
    if one := single(ctx, future):
        return await move_one(ctx, one, changes)
    answer = which_one(ctx, future, "move")
    ctx.conv.pending = Pending("move", changes)
    return answer


async def day_windows(ctx: Ctx, day: date, need: int, skip: int | None = None) -> list[tuple[int, int]]:
    """Свободные окна дня длиной need; skip — запись, которую переносим (её место свободно)."""
    if day < ctx.today:
        return []
    start = DAY_START
    if day == ctx.today:
        start = max(DAY_START, (ctx.now.hour * 60 + ctx.now.minute) // STEP * STEP + STEP)
    rows = await owner_records(ctx, Record.date == day.isoformat(), Record.time.is_not(None))
    busy = [span(r) for r in rows if r.status != TaskStatus.done and r.id != skip]
    return free_windows(busy, start, need)


MOVE_DAYS = 3   # кнопок с ближайшими свободными днями
MOVE_TIMES = 8  # кнопок со временем


def needs_time(record: Record) -> bool:
    """Задаче без времени хватает дня; встрече, напоминанию и делу со временем нужно время."""
    return record.type != TaskType.task or bool(record.time)


async def ask_move_day(ctx: Ctx, record: Record, time: str | None = None) -> dict:
    """«На какой день перенести?» — ближайшие дни со свободным окном и тот же день через неделю."""
    ctx.conv.pending = Pending("move_when", {"record_id": record.id, "time": time})
    need = span(record)[1] - span(record)[0] if record.time else LENGTH.get(record.type.value, 30)
    days: list[date] = []
    for shift in range(14):
        day = ctx.today + timedelta(days=shift)
        if len(days) == MOVE_DAYS:
            break
        if day.isoformat() == record.date and not time:
            continue
        windows = await day_windows(ctx, day, need, skip=record.id)
        if time:
            t = minutes(time)
            fits = any(b <= t and t + need <= e for b, e in windows)
        else:
            fits = bool(windows)
        if fits:
            days.append(day)
    # Тот же день недели на следующей неделе: от дня записи, а если она в прошлом — от сегодня
    base = max(parse_date(record.date) or ctx.today, ctx.today)
    next_week = base + timedelta(days=7)
    if next_week not in days:
        days.append(next_week)
    buttons = [button_text(capitalize(date_text(d, ctx.today)), f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}")
               for d in days]
    text, speech = ctx.say("move_ask_day", {"text": record.text})
    return reply("clarify", text, speech, records=[record.to_contract()], awaiting=True, buttons=buttons + [CANCEL])


async def ask_move_time(ctx: Ctx, record: Record, day: date, queue: list[int] | None = None) -> dict:
    """«Во сколько?» — свободные окна выбранного дня кнопками. queue — кого спросить следом (перенос всех)."""
    ctx.conv.pending = Pending("move_when", {"record_id": record.id, "date": day.isoformat(), "queue": queue or []})
    need = span(record)[1] - span(record)[0] if record.time else LENGTH.get(record.type.value, 30)
    windows = await day_windows(ctx, day, need, skip=record.id)
    values = {"day": date_text(day, ctx.today), "text": record.text}
    if not windows:
        text, speech = ctx.say("move_ask_time_full", values, {"day": date_speech(day, ctx.today)})
        return reply("clarify", text, speech, records=[record.to_contract()], awaiting=True, buttons=[CANCEL])
    spans = [f"{hhmm(b)}–{hhmm(e)}" for b, e in windows]
    values["slots"] = spans[0] if len(spans) == 1 else ", ".join(spans[:-1]) + " и " + spans[-1]
    times = [hhmm(t) for t in window_starts(windows, need)][:MOVE_TIMES]
    text, speech = ctx.say("move_ask_time", values, {"day": date_speech(day, ctx.today)})
    buttons = [button_text(t, f"в {t}") for t in times]
    return reply("clarify", text, speech, records=[record.to_contract()], awaiting=True, buttons=buttons + [CANCEL])


async def move_one(ctx: Ctx, record: Record, changes: dict) -> dict:
    """Переносим, только когда сказаны и день, и время (или сдвиг «на час позже»); иначе спрашиваем."""
    ctx.conv.focus = record.id
    new_date, new_time = changes.get("date"), changes.get("time")
    if changes.get("shift") and record.time:
        new_date = new_date or record.date
        start = max(0, min(minutes(record.time) + changes["shift"], 23 * 60 + 59))
        new_time = hhmm(start)
    # «Перенеси на 15» — тот же день, что сейчас (если он не прошёл); «перенеси на пятницу» — то же время
    if not new_date and new_time and record.date and record.date >= ctx.today.isoformat():
        new_date = record.date
    if new_date and not new_time and record.time and needs_time(record) and new_date != record.date:
        new_time = record.time  # на тот же день — ничего бы не поменялось: спросим время
    if not new_date:
        return await ask_move_day(ctx, record, new_time)
    if not new_time and needs_time(record):
        day = parse_date(new_date)
        if day and day >= ctx.today:
            return await ask_move_time(ctx, record, day)
    today = ctx.today.isoformat()
    if new_date < today or (new_date == today and new_time and new_time <= ctx.now.strftime("%H:%M")):
        ctx.conv.pending = Pending("move_when", {"record_id": record.id})
        values, _ = ctx.record_values({**record.to_contract(), "date": new_date, "time": new_time,
                                       "date_kind": "exact"})
        text, speech = ctx.say("clarify_date_past", values={"when": values["when"]})
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    ctx.conv.pending = None
    if record.end_time and record.time and new_time:
        record.end_time = shift_end(record.time, record.end_time, new_time)  # «работу на час позже» — длина та же
    record.date, record.time = new_date, new_time
    if record.date_kind == DateKind.none:
        record.date_kind = DateKind.exact
    await ctx.db.commit()
    data = record.to_contract(marks=["new"])
    values, speech_values = ctx.record_values(data)
    text, speech = ctx.say("moved", values, speech_values)
    if record.type == TaskType.meeting and record.time:
        busy = [r for r in await owner_records(ctx, Record.type == TaskType.meeting, Record.date == record.date,
                                               Record.time == record.time) if r.id != record.id]
        if busy:
            tail, _ = ctx.say("moved_busy", {"text": busy[0].text})
            text = f"{text} {tail}"
            return reply("updated", text, speech, records=[data] + [r.to_contract(marks=["conflict"]) for r in busy])
    return reply("updated", text, speech, records=[data])


async def answer_move_when(ctx: Ctx, pending: Pending, raw: str) -> dict | None:
    """Ответ на «На какое время перенести?»: «на 16», «завтра в 11». Не ответ — None."""
    record = await owned_record(ctx, pending.draft.get("record_id"))
    p = nlu.parse("перенеси " + raw, ctx.now)
    if not record or p.intent != "move" or p.keywords:
        return None
    changes = move_changes(p)
    changes["time"] = changes["time"] or pending.draft.get("time")
    changes["date"] = changes["date"] or pending.draft.get("date")
    queue = pending.draft.get("queue") or []
    answer = await move_one(ctx, record, changes)
    if answer["kind"] != "updated" or not queue:
        return answer
    # Перенос всех: следующая запись очереди на тот же день
    return await next_in_queue(ctx, queue, parse_date(changes["date"]) or ctx.today, answer)


# ── Перенос всех записей дня ──────────────────────────

async def records_by_ids(ctx: Ctx, ids: list[int]) -> list[Record]:
    rows = {r.id: r for r in await owner_records(ctx, Record.id.in_(ids))} if ids else {}
    return [rows[i] for i in ids if i in rows]


def join_answers(first: dict, second: dict) -> dict:
    """Один ответ из двух: «Перенёс…» и следом вопрос про следующую запись."""
    second["text"] = f"{first['text']} {second['text']}"
    if first.get("speech") and second.get("speech"):
        second["speech"] = f"{first['speech']} {second['speech']}"
    second["records"] = first["records"] + second["records"]
    return second


async def next_in_queue(ctx: Ctx, queue: list[int], day: date, done: dict | None) -> dict:
    rows = await records_by_ids(ctx, queue)
    if not rows:
        ctx.conv.pending = None
        return done or reply("cancelled", *ctx.say("cancelled"))
    ask = await ask_move_time(ctx, rows[0], day, [r.id for r in rows[1:]])
    return join_answers(done, ask) if done else ask


def same_time_free(record: Record, busy: list[tuple[int, int]]) -> bool:
    start, end = span(record)
    return all(end <= b or start >= e for b, e in busy)


async def busy_except(ctx: Ctx, day: date, ids: set[int]) -> list[tuple[int, int]]:
    rows = await owner_records(ctx, Record.date == day.isoformat(), Record.time.is_not(None))
    return [span(r) for r in rows if r.status != TaskStatus.done and r.id not in ids]


def type_forms(rows: list[Record]) -> tuple[str, str, str] | None:
    kinds = {r.type.value for r in rows}
    return TYPE_FORMS[kinds.pop()] if len(kinds) == 1 else None


async def moved_all(ctx: Ctx, rows: list[Record], day: date) -> dict:
    values = {"count": len(rows), "day": date_text(day, ctx.today), "day_on": when_text({"date": day.isoformat()}, ctx.today)}
    text, speech = ctx.say("moved_all", values, {"day": date_speech(day, ctx.today)}, count_forms=type_forms(rows))
    return reply("updated", text, speech, records=[r.to_contract(marks=["new"]) for r in rows])


async def move_rows(ctx: Ctx, rows: list[Record], day: date, keep: list[Record]) -> dict | None:
    """Переносим keep на day в их же часы; остальным из rows — время по очереди."""
    for r in keep:
        r.date = day.isoformat()
        if r.date_kind == DateKind.none:
            r.date_kind = DateKind.exact
    await ctx.db.commit()
    rest = [r.id for r in rows if r not in keep]
    done = await moved_all(ctx, keep, day) if keep else None
    if rest:
        return await next_in_queue(ctx, rest, day, done)
    ctx.conv.pending = None
    return done


async def answer_move_all(ctx: Ctx, p: nlu.Parse) -> dict:
    source = p.source or ctx.today
    conditions = [Record.date == source.isoformat()]
    if p.filter_type:
        conditions.append(Record.type == TaskType(p.filter_type))
    rows = sorted([r for r in await owner_records(ctx, *conditions) if r.status != TaskStatus.done],
                  key=lambda r: (r.time or "99:99", r.id))
    if not rows:
        values = {"day": date_text(source, ctx.today), "types_gen": TYPE_GEN_PLURAL.get(p.filter_type or "", "дел")}
        text, speech = ctx.say("move_all_none", values, {"day": date_speech(source, ctx.today)})
        return reply("found", text, speech, total=0)
    if len(rows) == 1:
        return await move_one(ctx, rows[0], move_changes(p))
    if p.shift:
        moved = [r for r in rows if r.time]
        for r in moved:
            r.time = hhmm(max(0, min(minutes(r.time) + p.shift, 23 * 60 + 59)))
        return await move_rows(ctx, moved, source, moved) or reply("cancelled", *ctx.say("cancelled"))
    if not p.date:
        return ask_move_all_day(ctx, rows, source)
    return await plan_move_all(ctx, rows, p.date)


def ask_move_all_day(ctx: Ctx, rows: list[Record], source: date) -> dict:
    ctx.conv.pending = Pending("move_all", {"ids": [r.id for r in rows]})
    days = [d for d in (ctx.today + timedelta(days=i) for i in range(4)) if d != source][:3]
    if source + timedelta(days=7) not in days:
        days.append(max(source, ctx.today) + timedelta(days=7))
    buttons = [button_text(capitalize(date_text(d, ctx.today)), f"{d.day} {MONTHS_GEN[d.month - 1]} {d.year}")
               for d in days]
    text, speech = ctx.say("move_all_ask_day", {"count": len(rows)}, count_forms=type_forms(rows))
    return reply("clarify", text, speech, records=[r.to_contract() for r in rows], awaiting=True,
                 buttons=buttons + [CANCEL])


async def plan_move_all(ctx: Ctx, rows: list[Record], day: date) -> dict:
    """День назван: те же часы свободны — предлагаем перенести как есть, иначе время по очереди."""
    if day < ctx.today:
        return ask_move_all_day(ctx, rows, parse_date(rows[0].date) or ctx.today)
    timed = [r for r in rows if r.time]
    if not timed:
        return await move_rows(ctx, rows, day, rows)
    busy = await busy_except(ctx, day, {r.id for r in rows})
    free = [r for r in timed if same_time_free(r, busy)]
    ctx.conv.pending = Pending("move_all", {"ids": [r.id for r in rows], "date": day.isoformat()})
    values = {"count": len(rows), "day": date_text(day, ctx.today), "free": len(free), "timed": len(timed)}
    speech_values = {"day": date_speech(day, ctx.today)}
    template = "move_all_plan_free" if len(free) == len(timed) else "move_all_plan_some" if free else "move_all_plan_busy"
    text, speech = ctx.say(template, values, speech_values, count_forms=type_forms(rows))
    buttons = []
    if free:
        buttons.append(button_action("В те же часы", "move_all_same"))
    buttons.append(button_action("Выбрать время каждой", "move_all_each", style="secondary" if free else None))
    return reply("clarify", text, speech, records=[r.to_contract() for r in rows], awaiting=True,
                 buttons=buttons + [CANCEL])


async def answer_move_all_choice(ctx: Ctx, pending: Pending, how: str) -> dict:
    rows = [r for r in await records_by_ids(ctx, pending.draft.get("ids", [])) if r.deleted_at is None]
    day = parse_date(pending.draft.get("date"))
    if not rows or not day:
        ctx.conv.pending = None
        return reply("cancelled", *ctx.say("cancelled"))
    untimed = [r for r in rows if not r.time]
    if how == "same":
        busy = await busy_except(ctx, day, {r.id for r in rows})
        keep = untimed + [r for r in rows if r.time and same_time_free(r, busy)]
    else:
        keep = untimed
    keep.sort(key=lambda r: (r.time or "99:99", r.id))
    return await move_rows(ctx, rows, day, keep) or reply("cancelled", *ctx.say("cancelled"))


SAME_RE = re.compile(r"(те\s+же|так\s+же|тоже\s+время|то\s+же\s+время|как\s+есть|оставь)", re.I)
EACH_RE = re.compile(r"(кажд|по\s+очереди|выбер|подбер)", re.I)


async def answer_move_all_reply(ctx: Ctx, pending: Pending, raw: str) -> dict | None:
    """Ответ голосом или текстом на вопросы переноса всех: день, «в те же часы», «по очереди»."""
    if pending.draft.get("date"):
        if SAME_RE.search(raw):
            return await answer_move_all_choice(ctx, pending, "same")
        if EACH_RE.search(raw):
            return await answer_move_all_choice(ctx, pending, "each")
    q = nlu.parse("перенеси " + raw, ctx.now)
    if q.intent != "move" or q.keywords or not q.date:
        return None
    rows = await records_by_ids(ctx, pending.draft.get("ids", []))
    return await plan_move_all(ctx, rows, q.date) if rows else None


# ── Поправка ослышки: «исправь и вам на Иван», «не и вам, а Иван» ──

FIX_RE = re.compile(
    r"^(?:(?:стив\w*|stevens?|steve)[\s,]+)?(?:нет[\s,]+)?"
    r"(?:(?:исправь|исправить|поправь|замени|поменяй|смени|измени)\s+(?P<a>.+?)\s+на\s+(?P<b>.+?)|не\s+(?P<c>.+?)[\s,—-]+а\s+(?P<d>.+?))"
    r"[\s.!?]*$",
    re.I,
)
QUOTES_RE = re.compile(r"[«»\"„“”']")


def fix_words(raw: str, now: datetime) -> tuple[str, str, bool] | None:
    """Что на что исправить, если фраза — поправка; третье — сказан глагол («поменяй Ивана на Петра»)."""
    m = FIX_RE.match(raw.strip())
    if not m:
        return None
    wrong, right = (m["a"], m["b"]) if m["a"] else (m["c"], m["d"])
    wrong = QUOTES_RE.sub("", wrong).strip(" ,.!?—-")
    right = QUOTES_RE.sub("", right).strip(" ,.!?—-")
    # «поменяй её на завтра», «поменяй встречу на задачу» — это перенос и смена типа, не замена слов
    if nlu.REF_RE.fullmatch(wrong) or nlu.RETYPE_RE.match(raw.strip()) or nlu.parse_edit(raw.strip(), now):
        return None
    if m["a"] and nlu._has_when("на " + right, now):
        return None
    return (wrong, right, bool(m["a"])) if wrong and right else None


def replace_words(text: str, wrong: str, right: str) -> str | None:
    """Последнее вхождение целыми словами, без учёта регистра и ё/е. Нет вхождения — None."""
    flex = re.sub("[её]", "[её]", r"\s+".join(re.escape(w) for w in wrong.split()))
    hits = list(re.finditer(rf"(?<![\w])({flex})(?![\w])", text, re.I))
    if not hits and len(wrong.split()) == 1 and len(right.split()) == 1:
        return replace_word_form(text, wrong, right)
    if not hits:
        return None
    hit = hits[-1]
    if hit.start() == 0 and text[:1].isupper():
        right = right[:1].upper() + right[1:]
    return text[:hit.start()] + right + text[hit.end():]


def replace_word_form(text: str, wrong: str, right: str) -> str | None:
    """«Поменяй Ивана на Петра» в «Созвон с Иваном» → «Созвон с Петром»: то же слово в другом падеже."""
    morph = nlu._MORPH
    if morph is None:
        return None
    key = nlu.word_key(wrong)
    hits = [m for m in re.finditer(r"[А-Яа-яЁё]+", text) if nlu.word_key(m.group()) == key]
    if not hits or len(key) < 3:
        return None
    hit = hits[-1]
    form = morph.parse(hit.group())[0]
    if key.startswith("@"):
        form = next((f for f in morph.parse(hit.group()) if "Name" in f.tag), form)
    grammemes = {g for g in (form.tag.case, form.tag.number) if g}
    new = right
    for parsed in morph.parse(right)[:3]:
        if (inflected := parsed.inflect(grammemes)) is not None:
            new = inflected.word
            break
    if hit.group()[:1].isupper() or right[:1].isupper():
        new = new[:1].upper() + new[1:]
    return text[:hit.start()] + new + text[hit.end():]


async def answer_fix(ctx: Ctx, wrong: str, right: str, verb: bool = False) -> dict | None:
    """Чиним текст: сначала недописанная запись (Стивенс переспрашивает), потом та, о которой шла речь,
    потом последние созданные. Слова «и вам» нигде нет — это не поправка, разбираем фразу как обычно."""
    pending = ctx.conv.pending
    if pending and pending.kind not in ("edit", "bulk_delete", "move", "move_when") and pending.draft.get("text"):
        fixed = replace_words(pending.draft["text"], wrong, right)
        if fixed:
            answer = await process_draft(ctx, {**pending.draft, "text": fixed})
            if answer["kind"] == "clarify":
                # Переспрос не показывает текст — говорим, что исправили, и спрашиваем дальше
                head, _ = ctx.say("fixed", {"text": fixed})
                answer["text"] = f"{head} {answer['text']}"
            return answer
    candidates: list[Record] = []
    if ctx.found:
        found = [r for r in await owner_records(ctx, Record.id.in_(ctx.found)) if replace_words(r.text, wrong, right)]
        if len(found) > 1:
            return fix_which(ctx, found, wrong, right)
        candidates += found
    if focus := await owned_record(ctx, ctx.conv.focus):
        if focus.deleted_at is None and focus not in candidates:
            candidates.append(focus)
    rows = await ctx.db.execute(select(Record).where(ctx.owner.filter(), Record.deleted_at.is_(None),
                                                     Record.created_at.is_not(None))
                                .order_by(Record.created_at.desc(), Record.id.desc()).limit(5))
    candidates += [r for r in rows.scalars().all() if r not in candidates]
    for record in candidates:
        fixed = replace_words(record.text, wrong, right)
        if not fixed:
            continue
        ctx.conv.pending = None
        ctx.conv.focus = record.id
        record.text = fixed[:300]
        await ctx.db.commit()
        data = record.to_contract(marks=["new"])
        values, speech_values = ctx.record_values(data)
        text, speech = ctx.say("fixed", values, speech_values)
        return reply("updated", text, speech, records=[data])
    if verb:
        # «Поменяй Ивана на Петра» — ищем по всем записям, впереди важнее
        found = [r for r in await owner_records(ctx) if replace_words(r.text, wrong, right)]
        found = [r for r in found if is_future(r, ctx.now) or not r.date] or found
        if one := single(ctx, found):
            return await edit_one(ctx, one, {"replace": [wrong, right]})
        if found:
            return fix_which(ctx, found, wrong, right)
    return None


def fix_which(ctx: Ctx, found: list[Record], wrong: str, right: str) -> dict:
    answer = which_one(ctx, found, "edit")
    ctx.conv.pending = Pending("edit", {"replace": [wrong, right]})
    return answer


# ── Кнопки-действия (раздел 6, 15.3) ──────────────────

async def owned_record(ctx: Ctx, record_id: int | None) -> Record | None:
    if record_id is None:
        return None
    record = await ctx.db.get(Record, record_id)
    return record if record and ctx.owner.owns(record) else None


async def handle_action(ctx: Ctx, action_id: str, record_id: int | None) -> dict | None:
    """Ответ на нажатие. None — действие не наше или доп выключен: разбираем текст."""
    pending = ctx.conv.pending
    if action_id == "cancel":
        ctx.conv.pending = None
        text, speech = ctx.say("cancelled")
        return reply("cancelled", text, speech)
    if action_id == "briefing":
        return await answer_briefing(ctx)
    if action_id == "show_undated":
        return await answer_undated(ctx)
    if action_id == "skip_undated":
        return skip_undated(ctx)
    if action_id in ("delete_occurrence", "delete_series") and "d2" in ctx.features:
        record = await owned_record(ctx, record_id)
        if record and record.deleted_at is None:
            if action_id == "delete_series" and record.series_id:
                return await delete_series(ctx, record)
            return await delete_one(ctx, record, "deleted_occurrence" if record.series_id else "deleted")
    if action_id == "confirm_yes" and pending and pending.kind == "bulk_delete" and "d2" in ctx.features:
        return await bulk_delete(ctx, pending)
    if action_id == "restore_all" and "d2" in ctx.features:
        return await restore_all(ctx)
    if action_id == "edit" and pending and pending.kind == "edit":
        record = await owned_record(ctx, record_id)
        if record and record.deleted_at is None:
            return await edit_one(ctx, record, pending.draft)
    if action_id == "confirm_yes" and pending and pending.kind == "multi":
        return await save_many(ctx, pending.draft["drafts"])
    if action_id == "confirm_yes" and pending and pending.kind == "confirm":
        return await process_draft(ctx, {**pending.draft, "confirmed": True})
    if action_id == "confirm_no" and pending and pending.kind == "confirm":
        ctx.conv.pending = Pending("confirm_no", {**pending.draft, "ambiguous": False})
        text, speech = ctx.say("confirm_no")
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])
    if action_id == "keep_both" and pending and pending.kind == "conflict":
        return await keep_anyway(ctx, pending.draft)
    if action_id == "change_new_time" and pending and pending.kind == "conflict":
        ctx.conv.pending = Pending("new_time", {**pending.draft, "time": None, "end_time": None, "busy_clash": False})
        text, speech = ctx.say("conflict_new_time")
        return reply("clarify", text, speech, awaiting=True, buttons=[CANCEL])

    feature = {"undo": "d10", "restore": "d2", "delete": "d2", "complete": "d3"}.get(action_id)
    if feature and feature in ctx.features:
        record = await owned_record(ctx, record_id)
        if not record:
            text, speech = ctx.say("found_none")
            return reply("found", text, speech, total=0)
        ctx.conv.pending = None
        if action_id == "undo" and record.series_id:
            return await undo_series(ctx, record)
        if action_id == "undo":
            return await delete_one(ctx, record, "undo_done")
        if action_id == "delete":
            return await delete_one(ctx, record)
        if action_id == "complete":
            return await complete_one(ctx, record)
        record.deleted_at = None
        await ctx.db.commit()
        ctx.conv.focus = record.id
        data = record.to_contract(marks=["new"])
        values, speech_values = ctx.record_values(data)
        text, speech = ctx.say("restored", values, speech_values)
        return reply("created", text, speech, records=[data], buttons=undo_button(ctx, record))
    if action_id in ("move_all_same", "move_all_each") and pending and pending.kind == "move_all":
        return await answer_move_all_choice(ctx, pending, action_id.removeprefix("move_all_"))
    if action_id == "move" and pending and pending.kind == "move":
        record = await owned_record(ctx, record_id)
        if record:
            return await move_one(ctx, record, pending.draft)
    if pending and action_id in ("confirm_yes", "confirm_no", "keep_both", "change_new_time"):
        return None
    return None


async def keep_anyway(ctx: Ctx, draft: dict) -> dict:
    """«Оставить обе»: встреча на время занятости — дальше обычные проверки; совпала со встречей — сохраняем обе."""
    if draft.get("busy_clash"):
        return await process_draft(ctx, {**draft, "busy_clash": False, "busy_ok": True})
    return await save(ctx, {**draft, "keep_both": True})


async def undo_series(ctx: Ctx, record: Record) -> dict:
    """«Отменить» под только что записанным повтором — убираем его целиком."""
    rows = await series.upcoming(ctx.db, ctx.owner, record.series_id, ctx.today)
    stamp = datetime.now(timezone.utc)
    for row in rows:
        row.deleted_at = stamp
    await series.set_ended(ctx.db, ctx.owner, {record.series_id}, True)
    await ctx.db.commit()
    text, speech = ctx.say("undo_done", {"text": record.text})
    return reply("deleted", text, speech, records=[record.to_contract(marks=["deleted"])])


# ── Эндпоинт ──────────────────────────────────────────

INFINITIVE_RE = re.compile(r"\b[а-яё]{3,}(?:ть|ться|тись|чь)\b", re.I)


def answers_time(pending: Pending, raw: str, now: datetime, p: nlu.Parse | None = None) -> bool:
    """Ждём время, а в ответе оно есть без глагола «запиши/напомни»: «не слушай 13:00» — это ответ, мусор отбросим."""
    if pending.kind == "multi_ask":
        waiting = True
    else:
        waiting = not pending.draft.get("time")
    if not waiting or nlu.CREATE_VERB_RE.search(raw.strip().lower()):
        return False
    if p and p.intent == "create" and p.text and INFINITIVE_RE.search(p.text) and (p.date or p.time):
        return False
    # «Завтра в 7 вечера кино с Петей» посреди вопроса — новое дело со своим днём, временем и смыслом
    if p and p.intent == "create" and p.text and p.date and p.time and len(nlu.keywords(p.text)) >= 2:
        return False
    return bool(nlu.parse_when(raw, now)[0] or nlu.reply_time(raw)[0])


# «Нет», «не хочу», «не надо» в ответ на «Показать задачи без срока?»
REFUSE_RE = re.compile(r"^(?:(?:стив\w*|stevens?)[\s,]+)?(?:нет|не\s+хочу|не\s+надо|не\s+сейчас|не\s+нужно|потом|позже"
                       r"|не\s+стоит|обойдусь|хватит)\b", re.I)
SHOW_RE = re.compile(r"^(?:(?:стив\w*|stevens?)[\s,]+)?(?:покажи(?:\s+(?:их|все|всё))?|показывай|хочу|давай|можно|ну\s+давай"
                     r"|посмотрю|посмотрим)[\s.!]*$", re.I)


def is_new_request(p: nlu.Parse, raw: str) -> bool:
    if p.only_when:
        return False
    if p.intent in ("list", "search", "help", "off_topic", "delete", "complete", "thanks", "move", "edit", "note", "notes",
                    "undated", "repeats"):
        return True
    if p.intent == "create":
        return bool(nlu.CREATE_VERB_RE.search(raw.strip().lower()) or (p.text and len(nlu.keywords(p.text)) >= 2)
                    or p.repeat_days)
    return False


@router.post("/chat")
async def chat_endpoint(
    payload: ChatRequest,
    background: BackgroundTasks,
    owner: Owner = Depends(get_owner),
    db: AsyncSession = Depends(get_db),
    x_stevens_features: str | None = Header(default=None),
):
    features = {f.strip().lower() for f in (x_stevens_features or "").split(",") if f.strip()}
    ctx = Ctx(owner=owner, db=db, conv=conversation(owner, payload.conversation_id),
              now=client_now(payload.client_time), features=features)
    started = clock.perf_counter()
    waiting = ctx.conv.pending.kind if ctx.conv.pending else None
    try:
        answer = await respond(ctx, payload)
    except Exception as error:  # noqa: BLE001 — сбой: kind error, «Повторить» добавит фронт
        print(f"chat error: {error!r}")
        await db.rollback()
        ctx.trace["error"] = repr(error)
        ctx.trace.pop("template", None)
        text, speech = ctx.say("error_backend")
        answer = reply("error", text, speech)
    ctx.conv.undo = next((b["send"]["action"] for b in answer.get("buttons") or []
                          if b["send"].get("action", {}).get("id") in UNDO_ACTIONS), None)
    # Журнал пишем уже после ответа: пользователь его не ждёт
    background.add_task(chatlog.write, owner, payload, answer, ctx.trace, waiting,
                        int((clock.perf_counter() - started) * 1000))
    return answer


UNDO_ACTIONS = ("undo", "restore", "restore_all")
# «Отмени», «верни как было» без вопроса — то же, что «Отменить»/«Вернуть» под последним ответом
UNDO_RE = re.compile(r"^(?:отмени(?:ть)?(?:\s+(?:это|последнее|вс[её]))?|отмена|верни(?:те)?(?:\s+(?:обратно|назад|как\s+было"
                     r"|вс[её]|это|е[её]|его|их))*)[\s.!]*$", re.I)
# «Другое время», «давай другое время» на конфликт встреч — как кнопка «Другое время для новой»
OTHER_TIME_RE = re.compile(r"^(?:(?:да|давай|ну|тогда)[\s,]+)*(?:друго[ей]|ино[ей]|поменяй|смени|измени)(?:\s+(?:время|час))?"
                           r"(?:\s+(?:для\s+)?новой)?[\s.!]*$", re.I)
# «Оставь обе» на конфликт встреч
ANYWAY_RE = re.compile(r"^(?:(?:да|ну|ок|ладно)[\s,]+)*(?:вс[её]\s+равно|да|ок|ладно|записывай|запиши|ставь|поставь|ничего)"
                       r"(?:[\s,]+(?:записывай|запиши|ставь|поставь|страшного))?[\s.!]*$", re.I)
KEEP_BOTH_RE = re.compile(r"^(?:(?:да|ну)[\s,]+)?(?:оставь|оставляй|оставить|пусть\s+будут|сохрани)?\s*(?:обе|оба|обе\s+встречи|вместе)\b", re.I)
# «А теперь на 12», «лучше в субботу» — перенос записи, о которой шла речь
FOLLOW_MOVE_RE = re.compile(r"^(?:(?:а|ну|ой|нет|не)[\s,]+)*(?:(?:лучше|тогда|теперь|давай|можно|пусть)[\s,]+)+(?:будет\s+)?"
                            r"(?=(?:на|в|во|завтра|послезавтра|сегодня)\b)", re.I)
# «Нет, в 11», «не, на пятницу» сразу после записи — поправка времени или дня
FOLLOW_FIX_RE = re.compile(r"^(?:(?:а|ну|ой)[\s,]+)*(?:нет|не|неа)[\s,.!—-]+(?:а\s+|лучше\s+)?"
                           r"(?=(?:на|в|во|завтра|послезавтра|сегодня)\b)", re.I)
# «А на час раньше», «на полчаса позже»
FOLLOW_SHIFT_RE = re.compile(r"^(?:(?:а|ну|и|тогда|давай)[\s,]+)*(?=на\s+(?:полчаса|\d*\s*час\w*|\d+\s*минут\w*)\s+(?:позже|раньше)\b)",
                             re.I)
ACK_RE = re.compile(r"^(?:да|нет|неа|не\s+надо|ага|угу|ок|окей|хорошо|понял|поняла|ясно|отлично|супер|класс|норм|понятно)[\s.!)]*$", re.I)
ORDINAL_RE = re.compile(r"\b(перв(?:ую|ый|ое|ая)|втор(?:ую|ой|ое|ая)|трет(?:ью|ья|ье|ий)|четв[её]рт(?:ую|ый|ое|ая)"
                        r"|пят(?:ую|ый|ое|ая)|последн(?:юю|ий|ее|яя))\b|^(?:номер\s+)?([1-5])[\s.!]*$", re.I)


def pick_choice(raw: str, rows: list[Record], now: datetime) -> Record | None:
    """Выбор из «Какую из них?» словами: «первую», «ту, что в субботу», «про интеграцию». None — не выбрал."""
    if not rows:
        return None
    if m := ORDINAL_RE.search(raw):
        if m.group(2):
            index = int(m.group(2)) - 1
        else:
            word = m.group(1).lower()
            index = -1 if word.startswith("последн") else next(
                i for i, stem in enumerate(("перв", "втор", "трет", "четв", "пят")) if word.startswith(stem))
        return rows[index] if -len(rows) <= index < len(rows) else None
    time, _, _, info = nlu.parse_when(raw, now)
    near = rows
    if info.date:
        near = [r for r in near if r.date == info.date.isoformat()]
    if time:
        near = [r for r in near if r.time == time]
    if (info.date or time) and len(near) == 1:
        return near[0]
    keys = [k for k in nlu.keywords(raw) if k not in ("~встреч", "~звон")]
    if keys:
        scores = [(matches(r, keys), r) for r in near or rows]
        best = max(score for score, _ in scores)
        top = [r for score, r in scores if score == best]
        if best and len(top) == 1:
            return top[0]
    return None


async def respond(ctx: Ctx, payload: ChatRequest) -> dict:
    ctx.found, ctx.conv.found = ctx.conv.found, []
    choices, ctx.conv.choices = ctx.conv.choices, None
    await series.extend(ctx.db, ctx.owner, ctx.today)
    if payload.action:
        answer = await handle_action(ctx, payload.action.id, payload.action.record_id)
        if answer:
            return answer

    raw = payload.text.strip()
    pending = ctx.conv.pending
    if choices and raw:
        rows = await records_by_ids(ctx, choices["ids"])
        if (pick := pick_choice(raw, rows, ctx.now)) and (answer := await handle_action(ctx, choices["action"], pick.id)):
            return answer
    if pending and pending.kind == "bulk_delete" and "d2" in ctx.features:
        if NO_RE.match(raw):
            ctx.conv.pending = None
            text, speech = ctx.say("cancelled")
            return reply("cancelled", text, speech)
        if YES_RE.match(raw):
            return await bulk_delete(ctx, pending)
    if pending and pending.kind == "undated":
        if NO_RE.match(raw) or REFUSE_RE.match(raw):
            return skip_undated(ctx)
        if YES_RE.match(raw) or SHOW_RE.match(raw):
            return await answer_undated(ctx)
    if pending and pending.kind == "meeting_delete":
        if YES_RE.match(raw) and (answer := await handle_action(ctx, "delete", pending.draft["id"])):
            return answer
        if NO_RE.match(raw) or nlu.is_cancel_reply(raw):
            ctx.conv.pending = None
            return reply("cancelled", *ctx.say("cancelled"))
    if pending and pending.kind == "series_delete" and nlu.is_cancel_reply(raw):
        ctx.conv.pending = None
        return reply("cancelled", *ctx.say("cancelled"))
    if pending and pending.kind == "series_delete" and (answer := await answer_series_delete(ctx, pending, raw)):
        return answer
    # «Да» / «нет» голосом или текстом на «Записать все?» и «Правильно понял?» — как кнопки
    if pending and pending.kind in ("multi", "confirm") and YES_RE.match(raw):
        if answer := await handle_action(ctx, "confirm_yes", None):
            return answer
    if pending and pending.kind == "confirm" and (no := NO_RE.match(raw)) and (
            rest := raw[no.end():].strip(" ,.!—-")) and (answer := await answer_confirm_fix(ctx, pending, rest)):
        return answer
    if pending and pending.kind in ("multi", "confirm") and NO_RE.match(raw):
        if answer := await handle_action(ctx, "cancel" if pending.kind == "multi" else "confirm_no", None):
            return answer
    if (fix := fix_words(raw, ctx.now)) and (answer := await answer_fix(ctx, *fix)):
        return answer
    p = nlu.parse(raw, ctx.now)
    ctx.trace["intent"] = p.intent

    # Ответ на уточнение (Р-45); не ответ — новый запрос, уточнение забываем (Р-33)
    pending = ctx.conv.pending
    if pending and pending.kind == "move_all" and not is_new_request(p, raw):
        if answer := await answer_move_all_reply(ctx, pending, raw):
            return answer
    if pending and pending.kind == "note" and not is_new_request(p, raw) and not nlu.is_cancel_reply(raw):
        return await answer_note(ctx, nlu.Parse(text=nlu.capitalize(raw.strip(" ,.!?:—-")) or None))
    if pending and pending.kind == "move_when" and not is_new_request(p, raw):
        if answer := await answer_move_when(ctx, pending, raw):
            return answer
    if choices and (nlu.is_cancel_reply(raw) or NO_RE.match(raw)):
        ctx.conv.pending = None
        return reply("cancelled", *ctx.say("cancelled"))
    if choices and not is_new_request(p, raw) and not UNDO_RE.match(raw):
        # Не выбрал из «Какую из них?» — спрашиваем ещё раз, вопрос не теряем
        rows = await records_by_ids(ctx, choices["ids"])
        if rows:
            keep = ctx.conv.pending
            answer = which_one(ctx, rows, choices["action"])
            ctx.conv.pending = keep
            return answer
    if pending and pending.kind == "conflict" and not nlu.is_cancel_reply(raw):
        if KEEP_BOTH_RE.match(raw) or (pending.draft.get("busy_clash") and ANYWAY_RE.match(raw)):
            return await keep_anyway(ctx, pending.draft)
        if OTHER_TIME_RE.match(raw) and (answer := await handle_action(ctx, "change_new_time", None)):
            return answer
        if not is_new_request(p, raw) and (nlu.parse_when(raw, ctx.now)[0] or nlu.reply_time(raw)[0]):
            # «Давай в 16» — другое время для новой встречи
            pending = ctx.conv.pending = Pending("new_time", {**pending.draft, "time": None, "end_time": None,
                                                              "busy_clash": False})
    asking = pending and pending.kind in ("draft", "new_time", "confirm_no", "multi_ask", "multi", "confirm", "conflict")
    if asking and nlu.is_cancel_reply(raw):
        # «Стой», «убери всё» в ответ на вопрос — отмена, а не удаление записей
        ctx.conv.pending = None
        text, speech = ctx.say("cancelled")
        return reply("cancelled", text, speech)
    if pending and pending.kind in ("draft", "new_time", "confirm_no", "multi_ask") and (
            not is_new_request(p, raw) or answers_time(pending, raw, ctx.now, p)):
        if pending.kind == "multi_ask":
            answer = await answer_multi_ask(ctx, pending, raw)
        else:
            merged = merge_reply(pending, raw, ctx.now)
            answer = await process_draft(ctx, merged) if merged else None
        if answer:
            return answer
        if pending.kind == "multi_ask":
            return await ask_many(ctx, pending.draft["drafts"])
        if answer := ask_again(ctx, pending):
            return answer
    asked = ctx.conv.pending
    ctx.conv.pending = None

    if UNDO_RE.match(raw) and not asked:
        if ctx.conv.undo and (answer := await handle_action(ctx, ctx.conv.undo["id"], ctx.conv.undo.get("record_id"))):
            return answer
        return reply("cancelled", *ctx.say("undo_nothing"))
    if p.intent in ("unknown", "create") and ctx.conv.focus and (
            follow := FOLLOW_MOVE_RE.match(raw) or FOLLOW_FIX_RE.match(raw) or FOLLOW_SHIFT_RE.match(raw)):
        # «А теперь на 12», «лучше в субботу» — переносим запись, о которой шла речь
        moved = nlu.parse("перенеси её " + raw[follow.end():], ctx.now)
        if moved.intent == "move" and (moved.date or moved.time or moved.shift) and not moved.keywords:
            ctx.trace["intent"] = "move"
            return await answer_move(ctx, moved)
    if p.intent == "unknown" and llm_enabled():
        p = await llm_parse(raw, ctx.now)
        ctx.trace.update(intent=p.intent, llm=True)

    # Ждали «да» или «нет», а ответ не поняли — вопрос не теряем, переспрашиваем
    if p.intent == "unknown" and asked and asked.kind == "conflict":
        ctx.conv.pending = asked
        return await process_draft(ctx, asked.draft)
    if p.intent == "unknown" and not asked and ACK_RE.match(raw):
        return reply("thanks", *ctx.say("ack"))
    if p.intent == "unknown" and asked and asked.kind in ("multi", "confirm", "bulk_delete"):
        ctx.conv.pending = asked
        text, speech = ctx.say("yes_no_again")
        no = "confirm_no" if asked.kind == "confirm" else "cancel"
        return reply("clarify", text, speech, awaiting=True, buttons=[
            button_action("Да", "confirm_yes"), button_action("Нет", no, style="secondary")])

    if p.intent == "delete" and "d2" not in ctx.features:
        p.intent = "unknown"
    if p.intent == "complete" and "d3" not in ctx.features:
        p.intent = "unknown"

    if p.intent == "create":
        many = nlu.parse_many(raw, ctx.now)
        if many and (answer := await answer_many(ctx, many)):
            return answer
        return await process_draft(ctx, draft_from(p, ctx.today))
    if p.intent == "undated":
        return await answer_undated(ctx)
    if p.intent == "repeats":
        return await answer_repeats(ctx)
    if p.intent == "bad_date":
        text, speech = ctx.say("bad_date", {"text": p.text})
        return reply("clarify", text, speech)
    if p.intent == "note":
        return await answer_note(ctx, p)
    if p.intent == "notes":
        return await answer_notes(ctx, p)
    if p.intent == "free":
        return await answer_free(ctx, p)
    if p.intent == "list":
        return await answer_list(ctx, p)
    if p.intent == "search":
        return await answer_search(ctx, p)
    if p.intent == "person":
        return await answer_person(ctx, p)
    if p.intent == "delete":
        return await answer_delete(ctx, p)
    if p.intent == "complete":
        return await answer_complete(ctx, p)
    if p.intent == "move":
        return await answer_move(ctx, p)
    if p.intent == "edit":
        return await answer_edit(ctx, p)
    if p.intent == "help":
        return examples("help", "help", ctx, HELP_EXAMPLES)
    if p.intent == "thanks":
        text, speech = ctx.say("thanks")
        return reply("thanks", text, speech)
    if p.intent == "off_topic":
        return examples("off_topic", "off_topic", ctx, EXAMPLES)
    if "d3" in ctx.features and (answer := await answer_did(ctx, raw)):
        return answer
    return examples("not_understood", "not_understood", ctx, EXAMPLES)


PAST_VERB_RE = re.compile(r"\b([а-яё]{3,}?)(?:л|ла|ли)\b", re.I)


async def answer_did(ctx: Ctx, raw: str) -> dict | None:
    """«Хлеб купил», «позвонила маме» — отмечаем дело «Купить хлеб», «Позвонить маме». Нет такого — None."""
    lower = raw.lower().strip(" .!")
    if raw.strip().endswith("?") or re.search(r"\bне\s+[а-яё]+л[аи]?\b", lower):
        return None
    for verb in PAST_VERB_RE.finditer(lower):
        stem = verb.group(1).replace("ё", "е")
        rest = (lower[:verb.start()] + " " + lower[verb.end():]).strip()
        keys, required = nlu.search_keywords(rest)
        keys = [k for k in keys if k not in ("я", "уже")]
        if not keys:
            continue
        rows = [r for r in await owner_records(ctx, Record.type.in_([TaskType.task, TaskType.reminder]))
                if r.status != TaskStatus.done and r.text.lower().replace("ё", "е").startswith(stem)]
        rows = by_keywords(rows, nlu.Parse(keywords=keys, required=required))
        if not rows:
            continue
        ctx.trace["intent"] = "complete"
        if one := single(ctx, rows):
            return await complete_one(ctx, one)
        return which_one(ctx, nearest_first(rows, ctx.now), "complete")
    return None
