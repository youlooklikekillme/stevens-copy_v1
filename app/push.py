"""Пуш-уведомления (Web Push): приходят, даже когда вкладка закрыта.

Раз в 30 секунд смотрим записи тех, кто подписался: напоминание звонит в своё время,
встреча — за remind_before минут (по умолчанию 15). У пуша кнопки «Посмотреть» и «Через 10 минут» (public/sw.js);
отложенное лежит в push_snoozes и уходит ещё раз в свой срок. Время записи местное, поэтому пояс берём из подписки браузера.
Демо-записи песочницы (demo) не звонят — только то, что человек завёл сам.
"""
import asyncio
import base64
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.hazmat.primitives import serialization
from py_vapid import Vapid
from pywebpush import WebPushException, webpush
from sqlalchemy import delete, select

from app.database import AsyncSessionLocal
from app.textfmt import plural
from app.models import AppSetting, PushSent, PushSnooze, PushSubscription, Record, TaskStatus, TaskType

TICK_SECONDS = 30
# Опоздали больше чем на столько (сервер спал) — уже не шлём
LATE_LIMIT = timedelta(minutes=15)
CLAIMS = {"sub": "mailto:stevens@example.com"}
SNOOZE = timedelta(minutes=10)

_vapid: Vapid | None = None


async def vapid() -> Vapid:
    """Ключи VAPID создаются один раз и хранятся в базе: подписки живут между перезапусками."""
    global _vapid
    if _vapid:
        return _vapid
    async with AsyncSessionLocal() as db:
        row = await db.get(AppSetting, "vapid_private_pem")
        if row:
            _vapid = Vapid.from_pem(row.value.encode())
        else:
            _vapid = Vapid()
            _vapid.generate_keys()
            db.add(AppSetting(key="vapid_private_pem", value=_vapid.private_pem().decode()))
            await db.commit()
    return _vapid


async def public_key() -> str:
    """applicationServerKey для pushManager.subscribe — точка в base64url без '='."""
    raw = (await vapid()).public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def zone(name: str):
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def fire_at(record: Record, tz) -> datetime | None:
    if not record.date or not record.time or record.demo:
        return None
    if record.type not in (TaskType.reminder, TaskType.meeting) or record.status == TaskStatus.done:
        return None
    try:
        at = datetime.fromisoformat(f"{record.date}T{record.time}").replace(tzinfo=tz)
    except ValueError:  # кривые дата или время в одной записи не должны останавливать пуши всем
        return None
    return at - timedelta(minutes=record.remind_minutes()) if record.type == TaskType.meeting else at


def minutes_text(total: int) -> str:
    """15 → «15 минут», 30 → «полчаса», 60 → «час», 120 → «2 часа»."""
    if total == 30:
        return "полчаса"
    if total == 60:
        return "час"
    if total % 60 == 0:
        return plural(total // 60, "час", "часа", "часов")
    return plural(total, "минуту", "минуты", "минут")


def record_key(record: Record) -> str:
    """Тот же ключ, что у уведомлений во вкладке: одно и то же не покажется дважды."""
    return f"{record.id}@{record.date}T{record.time}"


def payload(record: Record, snoozed: bool = False) -> str:
    """Две строки (контракт v1.5, 4.2): фраза Стивенса и текст записи."""
    if snoozed:
        first = "Позвольте напомнить ещё раз."
    elif record.type == TaskType.meeting:
        first = f"Через {minutes_text(record.remind_minutes())} у вас встреча."
    else:
        first = "Позвольте напомнить."
    body = f"{first}\n{record.text}"
    return json.dumps({
        "title": "Stevens", "body": body, "tag": record_key(record),
        "record_id": record.id, "snoozed": snoozed,
    }, ensure_ascii=False)


def send(sub: PushSubscription, data: str, key: Vapid) -> bool:
    """False — подписка больше не действует (браузер её отозвал)."""
    try:
        webpush(
            {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
            data=data, vapid_private_key=key, vapid_claims=dict(CLAIMS), ttl=600, timeout=10,
        )
    except WebPushException as error:
        status = error.response.status_code if error.response is not None else None
        if status in (404, 410):
            return False
        print(f"⚠️ Пуш не ушёл: {status} {error}")
    except Exception as error:  # сеть и прочее — попробуем со следующей записью
        print(f"⚠️ Пуш не ушёл: {error}")
    return True


async def tick() -> None:
    key = await vapid()
    async with AsyncSessionLocal() as db:
        subs = (await db.execute(select(PushSubscription))).scalars().all()
        owners: dict[tuple, list[PushSubscription]] = {}
        for sub in subs:
            owners.setdefault((sub.account_id, sub.sandbox_id), []).append(sub)
        sent = set((await db.execute(select(PushSent.key))).scalars().all())
        dead: list[int] = []
        for (account_id, sandbox_id), group in owners.items():
            tz = zone(group[-1].timezone)
            now = datetime.now(tz)
            days = [(now - timedelta(days=1)).date().isoformat(), now.date().isoformat(),
                    (now + timedelta(days=1)).date().isoformat()]
            owner = Record.account_id == account_id if account_id else Record.sandbox_id == sandbox_id
            records = (await db.execute(
                select(Record).where(owner, Record.deleted_at.is_(None), Record.date.in_(days))
            )).scalars().all()
            for record in records:
                at = fire_at(record, tz)
                if at is None or not (now - LATE_LIMIT <= at <= now) or record_key(record) in sent:
                    continue
                data = payload(record)
                for sub in group:
                    if sub.id not in dead and not await asyncio.to_thread(send, sub, data, key):
                        dead.append(sub.id)
                db.add(PushSent(key=record_key(record)))
                sent.add(record_key(record))
        # Отложенные «Через 10 минут»: запись могли уже выполнить или удалить — тогда молчим
        due = (await db.execute(
            select(PushSnooze).where(PushSnooze.at <= datetime.now(timezone.utc))
        )).scalars().all()
        for snooze in due:
            await db.delete(snooze)
            record = await db.get(Record, snooze.record_id)
            if record is None or record.deleted_at is not None or record.status == TaskStatus.done:
                continue
            group = owners.get((snooze.account_id, snooze.sandbox_id), [])
            data = payload(record, snoozed=True)
            for sub in group:
                if sub.id not in dead and not await asyncio.to_thread(send, sub, data, key):
                    dead.append(sub.id)
        if dead:
            await db.execute(delete(PushSubscription).where(PushSubscription.id.in_(dead)))
        # Старые отметки больше не нужны
        await db.execute(delete(PushSent).where(PushSent.sent_at < datetime.now(timezone.utc) - timedelta(days=3)))
        await db.commit()


async def loop() -> None:
    while True:
        try:
            await tick()
        except Exception as error:
            print(f"⚠️ Пуши: {error}")
        await asyncio.sleep(TICK_SECONDS)
