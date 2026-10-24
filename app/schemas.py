"""Тело запроса POST /api/chat (раздел 4 контракта v1.4). Ответ собирается
словарём в app/routers/chat.py — ровно поля раздела 5."""
from typing import Literal, Optional

from pydantic import BaseModel, Field


class Action(BaseModel):
    id: str
    record_id: Optional[int] = None


class ChatRequest(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=128)
    text: str = Field(default="", max_length=300)
    source: Literal["voice", "text", "button"] = "text"
    action: Optional[Action] = None
    client_time: Optional[str] = None
    timezone: Optional[str] = None
    raw_text: Optional[str] = Field(default=None, max_length=600)  # голос до поправок ослышки — только для журнала
