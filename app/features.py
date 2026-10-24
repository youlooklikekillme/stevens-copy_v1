"""Включённые фичи (контракт v1.5, раздел 1.1): их список отдаёт GET /api/health.

STEVENS_FEATURES — список через запятую («f2,f3,f5»); не задан — всё, что сервер умеет.
`stt` (распознавание на сервере) — только когда есть ключ Yandex Cloud.
Выключенная чат-фича получает «не понял», а фронт прячет её кнопки и шаги обучения.
"""
import os

READY = [f"f{n}" for n in range(26)]  # Ф0…Ф25: сервер умеет всё, Ф25 — занятость


def stt_ready() -> bool:
    return bool(os.getenv("YANDEX_API_KEY") and os.getenv("YANDEX_FOLDER_ID"))


def enabled() -> list[str]:
    ready = READY + (["stt"] if stt_ready() else [])
    raw = os.getenv("STEVENS_FEATURES", "").strip()
    if not raw:
        return ready
    wanted = {f.strip().lower() for f in raw.split(",") if f.strip()}
    return [f for f in ready if f in wanted]


def on(key: str) -> bool:
    return key in enabled()


# Намерение чата → фича: выключили фичу — фраза получает not_understood
BY_INTENT = {
    "create": "f5", "list": "f7", "undated": "f7", "search": "f8", "delete": "f12", "complete": "f12", "edit": "f12",
    "repeats": "f15", "free": "f16", "move": "f17", "greeting": "f18", "bye": "f18", "person": "f19",
    "note": "f24", "notes": "f24",
}
