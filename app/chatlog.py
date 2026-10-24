"""Журнал чата в таблице chat_log: каждый запрос и ответ Stevens.

Владелец обезличен (хеш), пароли и токены не пишем. Выключить: STEVENS_CHAT_LOG=0.
Отчёт: python -m app.log_report
"""
import hashlib
import os

from app.database import AsyncSessionLocal
from app.models import ChatLog


def enabled() -> bool:
    return os.getenv("STEVENS_CHAT_LOG", "1") != "0"


def who(owner) -> str:
    return hashlib.sha256(owner.key.encode()).hexdigest()[:12]


async def write(owner, payload, answer: dict, trace: dict, waiting: str | None, ms: int) -> None:
    """Пишем отдельной сессией: сбой журнала не ломает ответ пользователю."""
    if not enabled():
        return
    try:
        async with AsyncSessionLocal() as db:
            db.add(ChatLog(
                who=who(owner), conversation_id=payload.conversation_id[:64], source=payload.source,
                text=(payload.text or "")[:300], raw_text=(payload.raw_text or "")[:600] or None, action=payload.action.id[:32] if payload.action else None,
                waiting=waiting, intent=trace.get("intent"), llm=bool(trace.get("llm")),
                kind=answer.get("kind", "")[:16], template=(trace.get("template") or "")[:48] or None,
                reply=answer.get("text", ""), records=len(answer.get("records") or []), ms=ms,
                error=trace.get("error"),
            ))
            await db.commit()
    except Exception as error:  # noqa: BLE001
        print(f"chat log error: {error!r}")
