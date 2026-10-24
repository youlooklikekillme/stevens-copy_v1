"""Дотошная проверка 23.10: время словами, края дат, повторы, голые дела. python tests/test_deep_check_api.py"""
import asyncio, os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/stevens")
import httpx
from app.main import app

CLOCK = "2026-10-21T10:00:00+03:00"  # среда
# Диалоги: фраза → kind, кусок текста ответа (None — не проверяем), (текст, дата, время) записи с меткой new или deleted
DIALOGS = [
    # «Я свободен в 15?» — да или нет (первыми: дальше диалоги занимают эти часы)
    [("я свободен в пятницу в 15?", "clarify", "в 15:00 свободно", None)],
    [("свободен ли я завтра в 9:30", "clarify", "«Подготовить вопросы для Максимом»", None)],
    # Время словами
    [("напомни завтра в полдень позвонить маме", "created", None, ("Позвонить маме", "2026-10-22", "12:00"))],
    [("напомни в полночь выпить таблетку", "created", None, ("Выпить таблетку", "2026-10-22", "00:00"))],
    [("напомни в пятницу в пол восьмого вечера ужин", "created", None, ("Ужин", "2026-10-23", "19:30"))],
    [("встреча в четверг без четверти девять утра с Олей", "created", None, ("Встреча с Олей", "2026-10-22", "08:45"))],
    [("напомни в субботу в половине девятого утра бег", "created", None, ("Бег", "2026-10-24", "08:30"))],
    [("поставь встречу с Петей завтра на 9 утра", "created", None, ("Встреча с Петей", "2026-10-22", "09:00"))],
    # «Да» — не имя участника; новая просьба посреди вопроса — новое дело
    [("встреча в четверг", "clarify", None, None),
     ("да", "clarify", None, None),
     # Контракт v1.5, Ф6: пока встреча не дописана, новую не записываем — повторяем вопрос
     ("завтра в 7 вечера кино с Петей", "clarify", "«Встреча»", None),
     ("с Олегом", "clarify", None, None),
     ("в 15", "created", None, ("Встреча с Олегом", "2026-10-22", "15:00"))],
    # Повторы: список, «только завтрашнюю»
    [("каждый день в 8 утра пить воду", "created", None, None),
     ("покажи повторы", "found", "повтор", None),
     ("удали только завтрашнюю воду", "deleted", None, ("Пить воду", "2026-10-22", "08:00"))],
    # Голое дело без срока и «купил» — отметка
    [("купить хлеб", "created", None, ("Купить хлеб", None, None)),
     ("хлеб купил", "completed", None, None)],
    [("позвонить в банк", "created", None, None),
     ("позвонила в банк", "completed", None, None)],
    [("сделать что-нибудь", "not_understood", None, None)],
    # Дата без года в прошлом — следующий год; DD.MM рядом с временем — дата
    [("@", "2026-12-31T22:00:00+03:00"),
     ("напомни 1 января в 12 поздравить бабушку", "created", None, ("Поздравить бабушку", "2027-01-01", "12:00"))],
    [("@", "2026-12-31T22:00:00+03:00"),
     ("напомни 05.01 в 10 к врачу", "created", None, ("К врачу", "2027-01-05", "10:00"))],
    [("встреча с Ваней 25.12 в 15:00", "created", None, ("Встреча с Ваней", "2026-12-25", "15:00"))],
    # Время уже прошло — «Завтра» вместо «Сегодня»
    [("напомни сегодня в 8 утра зарядка", "clarify", None, None),
     ("завтра в 08:00", "created", None, ("Зарядка", "2026-10-22", "08:00"))],
    # Поправка сразу после записи: «нет, в 11», «а на час раньше», «не, на пятницу», просто «удали»
    [("напомни завтра в 10 позвонить Ивану", "created", None, None),
     ("нет, в 11", "updated", None, ("Позвонить Ивану", "2026-10-22", "11:00")),
     ("а на час раньше", "updated", None, ("Позвонить Ивану", "2026-10-22", "10:00")),
     ("не, на пятницу", "updated", None, ("Позвонить Ивану", "2026-10-23", "10:00")),
     ("удали", "deleted", None, ("Позвонить Ивану", "2026-10-23", "10:00"))],
    # Конфликт: «другое время» голосом — как кнопка
    [("запиши встречу с Олегом в пятницу в 15", "created", None, None),
     ("поставь созвон с Аней в пятницу в 15", "conflict", None, None),
     ("другое время", "clarify", None, None),
     ("в 16", "created", None, ("Созвон с Аней", "2026-10-23", "16:00"))],
    # «Вечера» в ответ на уточнение
    [("напомни в 7 позвонить сестре", "clarify", None, None),
     ("вечера", "clarify", None, None),
     ("завтра", "created", None, ("Позвонить сестре", "2026-10-22", "19:00"))],
    # Приветствие, прощание
    [("привет", "help", None, None)],
    [("ты кто", "help", None, None)],
    [("пока", "thanks", None, None)],
    # Свободные дни недели — сравнение дней
    [("какие дни свободны на следующей неделе", "clarify", "свобод", None)],
    # Несуществующая дата
    [("что у меня 32 октября", "clarify", "32 октября", None)],
    # Отметить всё за день
    [("отметь всё на сегодня выполненным", "completed", None, None)],
    # «25 числа», «через час двадцать»
    [("напомни 25 числа оплатить кредит", "clarify", None, None),
     ("в 10", "created", None, ("Оплатить кредит", "2026-10-25", "10:00"))],
    [("напомни через час двадцать позвонить", "created", None, ("Позвонить", "2026-10-21", "11:20"))],
]


async def main():
    fails = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            h = {"X-Client-Id": "self-" + uuid.uuid4().hex[:12], "X-Stevens-Features": "d1,d2,d3,d10"}
            await c.post("/api/sandbox/reset", headers=h)
            for dialog in DIALOGS:
                conv = str(uuid.uuid4())
                clock = CLOCK
                if dialog[0][0] == "@":
                    clock, dialog = dialog[0][1], dialog[1:]
                for phrase, kind, says, record in dialog:
                    body = {"conversation_id": conv, "text": phrase, "source": "voice", "client_time": clock,
                            "timezone": "Europe/Moscow"}
                    r = (await c.post("/api/chat", json=body, headers=h)).json()
                    if kind is None:
                        continue
                    marked = [(x["text"], x["date"], x["time"]) for x in r["records"]
                              if "new" in x["marks"] or "deleted" in x["marks"]]
                    if phrase == "верни" and kind == "created" and r["text"].startswith(("Вернул", "Готово,")) and len(r["records"]) != 1:
                        print(f"«{phrase}» → {len(r['records'])} карточек вместо одной")
                        fails += 1
                    ok = r["kind"] == kind and (says is None or says.lower() in r["text"].lower()) and (record is None or record in marked)
                    if not ok:
                        fails += 1
                        print(f"«{phrase}» → {r['kind']} {r['text']!r} {marked}, ждали {kind} {says!r} {record}")
    print("fails:", fails)
    sys.exit(1 if fails else 0)


asyncio.run(main())
