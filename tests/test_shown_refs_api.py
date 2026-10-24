"""«Удали эту задачу», «удали эти две», «отредактируй последнюю» после показанного списка: python tests/test_shown_refs_api.py"""
import asyncio, os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/stevens")
import httpx
from app.main import app

CLOCK = "2026-10-21T10:00:00+03:00"
# Диалоги со скриншотов Матвея 2026-10-23: фраза → kind, тексты записей в ответе (None — не проверяем)
DIALOGS = [
    [  # Пустой день, показали ближайшее — «эту задачу» про неё, а не про последнюю созданную
        ("задача на 25 октября встретиться с Лёшей в 12", "created", None),
        ("напомни сегодня в 13 не слушай", "created", ["Не слушай"]),
        ("что у меня послезавтра", "list", []),
        ("удали эту задачу", "deleted", ["Проверить смету по мобильному приложению"]),
    ],
    [
        ("задача на 25 октября позвонить маме", "created", None),
        ("задача на 25 октября купить молоко", "created", None),
        ("покажи что у меня 25 октября", "list", ["Обновить материалы по презентации", "Позвонить маме", "Купить молоко"]),
        ("удали эти три задачи", "deleted", None),
        ("покажи что у меня 25 октября", "list", []),
        ("верни все", None, None),
    ],
    [
        ("задача на 25 октября позвонить маме", "created", None),
        ("покажи что у меня 25 октября", "list", ["Обновить материалы по презентации", "Позвонить маме"]),
        ("удали эти две задачи", "deleted", ["Обновить материалы по презентации", "Позвонить маме"]),
    ],
    [
        ("задача на 25 октября позвонить маме", "created", None),
        ("задача на 25 октября купить молоко", "created", None),
        ("покажи что у меня 25 октября", "list", None),
        ("удали эти две задачи", "confirm", ["Обновить материалы по презентации", "Позвонить маме", "Купить молоко"]),
        ("нет", "cancelled", []),
    ],
    [
        ("напомни сегодня в 13 не слушай", "created", None),
        ("что у меня сегодня", "list", None),
        ("удали её", "clarify", None),  # показали три — спрашиваем какую
        ("первую", "deleted", ["Подготовиться к встрече с Пульсом"]),  # первая в «Какую?» — ближайшая впереди
    ],
    [
        ("запиши купить хлеб", "created", None),
        ("запиши купить молоко", "created", None),
        ("покажи задачи без срока", "found", ["Купить молоко", "Купить хлеб"]),
        ("отметь их выполненными", "completed", ["Купить молоко", "Купить хлеб"]),
    ],
    [
        ("удали эти две задачи", "clarify", []),  # ничего не показывали — не угадываем
        ("удали их", "clarify", []),
    ],
    [
        ("встреча с Ариной завтра в 16", "created", ["Встреча с Ариной"]),
        ("отредактируй последнюю запись", "clarify", ["Встреча с Ариной"]),
        ("на 17", "updated", ["Встреча с Ариной"]),
    ],
]


async def main():
    fails = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for dialog in DIALOGS:
                h = {"X-Client-Id": "refs-" + uuid.uuid4().hex[:12], "X-Stevens-Features": "d2,d3,d10"}
                await c.post("/api/sandbox/reset", headers=h)
                conv = str(uuid.uuid4())
                for phrase, kind, texts in dialog:
                    body = {"conversation_id": conv, "text": phrase, "source": "voice", "client_time": CLOCK,
                            "timezone": "Europe/Moscow"}
                    r = (await c.post("/api/chat", json=body, headers=h)).json()
                    got = [x["text"] for x in r["records"]]
                    ok = (kind is None or r["kind"] == kind) and (texts is None or sorted(got) == sorted(texts))
                    if not ok:
                        fails += 1
                        print(f"«{phrase}» → {r['kind']} {r['text']!r} {got}, ждали {kind} {texts}")
    print("fails:", fails)
    sys.exit(1 if fails else 0)

asyncio.run(main())
