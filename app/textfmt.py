"""Даты и числа для текстов ответа — по разделу 9 контракта v1.4.

Все функции получают «сегодня» явно: бэк берёт его из client_time запроса,
а не из часов сервера (раздел 3).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

MONTHS_SHORT = ["янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек"]
MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря"]
MONTHS_PREP = ["январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе",
               "сентябре", "октябре", "ноябре", "декабре"]
WEEKDAYS_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]

TYPE_WORD = {"task": "задача", "reminder": "напоминание", "meeting": "встреча", "note": "заметка",
             "busy": "занятость"}
TYPE_GEN_PLURAL = {"task": "задач", "reminder": "напоминаний", "meeting": "встреч", "note": "заметок",
                   "busy": "занятостей"}
TYPE_FORMS = {
    "task": ("задача", "задачи", "задач"),
    "reminder": ("напоминание", "напоминания", "напоминаний"),
    "meeting": ("встреча", "встречи", "встреч"),
    "note": ("заметка", "заметки", "заметок"),
    "busy": ("занятость", "занятости", "занятостей"),
}


def plural(n: int, f1: str, f2: str, f3: str) -> str:
    """{count:ф1|ф2|ф3} — число цифрами и нужная форма (Р-126)."""
    tail100, tail10 = n % 100, n % 10
    if tail10 == 1 and tail100 != 11:
        form = f1
    elif 2 <= tail10 <= 4 and not 12 <= tail100 <= 14:
        form = f2
    else:
        form = f3
    return f"{n} {form}"


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def date_text(value: date, today: date) -> str:
    """{date} в text: «сегодня» / «завтра» / «вчера» / «пн, 19 окт» (+ год, если не текущий)."""
    diff = (value - today).days
    if diff == 0:
        return "сегодня"
    if diff == 1:
        return "завтра"
    if diff == -1:
        return "вчера"
    return short_date(value, today)


def short_date(value: date, today: date) -> str:
    """«сб, 21 ноя» — без «сегодня/завтра»: так подписаны кнопки «Какую именно?»."""
    text = f"{WEEKDAYS_SHORT[value.weekday()]}, {value.day} {MONTHS_SHORT[value.month - 1]}"
    if value.year != today.year:
        text += f" {value.year}"
    return text


def date_speech(value: date, today: date) -> str:
    """{date} в speech: без дня недели и сокращений (Р-127)."""
    diff = (value - today).days
    if diff == 0:
        return "сегодня"
    if diff == 1:
        return "завтра"
    if diff == -1:
        return "вчера"
    text = f"{value.day} {MONTHS_GEN[value.month - 1]}"
    if value.year != today.year:
        text += f" {value.year} года"
    return text


def when_text(record: dict, today: date, speech: bool = False) -> str:
    """{when} по date_kind записи."""
    fmt = date_speech if speech else date_text
    kind = record.get("date_kind")
    value = parse_date(record.get("date"))
    if kind == "none" or value is None:
        return "без срока"
    if kind == "deadline":
        return f"до {fmt(value, today)}"
    if record.get("time") and record.get("end_time"):
        return f"{fmt(value, today)} с {record['time']} до {record['end_time']}"
    if record.get("time"):
        return f"{fmt(value, today)} в {record['time']}"
    return fmt(value, today)


def capitalize(text: str) -> str:
    return text[:1].upper() + text[1:] if text else text


def week_bounds(day: date) -> tuple[date, date]:
    """Неделя — пн–вс."""
    start = day - timedelta(days=day.weekday())
    return start, start + timedelta(days=6)


def client_now(client_time: str) -> datetime:
    """Местное «сейчас» пользователя без пояса: даты записей тоже местные."""
    try:
        return datetime.fromisoformat(client_time).replace(tzinfo=None)
    except (TypeError, ValueError):  # не прислали или прислали не дату
        return datetime.now()


def shift_end(old_start: str, old_end: str, new_start: str) -> str:
    """Конец интервала, когда начало передвинули: длина остаётся прежней."""
    to_min = lambda t: int(t[:2]) * 60 + int(t[3:])
    length = (to_min(old_end) - to_min(old_start)) % (24 * 60)
    end = (to_min(new_start) + length) % (24 * 60)
    return f"{end // 60:02d}:{end % 60:02d}"
