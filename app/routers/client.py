"""Журнал сбоев сайта (POST /api/log, Ф0) и распознавание речи на сервере (POST /api/stt, Ф4)."""
import os

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app import features
from app.chatlog import enabled as log_enabled, who
from app.database import AsyncSessionLocal
from app.models import ClientLog
from app.owner import Owner, get_owner

router = APIRouter(prefix="/api", tags=["client"])

STT_URL = "https://stt.api.cloud.yandex.net/speech/v1/stt:recognize"
STT_MAX_BYTES = 1024 * 1024
STT_TYPES = ("audio/ogg", "audio/webm")


class ClientEvent(BaseModel):
    event: str = Field(min_length=1, max_length=64)
    detail: str | None = Field(default=None, max_length=2000)
    client_time: str | None = None


@router.post("/log", status_code=204)
async def client_log(body: ClientEvent, owner: Owner = Depends(get_owner)):
    """Неизвестный event не ошибка — просто пишем. Сбой журнала ответ не ломает."""
    if log_enabled():
        try:
            async with AsyncSessionLocal() as db:
                db.add(ClientLog(who=who(owner), event=body.event[:64], detail=(body.detail or "")[:2000] or None))
                await db.commit()
        except Exception as error:  # noqa: BLE001
            print(f"client log error: {error!r}")
    return Response(status_code=204)


@router.post("/stt")
async def stt(request: Request, owner: Owner = Depends(get_owner)):
    """Аудио одной фразы → {"text": "…"} через Yandex SpeechKit. Нет ключа или сервис упал — 503."""
    kind = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if kind not in STT_TYPES:
        return JSONResponse(status_code=415, content={"error": "unsupported_audio"})
    audio = await request.body()
    if len(audio) > STT_MAX_BYTES:
        return JSONResponse(status_code=413, content={"error": "audio_too_long"})
    if not features.on("stt"):
        return JSONResponse(status_code=503, content={"error": "stt_unavailable"})
    if not audio:
        return {"text": ""}
    params = {"lang": "ru-RU", "folderId": os.getenv("YANDEX_FOLDER_ID"), "format": "oggopus"}
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            answer = await client.post(STT_URL, params=params, content=audio,
                                       headers={"Authorization": f"Api-Key {os.getenv('YANDEX_API_KEY')}"})
        answer.raise_for_status()
        return {"text": (answer.json().get("result") or "").strip()}
    except Exception as error:  # noqa: BLE001
        print(f"stt error: {error!r}")
        return JSONResponse(status_code=503, content={"error": "stt_unavailable"})
