"""Тестовые фразы контракта v1.5 (раздел 10): python tests/test_contract_v15.py [-v] [id…]

Сверяем kind, awaiting_reply и первую запись с меткой new / deleted / draft (тип, текст, дата, время).
Текст ответа — по смыслу, поэтому дословно не сверяем; ремарки «expect» ручных диалогов проверяют отдельные тесты.
"""
import asyncio, json, os, sys, uuid
sys.path.insert(0, os.getcwd())
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres@localhost:5433/stevens")
import httpx
from app.main import app

DATA = json.load(open(os.path.join(os.path.dirname(__file__), "data", "contract_v15_phrases.json")))
VERBOSE = "-v" in sys.argv
# Снятые на сайте диалоги, которые расходятся с правилами контракта — правило важнее (сообщено в тред контракта)
OVERRIDE = {
    # Ф6: пока Стивенс переспрашивает о записи, новую не делаем — hold_reask (раздел 7, Ф6)
    ("Т6-4", 1): {"kind": "clarify", "awaiting_reply": True, "record": None},
    ("Т6-6", 1): {"kind": "clarify", "awaiting_reply": True, "record": None},
    # Снято на аккаунте, где уже была задача без срока: на пустом аккаунте предложения нет
    ("Т7-16", 0): {"awaiting_reply": False},
}
ONLY = [a for a in sys.argv[1:] if not a.startswith("-")]


def first_marked(records):
    for r in records:
        if {"new", "deleted", "draft"} & set(r["marks"]):
            return r
    return None


async def run_dialog(c, d) -> list[str]:
    if d["mode"] == "sandbox":
        h = {"X-Client-Id": "c15-" + uuid.uuid4().hex[:12]}
        await c.post("/api/sandbox/reset", headers=h)
    else:
        r = await c.post("/api/auth/register", json={"login": "c" + uuid.uuid4().hex[:12], "password": "12345"})
        h = {"Authorization": "Bearer " + r.json()["token"]}
    h["X-Stevens-Features"] = "d2,d3,d10"
    conv, last, errors = str(uuid.uuid4()), None, []
    for index, step in enumerate(d["steps"]):
        step = {**step, **OVERRIDE.get((d["id"], index), {})}
        body = {"conversation_id": conv, "source": "voice", "client_time": d.get("clock") or DATA["meta"]["clock"],
                "timezone": "Europe/Moscow", "action": None}
        if "press" in step:
            label = step["press"]
            if label == "[действие briefing]":
                body.update(text="План на сегодня", source="button", action={"id": "briefing"})
            else:
                btn = next((b for b in (last or {}).get("buttons", []) if b["label"] == label), None)
                if not btn:
                    errors.append(f"нет кнопки «{label}»")
                    break
                body.update(text=btn["send"].get("text") or label, source="button", action=btn["send"].get("action"))
        else:
            body["text"] = step["say"]
        last = (await c.post("/api/chat", json=body, headers=h)).json()
        said = step.get("say") or step.get("press")
        if VERBOSE:
            print(f"  {said!r} → {last['kind']} {last['text']!r}")
        if last["kind"] != step["kind"]:
            errors.append(f"«{said}» → {last['kind']} {last['text']!r}, ждали {step['kind']}")
            break
        if (d["id"], index) in OVERRIDE and OVERRIDE[(d["id"], index)].get("kind"):
            break  # дальше диалог по снятому сценарию не идёт
        if "awaiting_reply" in step and last["awaiting_reply"] != step["awaiting_reply"]:
            errors.append(f"«{said}» → awaiting_reply {last['awaiting_reply']}")
        want = step.get("record")
        got = first_marked(last["records"])
        # Без меток (вопрос, список) первая запись в эталоне — просто первая показанная: её не сверяем
        if want and (got or last["kind"] in ("created", "updated")):
            fields = ("type", "text", "date", "time")
            if not got or any(want.get(k) != got.get(k) for k in fields if k in want):
                errors.append(f"«{said}» → запись {got and {k: got.get(k) for k in fields}}, ждали {want}")
    return errors


async def main():
    fails = 0
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            for d in DATA["dialogs"]:
                if ONLY and d["id"] not in ONLY:
                    continue
                if VERBOSE:
                    print(d["id"])
                errors = await run_dialog(c, d)
                if errors:
                    fails += 1
                    print(d["id"], d.get("note") or "", "; ".join(errors))
    print("fails:", fails)
    sys.exit(1 if fails else 0)


asyncio.run(main())
