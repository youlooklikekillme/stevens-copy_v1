"""Кнопка «Через 10 минут» в пуше: /api/push/snooze и повторный пуш из push.tick."""
import asyncio, json, os, sys, uuid
from datetime import datetime, timedelta, timezone
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/stevens")
import httpx
from sqlalchemy import select, update
from app import push
from app.database import AsyncSessionLocal
from app.main import app
from app.models import PushSnooze

sent = []
push.send = lambda sub, data, key: (sent.append((sub.endpoint, json.loads(data))), True)[1]


async def main():
    fails = 0
    def check(name, ok):
        nonlocal fails
        print(("OK  " if ok else "FAIL"), name)
        fails += 0 if ok else 1
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            h = {"X-Client-Id": "snz-" + uuid.uuid4().hex[:12]}
            other = {"X-Client-Id": "snz-" + uuid.uuid4().hex[:12]}
            await c.post("/api/sandbox/reset", headers=h)
            await c.post("/api/sandbox/reset", headers=other)
            body = {"conversation_id": str(uuid.uuid4()), "text": "Напомни завтра в 10 позвонить Ивану", "source": "text",
                    "action": None, "client_time": "2026-10-21T10:00:00+03:00", "timezone": "Europe/Moscow"}
            r = (await c.post("/api/chat", json=body, headers=h)).json()
            rid = r["records"][0]["id"]
            endpoint = "https://push.example/" + uuid.uuid4().hex
            sub = {"endpoint": endpoint, "keys": {"p256dh": "x", "auth": "y"}, "timezone": "Europe/Moscow"}
            check("подписка", (await c.post("/api/push/subscribe", json=sub, headers=h)).status_code == 204)
            foreign = "https://push.example/" + uuid.uuid4().hex
            await c.post("/api/push/subscribe", json={**sub, "endpoint": foreign}, headers=other)
            check("чужой браузер не отложит", (await c.post("/api/push/snooze", json={"endpoint": foreign, "record_id": rid})).status_code == 404)
            check("неизвестный браузер", (await c.post("/api/push/snooze", json={"endpoint": "https://nope", "record_id": rid})).status_code == 404)
            check("плохой запрос", (await c.post("/api/push/snooze", json={"endpoint": endpoint})).status_code == 422)
            check("отложить", (await c.post("/api/push/snooze", json={"endpoint": endpoint, "record_id": rid})).status_code == 204)
            check("повтор не плодит", (await c.post("/api/push/snooze", json={"endpoint": endpoint, "record_id": rid})).status_code == 204)
            async with AsyncSessionLocal() as db:
                rows = (await db.execute(select(PushSnooze).where(PushSnooze.record_id == rid))).scalars().all()
                check("одна отложка через ~10 минут", len(rows) == 1 and timedelta(minutes=9) < rows[0].at - datetime.now(timezone.utc) <= timedelta(minutes=10))
            await push.tick()
            check("раньше срока не шлёт", not [s for s in sent if s[0] == endpoint])
            async with AsyncSessionLocal() as db:
                await db.execute(update(PushSnooze).where(PushSnooze.record_id == rid).values(at=datetime.now(timezone.utc)))
                await db.commit()
            await push.tick()
            mine = [d for e, d in sent if e == endpoint]
            check("пуш ещё раз", len(mine) == 1 and mine[0]["record_id"] == rid and mine[0]["snoozed"] is True and mine[0]["body"] == "Позвольте напомнить ещё раз.\nПозвонить Ивану")
            check("чужим не шлёт", not [s for s in sent if s[0] == foreign])
            await push.tick()
            check("только один раз", len([d for e, d in sent if e == endpoint]) == 1)
            # Выполненное за эти 10 минут — молчим
            await c.post("/api/push/snooze", json={"endpoint": endpoint, "record_id": rid})
            await c.patch(f"/api/records/{rid}", json={"status": "done"}, headers=h)
            async with AsyncSessionLocal() as db:
                await db.execute(update(PushSnooze).where(PushSnooze.record_id == rid).values(at=datetime.now(timezone.utc)))
                await db.commit()
            await push.tick()
            check("выполненное не звонит", len([d for e, d in sent if e == endpoint]) == 1)
    print("fails:", fails)
    return fails


if __name__ == "__main__":
    sys.exit(1 if asyncio.run(main()) else 0)
