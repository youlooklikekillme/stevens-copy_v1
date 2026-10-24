"""Самопроверка 23.10: фразы, которые раньше ломались. python tests/test_selfcheck_api.py"""
import asyncio, os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/stevens")
import httpx
from app.main import app

CLOCK = "2026-10-21T10:00:00+03:00"  # среда
# Диалоги: фраза → kind, кусок текста ответа (None — не проверяем), (текст, дата, время) записи с меткой new или deleted
DIALOGS = [
    # Дни без предлога и вопросы про день
    [("покажи пятницу", "list", "пт, 23 окт", None)],
    [("что в понедельник", "list", "пн, 26 окт", None)],
    [("что у меня на выходных", "list", "На выходных", None)],
    [("что на этой неделе", "list", "на этой неделе", None)],
    [("Сколько у меня дел завтра?", "list", None, None)],
    [("Что я делал на прошлой неделе?", "list", "На прошлой неделе", None)],
    [("что у меня завтра утром", "list", "утром", None)],
    [("Напомни позвонить врачу", "clarify", None, None),
     ("завтра", "clarify", None, None),   # ответ на уточнение, а не «что у меня завтра»
     ("в 11", "created", None, ("Позвонить врачу", "2026-10-22", "11:00"))],
    [("Встреча с Ваней завтра в 14", "created", None, None),
     ("а на завтра?", "list", None, None)],
    # Свободное время вопросом
    [("есть окно в пятницу?", "clarify", "свобод", None)],
    [("я свободен в пятницу в 15?", "clarify", "свобод", None)],
    # «Отмени …» с делом — удаление, а не встреча «Отмени встречу»
    [("Поставь встречу с Олегом завтра в 15", "created", None, None),
     ("отмени встречу с Олегом", "deleted", None, ("Встреча с Олегом", "2026-10-22", "15:00"))],
    # «Отмени», «верни» словами — как кнопки
    [("Напомни через 20 минут выключить духовку", "created", None, None),
     ("отмени", "deleted", None, ("Выключить духовку", "2026-10-21", "10:20")),
     ("ок", "thanks", None, None),
     ("отмени", "cancelled", "нечего", None)],
    # Перенос по контексту без глагола
    [("Встреча с Иваном завтра в 13", "created", None, None),
     ("а теперь на 12", "updated", None, ("Встреча с Иваном", "2026-10-22", "12:00")),
     ("лучше в субботу", "updated", None, ("Встреча с Иваном", "2026-10-24", "12:00")),
     ("нет, лучше на понедельник в 10", "updated", None, ("Встреча с Иваном", "2026-10-26", "10:00")),
     ("сделай задачей", "updated", None, None)],
    # Выбор из «Какую из них?» голосом
    [("найди встречу с Марией", "found", None, None),
     ("перенеси её на завтра", "clarify", None, None),
     ("на 15", "clarify", None, None),
     ("вторую", "updated", None, ("Встреча с Марией по сайту", "2026-10-22", "17:00"))],
    [("Удали встречу с Марией по договору и по интеграции", None, None, None)],
    [("найди встречу с Марией", "found", None, None),
     ("удали её", "clarify", None, None),
     ("ту что в субботу", "deleted", None, ("Встреча с Марией по интеграции", "2026-10-24", "20:45")),
     ("верни", "created", None, ("Встреча с Марией по интеграции", "2026-10-24", "20:45"))],
    # Конфликт встреч: «да» переспрашивает, время в ответе — новая встреча на это время, «оставь обе»
    [("поставь встречу с Аней завтра в 16", "created", None, None),
     ("поставь встречу с Борей завтра в 16", "conflict", None, None),
     ("да", "conflict", None, None),
     ("давай в 19", "created", None, ("Встреча с Борей", "2026-10-22", "19:00"))],
    [("поставь встречу с Гошей завтра в 18", "created", None, None),
     ("поставь встречу с Димой завтра в 18", "conflict", None, None),
     ("оставь обе", "created", None, ("Встреча с Димой", "2026-10-22", "18:00"))],
    # Новая просьба посреди уточнения — запись держится (контракт v1.5, Ф6), вопрос повторяем; вопрос про планы — отвечаем
    [("Запиши на завтра встретиться с Алексеем", "clarify", None, None),
     ("в субботу в 12 сходить в кино", "clarify", None, None),
     ("что у меня в субботу", "list", None, None),
     ("в 15", "created", None, ("Встреча с Алексеем", "2026-10-22", "15:00"))],
    # Период у задачи — срок до конца периода
    [("на этой неделе позвонить бабушке", "created", None, ("Позвонить бабушке", "2026-10-25", None))],
    [("в следующем месяце сдать отчёт", "created", None, ("Сдать отчёт", "2026-11-30", None))],
    # «И ещё …», несколько дел с «во вторник»
    [("и ещё в пятницу купить подарок Лене", "created", None, ("Купить подарок Лене", "2026-10-23", None))],
    [("Напомни мне в понедельник позвонить в банк и во вторник забрать документы", "clarify", "позвонить в банк", None),
     ("в 10", "clarify", None, None),
     ("в 11", "created", None, ("Забрать документы", "2026-10-27", "11:00"))],
    # Ближайшее дело
    [("когда у меня ближайшая встреча", "found", None, None)],
    [("какое у меня следующее дело", "found", None, None)],
    # «Да/неа» на массовое удаление, «верни» после него
    [("Удали все дела на следующей неделе", "confirm", None, None),
     ("неа", "cancelled", None, None)],
    [("Отлично, спасибо.", "thanks", None, None)],
    # «Сделал» в конце фразы; встречу не отмечают — «да» удаляет её
    [("Запиши: купить корм коту", "created", None, None),
     ("Купить корм коту сделал", "completed", None, None)],
    [("Встреча с Ольгой завтра в 20", "created", None, None),
     ("встречу с Ольгой — готово", "clarify", None, None),
     ("да", "deleted", None, ("Встреча с Ольгой", "2026-10-22", "20:00"))],
    # Вернули повтор — одна карточка, а не все дни
    [("Каждый вторник в 9 планёрка до июня", "created", None, None),
     ("удали планёрку вообще", "deleted", None, None),
     ("верни", "created", None, ("Планёрка", "2026-10-27", "09:00"))],
]


async def main():
    fails = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            h = {"X-Client-Id": "self-" + uuid.uuid4().hex[:12], "X-Stevens-Features": "d1,d2,d3,d10"}
            await c.post("/api/sandbox/reset", headers=h)
            for dialog in DIALOGS:
                conv = str(uuid.uuid4())
                for phrase, kind, says, record in dialog:
                    body = {"conversation_id": conv, "text": phrase, "source": "voice", "client_time": CLOCK,
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
