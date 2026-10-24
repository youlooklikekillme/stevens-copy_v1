"""Повторы по дням недели: «каждый вторник в 10 планёрка», «по средам до конца года».

Каждый день повтора — обычная запись с series_id: её видно в списках и календаре, по ней звонят
уведомления, её можно перенести или удалить отдельно. Бессрочный повтор создаётся на год вперёд
и продлевается сам (extend), пока его не убрали целиком.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import DateKind, Record, Series, TaskStatus, TaskType
from app.owner import Owner
from app.textfmt import MONTHS_GEN, parse_date

HORIZON_DAYS = 365   # на сколько вперёд создаём дни бессрочного повтора
REFILL_DAYS = 300    # продлеваем, когда созданного осталось меньше чем на столько дней

DAY_ONE = ["каждый понедельник", "каждый вторник", "каждую среду", "каждый четверг", "каждую пятницу",
           "каждую субботу", "каждое воскресенье"]
DAY_MANY = ["понедельникам", "вторникам", "средам", "четвергам", "пятницам", "субботам", "воскресеньям"]


def owner_filter(owner: Owner):
    return Series.account_id == owner.account.id if owner.account else Series.sandbox_id == owner.sandbox_id


def rule_text(days: list[int], time: str | None = None, end: str | None = None) -> str:
    """«каждый вторник в 10:00», «по понедельникам и четвергам», «по будням с 9:00 до 18:00»."""
    days = sorted(set(days))
    if days == list(range(7)):
        rule = "каждый день"
    elif days == [0, 1, 2, 3, 4]:
        rule = "по будням"
    elif days == [5, 6]:
        rule = "по выходным"
    elif len(days) == 1:
        rule = DAY_ONE[days[0]]
    else:
        names = [DAY_MANY[d] for d in days]
        rule = "по " + ", ".join(names[:-1]) + " и " + names[-1]
    if time and end:
        return f"{rule} с {time} до {end}"
    return f"{rule} в {time}" if time else rule


def until_text(value: date, today: date) -> str:
    """«31 декабря», с годом — если не текущий."""
    text = f"{value.day} {MONTHS_GEN[value.month - 1]}"
    return text if value.year == today.year else f"{text} {value.year} года"


def days_between(first: date, last: date, weekdays: list[int]) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)
            if (first + timedelta(days=n)).weekday() in weekdays]


def weekdays_of(series: Series) -> list[int]:
    return [int(d) for d in series.weekdays.split(",")]


def occurrence(owner: Owner, series: Series, day: date, created: datetime) -> Record:
    return owner.new_record(
        type=series.type, text=series.text, date=day.isoformat(), time=series.time, end_time=series.end_time,
        date_kind=DateKind.exact, status=None if series.type in (TaskType.meeting, TaskType.busy) else TaskStatus.open, created_at=created,
        series_id=series.id, repeat=series.repeat(),
    )


async def create(db: AsyncSession, owner: Owner, draft: dict, today: date) -> tuple[Series, list[Record]]:
    """Повтор и его дни с первого (draft["date"]) до конца или на год вперёд. Дней нет — пустой список."""
    repeat = draft["repeat"]
    weekdays = sorted(set(repeat["days"]))
    until = parse_date(repeat.get("until"))
    first = parse_date(draft["date"]) or today
    last = min(until, today + timedelta(days=HORIZON_DAYS)) if until else today + timedelta(days=HORIZON_DAYS)
    dates = days_between(first, last, weekdays)
    if not dates:
        return None, []  # type: ignore[return-value]
    created = datetime.now(timezone.utc)
    series = Series(
        account_id=owner.account.id if owner.account else None, sandbox_id=None if owner.account else owner.sandbox_id,
        type=TaskType(draft["type"]), text=draft["text"][:300], time=draft.get("time"), end_time=draft.get("end_time"),
        weekdays=",".join(map(str, weekdays)), until=until.isoformat() if until else None,
        filled_until=last.isoformat(), created_at=created,
    )
    db.add(series)
    await db.flush()
    records = [occurrence(owner, series, day, created) for day in dates]
    db.add_all(records)
    await db.commit()
    for record in records[:1]:
        await db.refresh(record)
    return series, records


async def extend(db: AsyncSession, owner: Owner, today: date) -> None:
    """Бессрочные повторы: досоздаём дни, чтобы впереди всегда был год."""
    edge = (today + timedelta(days=REFILL_DAYS)).isoformat()
    # Календарь и чат приходят разом: строку повтора продлевает тот, кто первым её занял, остальные пропускают
    rows = (await db.execute(select(Series).where(owner_filter(owner), Series.ended.is_(False),
                                                  Series.until.is_(None), Series.filled_until < edge)
                             .with_for_update(skip_locked=True))).scalars().all()
    if not rows:
        return
    created = datetime.now(timezone.utc)
    last = today + timedelta(days=HORIZON_DAYS)
    for series in rows:
        start = (parse_date(series.filled_until) or today) + timedelta(days=1)
        db.add_all([occurrence(owner, series, day, created) for day in days_between(start, last, weekdays_of(series))])
        series.filled_until = last.isoformat()
    await db.commit()


async def upcoming(db: AsyncSession, owner: Owner, series_id: int, today: date) -> list[Record]:
    """Предстоящие (с сегодняшнего дня) неудалённые дни повтора — по порядку."""
    rows = (await db.execute(select(Record).where(owner.filter(), Record.series_id == series_id,
                                                  Record.deleted_at.is_(None), Record.date >= today.isoformat()))
            ).scalars().all()
    return sorted(rows, key=lambda r: (r.date, r.time or "99:99", r.id))


async def set_ended(db: AsyncSession, owner: Owner, series_ids: set[int], ended: bool) -> None:
    if not series_ids:
        return
    rows = (await db.execute(select(Series).where(owner_filter(owner), Series.id.in_(series_ids)))).scalars().all()
    for series in rows:
        series.ended = ended
