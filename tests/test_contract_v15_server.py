"""Контракт v1.5: health с фичами, STEVENS_FEATURES, /api/log, raw_text, /api/stt, remind_before. python tests/test_contract_v15_server.py"""
import asyncio, json, os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/stevens")
os.environ.pop("YANDEX_API_KEY", None)
import httpx
from sqlalchemy import select
from app.main import app
from app.database import AsyncSessionLocal
from app.models import ChatLog, ClientLog, Record
from app import push

CLOCK = "2026-10-21T10:00:00+03:00"
fails = 0


def check(name, ok, info=""):
    global fails
    print(("OK  " if ok else "FAIL"), name, info if not ok else "")
    fails += 0 if ok else 1


async def main():
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            h = {"X-Client-Id": "v15-" + uuid.uuid4().hex[:12]}
            await c.post("/api/sandbox/reset", headers=h)
            health = (await c.get("/api/health")).json()
            check("health: features", health["status"] == "ok" and "f10" in health["features"] and "stt" not in health["features"], health)

            os.environ["STEVENS_FEATURES"] = "f2,f3,f7"
            health = (await c.get("/api/health")).json()
            check("STEVENS_FEATURES сужает список", health["features"] == ["f2", "f3", "f7"], health)
            r = (await c.post("/api/chat", headers=h, json={"conversation_id": str(uuid.uuid4()), "text": "Напомни завтра в 10 позвонить Ивану",
                                                            "source": "text", "client_time": CLOCK, "timezone": "Europe/Moscow"})).json()
            check("выключенная Ф5 — не понял", r["kind"] == "not_understood", r["kind"])
            os.environ.pop("STEVENS_FEATURES")

            r = await c.post("/api/log", headers=h, json={"event": "mic_failed", "detail": "not-allowed", "client_time": CLOCK})
            check("/api/log 204", r.status_code == 204, r.status_code)
            r = await c.post("/api/log", headers=h, json={"event": "что-то новое"})
            check("/api/log неизвестный event — 204", r.status_code == 204, r.status_code)

            conv = str(uuid.uuid4())
            r = (await c.post("/api/chat", headers=h, json={"conversation_id": conv, "text": "Встреча с Олегом завтра в 15", "source": "voice",
                                                            "raw_text": "встреча с олегом завтра в 15", "client_time": CLOCK, "timezone": "Europe/Moscow"})).json()
            rec = r["records"][0]
            check("встреча: remind_before 15 по умолчанию", rec["remind_before"] == 15, rec)
            r = await c.patch(f"/api/records/{rec['id']}", headers=h, json={"remind_before": 30})
            check("PATCH remind_before", r.status_code == 200 and r.json()["record"]["remind_before"] == 30, r.text)
            bad = await c.patch(f"/api/records/{rec['id']}", headers=h, json={"remind_before": 2000})
            check("PATCH remind_before > 1440 — 422", bad.status_code == 422, bad.status_code)
            r2 = (await c.post("/api/chat", headers=h, json={"conversation_id": conv, "text": "Напомни завтра в 9 выпить воды", "source": "text",
                                                             "client_time": CLOCK, "timezone": "Europe/Moscow"})).json()
            bad = await c.patch(f"/api/records/{r2['records'][0]['id']}", headers=h, json={"remind_before": 30})
            check("remind_before у напоминания — 422", bad.status_code == 422, bad.status_code)

            r = await c.post("/api/stt", headers={**h, "Content-Type": "audio/ogg"}, content=b"x" * 10)
            check("/api/stt без ключа — 503", r.status_code == 503, r.status_code)
            r = await c.post("/api/stt", headers={**h, "Content-Type": "audio/mpeg"}, content=b"x")
            check("/api/stt чужой формат — 415", r.status_code == 415, r.status_code)
            r = await c.post("/api/stt", headers={**h, "Content-Type": "audio/ogg"}, content=b"x" * (1024 * 1024 + 1))
            check("/api/stt больше 1 МБ — 413", r.status_code == 413, r.status_code)

        await asyncio.sleep(0.3)  # журнал пишется после ответа
        async with AsyncSessionLocal() as db:
            raw = (await db.execute(select(ChatLog.raw_text).where(ChatLog.conversation_id == conv))).scalars().all()
            check("raw_text в журнале", "встреча с олегом завтра в 15" in raw, raw)
            events = (await db.execute(select(ClientLog.event))).scalars().all()
            check("client_log пишется", "mic_failed" in events, events)
            meeting = await db.get(Record, rec["id"])
            body = json.loads(push.payload(meeting))["body"]
            check("пуш встречи: «Через полчаса у вас встреча.»", body == "Через полчаса у вас встреча.\nВстреча с Олегом", body)
            check("встреча звонит за remind_before", (push.fire_at(meeting, push.zone("Europe/Moscow")).strftime("%H:%M")) == "14:30")
    print("fails:", fails)
    sys.exit(1 if fails else 0)


asyncio.run(main())
