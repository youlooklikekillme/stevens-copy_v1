"""Таблицы по контракту v1.4: аккаунты по логину (раздел 14), песочницы по
X-Client-Id, записи с мягким удалением (15.4).

Имена таблиц новые (accounts, records…): старые таблицы прежней версии
(users, tasks) в той же базе не мешают и не используются.
"""
import enum
from datetime import datetime as dt

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class TaskType(str, enum.Enum):
    task = "task"
    meeting = "meeting"
    reminder = "reminder"
    note = "note"          # заметка: без даты и времени, вне расписания
    busy = "busy"          # занятость: «работа с 9 до 18» — интервал в расписании, без напоминаний и галочки


class TaskStatus(str, enum.Enum):
    open = "open"
    done = "done"


class DateKind(str, enum.Enum):
    exact = "exact"        # в этот момент
    deadline = "deadline"  # до этой даты
    none = "none"          # без срока


class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Храним как ввели, сравниваем в нижнем регистре (Р-189)
    login: Mapped[str] = mapped_column(String(32), nullable=False)
    login_lower: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[dt] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AuthToken(Base):
    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[dt] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[dt | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Sandbox(Base):
    """Песочница: своя копия демо-данных на устройство (раздел 14)."""
    __tablename__ = "sandboxes"

    client_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[dt] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Когда вкладка заходила последний раз (с точностью до часа): по нему чистим заброшенные
    seen_at: Mapped[dt] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Series(Base):
    """Повтор по дням недели: «каждый вторник в 10 планёрка», «по средам до конца года».
    Дни разворачиваются в обычные записи (series_id), бессрочный повтор — на год вперёд с продлением."""
    __tablename__ = "series"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    sandbox_id: Mapped[str | None] = mapped_column(ForeignKey("sandboxes.client_id"), nullable=True, index=True)
    type: Mapped[TaskType] = mapped_column(Enum(TaskType, name="record_type"), nullable=False)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # занятость: до скольки
    weekdays: Mapped[str] = mapped_column(String(13), nullable=False)    # «1,3» — пн = 0
    until: Mapped[str | None] = mapped_column(String(10), nullable=True)  # последний день, None — без конца
    filled_until: Mapped[str] = mapped_column(String(10), nullable=False)  # до какого дня записи уже созданы
    ended: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # «убери вообще»: не продлевать
    created_at: Mapped[dt] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def repeat(self) -> str:
        """Что пишем в записи: «1,3» или «1,3;2026-12-31»."""
        return f"{self.weekdays};{self.until}" if self.until else self.weekdays


class Record(Base):
    __tablename__ = "records"
    __table_args__ = (
        Index("ix_records_account_date", "account_id", "date"),
        Index("ix_records_sandbox_date", "sandbox_id", "date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # Владелец — ровно один из двух
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True)
    sandbox_id: Mapped[str | None] = mapped_column(ForeignKey("sandboxes.client_id"), nullable=True)

    type: Mapped[TaskType | None] = mapped_column(Enum(TaskType, name="record_type"), nullable=True)
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    date: Mapped[str | None] = mapped_column(String(10), nullable=True)  # "YYYY-MM-DD"
    time: Mapped[str | None] = mapped_column(String(5), nullable=True)   # "HH:MM"
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)  # "HH:MM": до скольки, у занятости
    date_kind: Mapped[DateKind] = mapped_column(Enum(DateKind, name="record_date_kind"), nullable=False)
    status: Mapped[TaskStatus | None] = mapped_column(Enum(TaskStatus, name="record_status"), nullable=True)
    created_at: Mapped[dt | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_at: Mapped[dt | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Повтор: из какой серии и её правило (копия, чтобы карточка показывала «каждый вт» без лишнего запроса)
    series_id: Mapped[int | None] = mapped_column(ForeignKey("series.id"), nullable=True, index=True)
    repeat: Mapped[str | None] = mapped_column(String(24), nullable=True)
    # Демо-данные песочницы из CSV: время создания у них есть, но уведомления по ним не звонят
    demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false", nullable=False)

    def repeat_contract(self) -> dict | None:
        if not self.repeat:
            return None
        days, _, until = self.repeat.partition(";")
        return {"weekdays": [int(d) for d in days.split(",")], "until": until or None}

    def to_contract(self, marks: list[str] | None = None, highlight: str | None = None) -> dict:
        """Record из раздела 6 контракта."""
        return {
            "id": self.id,
            "type": self.type.value if self.type else None,
            "text": self.text,
            "date": self.date,
            "time": self.time,
            "end_time": self.end_time,
            "date_kind": self.date_kind.value,
            "status": self.status.value if self.status else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "repeat": self.repeat_contract(),
            "demo": bool(self.demo),
            "marks": marks if marks is not None else (["deleted"] if self.deleted_at else []),
            "highlight": highlight,
        }


class PushSubscription(Base):
    """Подписка браузера на пуш-уведомления (Web Push). Владелец — как у записей."""
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    sandbox_id: Mapped[str | None] = mapped_column(ForeignKey("sandboxes.client_id"), nullable=True, index=True)
    endpoint: Mapped[str] = mapped_column(String(1000), unique=True, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(200), nullable=False)
    auth: Mapped[str] = mapped_column(String(100), nullable=False)
    # Часовой пояс браузера: записи хранятся в местном времени
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[dt] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushSent(Base):
    """Уже отправленные пуши: запись и момент, на который она стояла."""
    __tablename__ = "push_sent"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    sent_at: Mapped[dt] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushSnooze(Base):
    """«Через 10 минут» в пуше: когда напомнить о записи ещё раз."""
    __tablename__ = "push_snoozes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    account_id: Mapped[int | None] = mapped_column(ForeignKey("accounts.id"), nullable=True, index=True)
    sandbox_id: Mapped[str | None] = mapped_column(ForeignKey("sandboxes.client_id"), nullable=True, index=True)
    record_id: Mapped[int] = mapped_column(nullable=False)
    at: Mapped[dt] = mapped_column(DateTime(timezone=True), nullable=False, index=True)


class AppSetting(Base):
    """Настройки сервера, которые нужно помнить между запусками (ключи VAPID)."""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(4000), nullable=False)


class ChatLog(Base):
    """Журнал чата: что спросили и что ответил Stevens — чтобы видеть, где он ошибается."""
    __tablename__ = "chat_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    at: Mapped[dt] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    who: Mapped[str] = mapped_column(String(16), index=True)          # обезличенный владелец
    conversation_id: Mapped[str] = mapped_column(String(64), index=True)
    source: Mapped[str] = mapped_column(String(8))                    # voice | text | button
    text: Mapped[str] = mapped_column(String(300), default="")
    action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    waiting: Mapped[str | None] = mapped_column(String(16), nullable=True)   # чего Stevens ждал до запроса
    intent: Mapped[str | None] = mapped_column(String(16), nullable=True)
    llm: Mapped[bool] = mapped_column(Boolean, default=False)
    kind: Mapped[str] = mapped_column(String(16), index=True)         # kind ответа
    template: Mapped[str | None] = mapped_column(String(48), nullable=True)
    reply: Mapped[str] = mapped_column(Text, default="")
    records: Mapped[int] = mapped_column(Integer, default=0)
    ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
