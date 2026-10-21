"""Разбор фраз без базы: python tests/test_nlu_phrases.py"""
import os, sys
from datetime import datetime
sys.path.insert(0, os.getcwd())
from app.nlu import parse

NOW = datetime(2026, 10, 21, 12)
CASES = [
    # фраза, тип, название
    ("напомни что завтра нужно встретиться с иваном в 15:00", "meeting", "Встреча с иваном"),
    ("Напомни мне, пожалуйста, что завтра нужно встретиться с Иваном в 15:00", "meeting", "Встреча с Иваном"),
    ("Запиши чтобы я проверил наушники", "task", "Проверить наушники"),
    ("напомни чтобы я купил хлеб", "reminder", "Купить хлеб"),
    ("запиши что я должен позвонить маме в пятницу", "task", "Позвонить маме"),
    ("напомни созвониться с машей в пятницу в 11", "meeting", "Созвон с машей"),
    ("запиши что мы увидимся с Петей в субботу в 12", "meeting", "Встреча с Петей"),
    ("напомни встретить маму на вокзале завтра в 9", "reminder", "Встретить маму на вокзале"),
    ("напомни позвонить Ивану завтра в 10", "reminder", "Позвонить Ивану"),
]

fails = 0
for phrase, kind, text in CASES:
    p = parse(phrase, NOW)
    if (p.type, p.text) != (kind, text):
        fails += 1
        print(f"«{phrase}» → {p.type} {p.text!r}, ждали {kind} {text!r}")
print("fails:", fails)
sys.exit(1 if fails else 0)
