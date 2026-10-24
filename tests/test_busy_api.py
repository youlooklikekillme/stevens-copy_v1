"""Занятость «с … до …»: пары, работа, учёба — python tests/test_busy_api.py"""
import asyncio, os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/stevens")
import httpx
from app.main import app

CLOCK = "2026-10-21T10:00:00+03:00"  # среда
first = lambda r: r["records"][0] if r["records"] else {}
texts = lambda r: [x["text"] for x in r["records"]]
types = lambda r: [x["type"] for x in r["records"]]
labels = lambda r: [b["label"] for b in r["buttons"]]

DIALOG = [
    # Создание: повтор с интервалом. Сегодня работа уже идёт, но не кончилась — первый день сегодня
    ("по будням работа с 9 до 18", "created",
     lambda r: first(r)["type"] == "busy" and first(r)["text"] == "Работа" and first(r)["date"] == "2026-10-21"
               and first(r)["time"] == "09:00" and first(r)["end_time"] == "18:00" and first(r)["status"] is None
               and first(r)["repeat"] == {"weekdays": [0, 1, 2, 3, 4], "until": None}
               and "по будням с 09:00 до 18:00" in r["text"]),
    ("пары по вторникам с 10 до 13:20 до июня", "created",
     lambda r: first(r)["type"] == "busy" and first(r)["text"] == "Пары" and first(r)["date"] == "2026-10-27"
               and first(r)["end_time"] == "13:20" and first(r)["repeat"]["until"] == "2027-05-31"),
    # Список: занятость не считается делом
    ("что у меня завтра", "list",
     lambda r: "дел нет" in r["text"] and "«Работа» с 09:00 до 18:00" in r["text"] and types(r) == ["busy"]),
    # Встреча на время работы — спрашиваем
    ("завтра в 15 встреча с Иваном", "conflict",
     lambda r: "«Работа»" in r["text"] and "Всё равно записать" in labels(r) and r["awaiting_reply"]),
    ("всё равно запиши", "created", lambda r: first(r)["type"] == "meeting" and first(r)["time"] == "15:00"),
    ("что у меня завтра", "list",
     lambda r: r["total"] == 1 and ("заняты" in r["text"] or "занятость" in r["text"]) and "busy" in types(r)),
    # Свободное время учитывает весь интервал
    ("когда я свободен в пятницу", "clarify", lambda r: "18:00–21:00" in r["text"] and "09:00" not in r["text"]),
    ("я свободен в пятницу в 12?", "clarify", lambda r: "Работа" in r["text"]),
    ("я свободен в субботу с 14 до 16?", "clarify", lambda r: "Поставить на 14:00" in labels(r)),
    # Встреча «с … до …» остаётся встречей и хранит конец
    ("в субботу встреча с Машей с 16 до 17:30", "created",
     lambda r: first(r)["type"] == "meeting" and first(r)["end_time"] == "17:30"),
    # Отметить нельзя
    ("отметь работу выполненной", "clarify", lambda r: "не отмечается" in r["text"] or "Отмечаются только" in r["text"]),
    # Без содержания — спросим, чем занят
    ("я занят в субботу с 12 до 15", "clarify", lambda r: r["awaiting_reply"] and "?" in r["text"]),
    ("стоматолог", "created",
     lambda r: first(r)["text"] == "Стоматолог" and first(r)["date"] == "2026-10-24" and first(r)["end_time"] == "15:00"),
    # Без конца — спросим до скольки
    ("в воскресенье в 10 занят: бассейн", "clarify", lambda r: r["awaiting_reply"]),
    ("до 12", "created",
     lambda r: first(r)["text"] == "Бассейн" and first(r)["time"] == "10:00" and first(r)["end_time"] == "12:00"),
    # Перенос сдвигает весь интервал
    ("перенеси бассейн на час позже", "updated",
     lambda r: first(r)["time"] == "11:00" and first(r)["end_time"] == "13:00"),
    # Без времени — спросим, с какого и до какого
    ("запиши занятость: учёба в понедельник", "clarify", lambda r: r["awaiting_reply"]),
    ("с 8:30 до 14", "created",
     lambda r: first(r)["type"] == "busy" and first(r)["date"] == "2026-10-26" and first(r)["time"] == "08:30"
               and first(r)["end_time"] == "14:00"),
    # «Учёба с понедельника по пятницу с 9 до 15» — повтор по будням
    ("курсы с понедельника по пятницу с 19 до 21 до конца года", "created",
     lambda r: first(r)["repeat"] == {"weekdays": [0, 1, 2, 3, 4], "until": "2026-12-31"} and first(r)["type"] == "busy"),
    # Следующее дело — не занятость
    ("какое у меня следующее дело", "found", lambda r: "busy" not in types(r)),
    # Покажи занятость
    ("покажи занятость на этой неделе", "list", lambda r: r["total"] and set(types(r)) == {"busy"}),
    # Удаление одного дня и всей серии — как у повторов
    ("удали работу в пятницу", "deleted", lambda r: first(r)["date"] == "2026-10-23"),
    ("убери пары вообще", "deleted", None),
    ("что у меня во вторник", "list", lambda r: "Пары" not in texts(r) and "Работа" in texts(r)),
]


async def main():
    fails = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            login = "bz" + uuid.uuid4().hex[:10]
            token = (await c.post("/api/auth/register", json={"login": login, "password": "secret123"})).json()["token"]
            h = {"Authorization": f"Bearer {token}", "X-Stevens-Features": "d2,d3,d10"}
            conv = str(uuid.uuid4())
            for phrase, kind, check in DIALOG:
                body = {"conversation_id": conv, "text": phrase, "source": "voice", "client_time": CLOCK,
                        "timezone": "Europe/Moscow"}
                r = (await c.post("/api/chat", json=body, headers=h)).json()
                ok = (kind is None or r["kind"] == kind) and (check is None or check(r))
                print(("ok " if ok else "BAD"), f"«{phrase}» → {r['kind']} {r['text']!r} {texts(r)[:3]} {labels(r)[:3]}")
                fails += not ok
            # Брифинг: занятость отдельной фразой, в число дел не входит
            body = {"conversation_id": str(uuid.uuid4()), "text": "", "source": "text", "client_time": CLOCK,
                    "timezone": "Europe/Moscow", "action": {"id": "briefing"}}
            r = (await c.post("/api/chat", json=body, headers=h)).json()
            ok = "Работа" in r["text"] and "busy" in types(r)
            print(("ok " if ok else "BAD"), "брифинг →", r["text"])
            fails += not ok
            # REST: конец интервала есть, отметить занятость нельзя, перенос начала сдвигает конец
            rows = (await c.get("/api/records?from=2026-10-21&to=2026-10-21", headers=h)).json()["records"]
            work = next(x for x in rows if x["type"] == "busy")
            bad = await c.patch(f"/api/records/{work['id']}", json={"status": "done"}, headers=h)
            moved = (await c.patch(f"/api/records/{work['id']}", json={"time": "10:00"}, headers=h)).json()["record"]
            ok = work["end_time"] == "18:00" and bad.status_code == 422 and moved.get("end_time") == "19:00"
            print(("ok " if ok else "BAD"), "REST", bad.status_code, moved.get("time"), moved.get("end_time"))
            fails += not ok
    print("fails:", fails)
    sys.exit(1 if fails else 0)

asyncio.run(main())
