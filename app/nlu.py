"""Разбор фразы без LLM: правила для русского языка.

Работает всегда и без ключей Yandex. Если правила не поняли фразу, а ключи
заданы, чат спрашивает LLM (brain.py) — см. app/routers/chat.py.
Даты считаются от «сегодня» пользователя (client_time), а не от часов сервера.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from datetime import date, datetime, timedelta

from app.textfmt import MONTHS_GEN, MONTHS_PREP, MONTHS_SHORT, capitalize, date_speech, date_text, week_bounds

I = re.IGNORECASE
E = "[её]"  # «ё» и «е» пишут вперемешку

WEEKDAYS = {
    "понедельник": 0, "вторник": 1, "сред": 2, "четверг": 3, "пятниц": 4, "суббот": 5, "воскресень": 6,
}
WEEKDAY_RE = r"(понедельник\w?|вторник\w?|сред[ауеы]|четверг\w?|пятниц[ауеы]|суббот[ауеы]|воскресень[еяю])"
MONTH_RE = r"(январ[яь]|феврал[яь]|март[а]?|апрел[яь]|ма[яй]|июн[яь]|июл[яь]|август[а]?|сентябр[яь]|октябр[яь]|ноябр[яь]|декабр[яь])"
MONTH_PREP_RE = r"(январе|феврале|марте|апреле|мае|июне|июле|августе|сентябре|октябре|ноябре|декабре)"

MEETING_NOUNS = {
    "встречу": "Встреча", "встреча": "Встреча", "встречи": "Встреча",
    "созвон": "Созвон", "планерку": "Планёрка", "планёрку": "Планёрка", "планерка": "Планёрка",
    "планёрка": "Планёрка", "совещание": "Совещание", "демо": "Демо", "звонок": "Звонок",
    # «напомни о встрече с Машей» → «Встреча с Машей»
    "встрече": "Встреча", "созвоне": "Созвон", "планерке": "Планёрка", "планёрке": "Планёрка",
    "совещании": "Совещание",
}
MEETING_WORD_RE = rf"(встреч|созвон|план{E}рк|совещани|демо\b|\bвстрет(?:иться|имся)\b|\bувид(?:еться|имся)\b|\bпересеч(?:ься|{E}мся)\b)"
# «встретиться с Иваном» → «Встреча с Иваном»
MEETING_VERBS = {
    "встретиться": "Встреча", "встретимся": "Встреча", "увидеться": "Встреча", "увидимся": "Встреча",
    "пересечься": "Встреча", "пересечёмся": "Встреча", "пересечемся": "Встреча",
    "созвониться": "Созвон", "созвонимся": "Созвон", "встречаемся": "Встреча", "встречаться": "Встреча",
    "созваниваемся": "Созвон",
}

STOPWORDS = {
    "с", "со", "по", "про", "о", "об", "на", "в", "во", "к", "ко", "для", "и", "у", "меня", "мне", "мою", "мой",
    "моя", "мои", "мои", "все", "всё", "эту", "этот", "это", "её", "ее", "его", "их", "за", "из", "от", "до",
    "задачу", "задача", "задачи", "задач", "встречу", "встреча", "встречи", "встреч", "напоминание",
    "напоминания", "напоминаний", "запись", "записи", "дело", "дела", "когда", "найди", "найти", "покажи",
    "был", "была", "было", "были", "будет", "есть", "ли", "что", "где", "какую", "какой", "какие", "я",
    "ставил", "ставила", "когда-то", "выполненной", "выполненным", "выполнено", "отметь", "удали", "удалить",
    "сделал", "сделала", "сделано", "который", "которую", "мы", "нам", "нас", "а", "же", "уже", "там",
    "сегодня", "завтра", "вчера", "послезавтра",
    "последнюю", "последний", "последнее", "последняя", "предыдущую", "эта", "перенеси", "перенести", "передвинь",
    "сдвинь", "подвинь", "поменяй", "измени", "время", "дату", "позже", "раньше", "выполненной", "готово",
    "убери", "убрать", "сотри", "стереть", "очисти", "удалите", "всех", "дни", "дней", "день", "дел", "время",
    "записей", "ней", "нему", "неё", "него", "этой", "этому", "из", "переименуй", "назови", "сделай",
    "заметку", "заметка", "заметки", "заметок", "заметках", "идею", "идея", "идеи", "идей", "мысль", "мысли",
}


@dataclass
class Period:
    start: date
    end: date
    label: str          # {period} в text: «на сегодня», «на этой неделе»…
    label_speech: str   # то же для speech


@dataclass
class Parse:
    intent: str = "unknown"  # create note notes list search person help thanks off_topic delete complete free move edit unknown
    type: str | None = None
    text: str | None = None
    date: date | None = None
    date_kind: str = "exact"
    time: str | None = None
    time_ambiguous: bool = False
    period: Period | None = None
    filter_type: str | None = None
    keywords: list[str] = field(default_factory=list)
    required: list[str] = field(default_factory=list)  # search: без этих ключей запись не подходит
    ref: bool = False           # «её», «это», «последнюю» — запись из контекста диалога
    shift: int = 0              # move: «на час позже» → +60 минут
    ask: str | None = None      # when | exists
    tense: str | None = None    # past | future
    vague_period: bool = False  # «на следующей неделе» без дня у создания
    has_subject: bool = True    # у встречи есть участник или тема
    thanks: bool = False        # в сообщении есть благодарность
    duration: int = 60          # free: сколько минут нужно
    window: int = 30            # free: окна короче этого не показываем
    free_mode: str = "windows"
    weekends: bool = False      # free day: выходные тоже сравниваем  # free: windows (все окна) | longest (самые большие) | day | week (самый свободный)
    subject: str | None = None  # free: «Встреча с Ваней» для кнопок со временем
    person: str | None = None   # person: «Иваном» — с кем досье, в творительном падеже
    bulk: bool = False          # delete: «удали все дела на завтра» — много записей, с подтверждением
    new_text: str | None = None  # edit: «переименуй её в созвон с Иваном»
    new_type: str | None = None  # edit: «сделай её напоминанием»
    note: str | None = None      # edit: «добавь к встрече с Машей заметку взять ноутбук»
    all: bool = False           # move: «перенеси все встречи» — все записи дня source
    source: date | None = None  # move all: с какого дня переносим (по умолчанию сегодня)


# ── Время ─────────────────────────────────────────────

TIME_HM = re.compile(r"(?:\bв\s+|\bна\s+|^|\s)(\d{1,2})[:.](\d{2})\b", I)
TIME_H = re.compile(r"\bв\s+(\d{1,2})(?:\s*(?:час(?:а|ов)?|ч\.?))?(?:\s+(утра|дня|вечера|ночи))?(?=\s|$|[,.!?])", I)
TIME_WORDS = re.compile(r"\b(утром|вечером|днём|днем)\b", I)


def _parse_time(text: str) -> tuple[str | None, bool, list[tuple[int, int]]]:
    """Время HH:MM, признак «утро или вечер — неясно» и куски фразы, где оно было."""
    spans: list[tuple[int, int]] = []
    m = TIME_HM.search(text)
    if m:
        h, mm = int(m.group(1)), int(m.group(2))
        if h < 24 and mm < 60:
            spans.append(m.span())
            ampm = re.search(r"\s+(утра|дня|вечера|ночи)\b", text[m.end():m.end() + 8], I)
            word = TIME_WORDS.search(text)
            ambiguous = False
            if ampm and ampm.group(1).lower() in ("вечера", "дня") and h < 12:
                h += 12
            elif not ampm and not word and len(m.group(1)) == 1 and 1 <= h <= 7:
                # «в 2:00» — как «в 2»: ставим день и переспрашиваем (Р-19). «в 02:00» — точно ночь
                h += 12
                ambiguous = True
            return f"{h:02d}:{mm:02d}", ambiguous, spans
    m = TIME_H.search(text)
    if m:
        h = int(m.group(1))
        part = (m.group(2) or "").lower()
        if h > 23:
            return None, False, spans
        spans.append(m.span())
        ambiguous = False
        if part in ("вечера", "дня") and h < 12:
            h += 12
        elif part == "ночи" and h == 12:
            h = 0
        elif not part:
            word = TIME_WORDS.search(text)
            if word and word.group(1).lower() == "вечером" and h < 12:
                h += 12
            elif word and word.group(1).lower() in ("днём", "днем") and h < 8:
                h += 12
            elif not word and 1 <= h <= 7:
                # «в 7» — утро или вечер? Ставим вечер и переспрашиваем (Р-19)
                h += 12
                ambiguous = True
        return f"{h:02d}:00", ambiguous, spans
    return None, False, spans


# «через 11 минут», «через полчаса», «через час и 20 минут», «через пару дней», «через неделю»
_UNITS = {"ноль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
          "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
          "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
          "восемнадцать": 18, "девятнадцать": 19, "пару": 2, "пара": 2}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}
_NUM = rf"(?:\d+(?:[.,]5)?|(?:{'|'.join(_TENS)})(?:\s+(?:{'|'.join(_UNITS)}))?|{'|'.join(_UNITS)})"
_UNIT = r"(?:полчаса|полтора\s+часа|полминуты|минут[уы]?|мин\.?|час(?:а|ов)?|д(?:ень|ня|ней)|сутки|суток|недел[юи]|недель|месяц(?:а|ев)?)"
REL_RE = re.compile(
    rf"\bчерез\s+(?:({_NUM})\s+)?({_UNIT})(?:\s+(?:и\s+)?({_NUM})\s+(минут[уы]?|мин\.?))?(?=\s|$|[,.!?])",
    I,
)


def _number(raw: str | None) -> float:
    if not raw:
        return 1
    raw = raw.lower().replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        words = raw.split()
        return sum(_TENS.get(w, 0) + _UNITS.get(w, 0) for w in words) or 1


def _relative(text: str, now: datetime) -> tuple[date | None, str | None, tuple[int, int] | None]:
    m = REL_RE.search(text)
    if not m:
        return None, None, None
    n, unit = _number(m.group(1)), m.group(2).lower()
    extra = timedelta(minutes=_number(m.group(3))) if m.group(3) else timedelta()
    if unit == "полчаса":
        delta = timedelta(minutes=30)
    elif unit.startswith("полтора"):
        delta = timedelta(minutes=90)
    elif unit == "полминуты":
        delta = timedelta(minutes=1)
    elif unit.startswith("мин"):
        delta = timedelta(minutes=n)
    elif unit.startswith("час"):
        delta = timedelta(minutes=round(n * 60))
    elif unit.startswith("д") or unit.startswith("сут"):
        return (now + timedelta(days=int(n))).date(), None, m.span()
    elif unit.startswith("недел"):
        return (now + timedelta(weeks=int(n))).date(), None, m.span()
    else:
        month = now.month - 1 + int(n)
        year, month = now.year + month // 12, month % 12 + 1
        day = min(now.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return date(year, month, day), None, m.span()
    moment = now + delta + extra
    return moment.date(), moment.strftime("%H:%M"), m.span()


def parse_when(text: str, now: datetime) -> tuple[str | None, bool, list[tuple[int, int]], "_DateInfo"]:
    """Время и дата фразы, с относительными «через 20 минут» / «через 2 дня»."""
    rel_date, rel_time, rel_span = _relative(text, now)
    if rel_span:
        rest = text[:rel_span[0]] + " " * (rel_span[1] - rel_span[0]) + text[rel_span[1]:]
        time, ambiguous, time_spans = _parse_time(rest)
        info = _parse_date(rest, now, time)
        if rel_time:
            time, ambiguous = rel_time, False
        # «Во вторник через неделю», «через 2 недели в пятницу» — ближайший вторник плюс недели
        weeks = re.search(r"недел", text[rel_span[0]:rel_span[1]], I)
        day_word = re.search(rf"(?:\b(?:в|во|на)\s+)?\b{WEEKDAY_RE}\b", rest, I) if weeks else None
        if day_word:
            today = now.date()
            n = (rel_date - today).days // 7
            target = _weekday_index(day_word.group(1))
            info.date = today + timedelta(days=(target - today.weekday()) % 7 + 7 * n)
            info.spans.append(day_word.span())
        info.date = info.date or rel_date
        time_spans.append(rel_span)
        return time, ambiguous, time_spans, info
    time, ambiguous, time_spans = _parse_time(text)
    return time, ambiguous, time_spans, _parse_date(text, now, time)


# ── Дата ──────────────────────────────────────────────

def _weekday_index(word: str) -> int:
    word = word.lower()
    for stem, index in WEEKDAYS.items():
        if word.startswith(stem):
            return index
    return 0


def _month_index(word: str) -> int:
    word = word.lower()
    for i, name in enumerate(MONTHS_GEN):
        if word[:3] == name[:3]:
            return i
    return 0


@dataclass
class _DateInfo:
    date: date | None = None
    kind: str = "exact"
    period: Period | None = None
    spans: list[tuple[int, int]] = field(default_factory=list)


def _day_period(day: date, today: date, label: str | None = None, speech: str | None = None) -> Period:
    diff = (day - today).days
    if label is None:
        if diff == 0:
            label, speech = "на сегодня", "на сегодня"
        elif diff == 1:
            label, speech = "завтра", "завтра"
        elif diff == -1:
            label, speech = "вчера", "вчера"
        else:
            label, speech = f"на {date_text(day, today)}", f"на {date_speech(day, today)}"
    return Period(day, day, label, speech or label)


def _range_label(start: date, end: date) -> tuple[str, str]:
    if start.month == end.month:
        return (f"с {start.day} по {end.day} {MONTHS_SHORT[end.month - 1]}",
                f"с {start.day} по {end.day} {MONTHS_GEN[end.month - 1]}")
    return (f"с {start.day} {MONTHS_SHORT[start.month - 1]} по {end.day} {MONTHS_SHORT[end.month - 1]}",
            f"с {start.day} {MONTHS_GEN[start.month - 1]} по {end.day} {MONTHS_GEN[end.month - 1]}")


def _parse_date(text: str, now: datetime, time: str | None) -> _DateInfo:
    today = now.date()
    info = _DateInfo()

    def found(m: re.Match, day: date | None = None, kind: str = "exact", period: Period | None = None):
        info.spans.append(m.span())
        if day is not None:
            info.date, info.kind = day, kind
        if period is not None:
            info.period = period

    # Сроки: «до конца недели», «до пятницы», «к пятнице», «до 25 октября»
    m = re.search(r"\b(?:до|к)\s+конц[ау]\s+недели\b", text, I)
    if m:
        found(m, week_bounds(today)[1], "deadline")
    m = m or re.search(rf"\b(?:до|к)\s+{WEEKDAY_RE}\b", text, I)
    if m and not info.date:
        target = _weekday_index(m.group(1))
        found(m, today + timedelta(days=(target - today.weekday()) % 7 or 7), "deadline")
    m2 = re.search(rf"\bдо\s+(\d{{1,2}})\s+{MONTH_RE}\b", text, I)
    if m2 and not info.date:
        try:
            found(m2, date(today.year, _month_index(m2.group(2)) + 1, int(m2.group(1))), "deadline")
        except ValueError:
            pass

    # Периоды: неделя, месяц
    m = re.search(r"\b(?:на|в)\s+(?:эт(?:ой|у)|текущ(?:ей|ую))\s+недел[еюи]\b", text, I)
    if m:
        s, e = week_bounds(today)
        found(m, period=Period(s, e, "на этой неделе", "на этой неделе"))
    m = re.search(r"\b(?:на|в)\s+следующ(?:ей|ую)\s+недел[еюи]\b", text, I)
    if m:
        s, e = week_bounds(today + timedelta(days=7))
        found(m, period=Period(s, e, "на следующей неделе", "на следующей неделе"))
    m = re.search(r"\b(?:на|в)\s+прошл(?:ой|ую)\s+недел[еюи]\b", text, I)
    if m:
        s, e = week_bounds(today - timedelta(days=7))
        found(m, period=Period(s, e, "на прошлой неделе", "на прошлой неделе"))
    m = re.search(rf"\bв\s+{MONTH_PREP_RE}\b", text, I)
    if m:
        index = MONTHS_PREP.index(m.group(1).lower())
        year = today.year + (1 if index + 1 < today.month - 6 else 0)
        start = date(year, index + 1, 1)
        end = (date(year + (index == 11), (index + 1) % 12 + 1, 1) - timedelta(days=1))
        found(m, period=Period(start, end, f"в {MONTHS_PREP[index]}", f"в {MONTHS_PREP[index]}"))
    m = re.search(r"\bв\s+(?:этом|текущем)\s+месяце\b", text, I)
    if m:
        start = today.replace(day=1)
        end = (start.replace(month=start.month % 12 + 1, year=start.year + (start.month == 12)) - timedelta(days=1))
        found(m, period=Period(start, end, "в этом месяце", "в этом месяце"))

    # Конкретный день
    if not info.date:
        m = re.search(r"\b(?:на\s+)?(позавчера|вчера|сегодня|послезавтра|завтра)\b", text, I)
        if m:
            shift = {"позавчера": -2, "вчера": -1, "сегодня": 0, "завтра": 1, "послезавтра": 2}[m.group(1).lower()]
            found(m, today + timedelta(days=shift))
    if not info.date:
        m = re.search(rf"\b(?:в|во|на)\s+(прошл(?:ый|ую|ое)|следующ(?:ий|ую|ее)|эт(?:от|у|о))?\s*{WEEKDAY_RE}\b", text, I)
        if m:
            target = _weekday_index(m.group(2))
            which = (m.group(1) or "").lower()
            if which.startswith("прошл"):
                delta = (today.weekday() - target) % 7 or 7
                day = today - timedelta(days=delta)
            elif which.startswith("следующ"):
                day = week_bounds(today + timedelta(days=7))[0] + timedelta(days=target)
            else:
                day = today + timedelta(days=(target - today.weekday()) % 7)
                # Сегодняшний день недели, но время уже прошло — значит, через неделю
                if day == today and time and time <= now.strftime("%H:%M"):
                    day += timedelta(days=7)
            found(m, day)
    if not info.date:
        m = re.search(rf"\b(?:на\s+)?(\d{{1,2}})\s+{MONTH_RE}(?:\s+(\d{{4}}))?\b", text, I)
        if m:
            try:
                year = int(m.group(3)) if m.group(3) else today.year
                found(m, date(year, _month_index(m.group(2)) + 1, int(m.group(1))))
            except ValueError:
                pass
    if not info.date:
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b(?!:)", text)
        if m and not re.search(r"\d[:.]\d{2}\b", m.group(0)):
            try:
                if m.group(1):
                    found(m, date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                else:
                    found(m, date(int(m.group(6) or today.year), int(m.group(5)), int(m.group(4))))
            except ValueError:
                pass
    return info


# ── Ключевые слова для поиска ─────────────────────────

def stem(word: str) -> str:
    """Грубая основа слова: «Иваном» и «Иван» → «ива», «договору» → «догов»."""
    word = word.lower().replace("ё", "е")
    return word if len(word) <= 3 else word[: max(3, len(word) - 2)]


# Слова с одним смыслом ищем вместе: «звонил Ивану» найдёт «Позвонить Ивану» и «Созвон с Иваном»
ROOTS = {
    "звонить": "звон", "позвонить": "звон", "перезвонить": "звон", "созвониться": "звон", "созвон": "звон",
    "звонок": "звон", "встреча": "встреч", "встретиться": "встреч", "встречаться": "встреч",
    "увидеться": "встреч", "видеться": "встреч",
}
# «Ваня» и «Иван» — один человек
NICKNAMES = {
    "ваня": "иван", "петя": "петр", "маша": "мария", "леша": "алексей", "алеша": "алексей", "саша": "александр",
    "дима": "дмитрий", "сережа": "сергей", "лена": "елена", "катя": "екатерина", "настя": "анастасия",
    "миша": "михаил", "коля": "николай", "таня": "татьяна", "оля": "ольга", "женя": "евгений", "юля": "юлия",
    "аня": "анна", "наташа": "наталья", "андрюша": "андрей", "вова": "владимир", "паша": "павел",
}
SEARCH_FILLERS = {"последний", "раз", "что-нибудь", "что-то", "нибудь", "вообще", "всё", "все", "весь", "дело", "раньше"}

try:
    import pymorphy3
    _MORPH = pymorphy3.MorphAnalyzer()
except Exception:  # noqa: BLE001 — без pymorphy3 поиск работает по основам слов
    _MORPH = None


@lru_cache(maxsize=20000)
def word_key(word: str) -> str:
    """Ключ слова для поиска: «@иван» — имя (с учётом «Ваня»), «~звон» — смысловая группа, иначе основа."""
    if _MORPH is not None:
        forms = _MORPH.parse(word)
        first = forms[0]
        lemma = first.normal_form.replace("ё", "е")
        if "Name" in first.tag or (word[:1].isupper() and any("Name" in f.tag for f in forms[:3])):
            name = next(f for f in forms if "Name" in f.tag).normal_form.replace("ё", "е")
            return "@" + NICKNAMES.get(name, name)
        if lemma in ROOTS:
            return "~" + ROOTS[lemma]
    return stem(word)


def _lemma(word: str) -> str:
    return _MORPH.parse(word)[0].normal_form if _MORPH is not None else word.lower()


def keywords(text: str) -> list[str]:
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]+", text)
    return [word_key(w) for w in words if w.lower().replace("ё", "е") not in STOPWORDS and len(w) >= 2 and not w.isdigit()]


def search_keywords(text: str, filter_type: str | None = None) -> tuple[list[str], list[str]]:
    """Ключи для поиска и обязательные из них: имена и названия с большой буквы («Петром», «Вектор»)."""
    words = re.findall(r"[A-Za-zА-Яа-яЁё0-9-]+", text)
    keys, required = [], []
    for i, w in enumerate(words):
        if w.lower().replace("ё", "е") in STOPWORDS or len(w) < 2 or w.isdigit() or _lemma(w) in SEARCH_FILLERS:
            continue
        key = word_key(w)
        if filter_type == "meeting" and key in ("~звон", "~встреч"):
            continue  # «найди созвон с Петром» — любая встреча с Петром, не только со словом «созвон»
        keys.append(key)
        if key.startswith("@") or (i > 0 and w[:1].isupper()):
            required.append(key)
    return keys, required


def record_has(words: list[str], key: str) -> bool:
    """Есть ли в словах записи ключ поиска."""
    if key[:1] in "@~":
        return any(word_key(w) == key for w in words)
    return any(w.lower().replace("ё", "е").startswith(key) for w in words)


def past_tense(text: str) -> bool:
    if re.search(r"\b(был|была|было|были|последний\s+раз|в\s+прошлый\s+раз|раньше)\b", text, I):
        return True
    if _MORPH is None:
        return False
    return any("VERB" in f.tag and "past" in f.tag
               for f in (_MORPH.parse(w)[0] for w in re.findall(r"[А-Яа-яЁё]+", text)))


def _filter_type(text: str) -> str | None:
    lower = text.lower()
    if re.search(MEETING_WORD_RE, lower):
        return "meeting"
    if re.search(r"напоминани", lower):
        return "reminder"
    if re.search(r"\bзадач", lower):
        return "task"
    if NOTE_WORD_RE.search(lower):
        return "note"
    return None


def _cut(text: str, spans: list[tuple[int, int]]) -> str:
    for start, end in sorted(spans, reverse=True):
        text = text[:start] + " " + text[end:]
    return text


# ── Досье на человека: «что у меня с Иваном?» ────────

PERSON_RE = re.compile(
    r"(?:^|\b)(?:"
    r"что\s+(?:у\s+(?:меня|нас)\s+)?(?:было\s+|будет\s+|есть\s+|связано\s+)?(?:с|со|про|по)"
    r"|досье\s+(?:на|по|про)"
    r"|(?:вс[её]|все)\s+(?:что\s+(?:есть|связано)\s+)?(?:про|о|об|по|с|со)"
    r"|какие\s+(?:у\s+меня\s+)?(?:дела|планы|встречи|задачи)\s+(?:с|со)"
    r"|(?:мои\s+)?дела\s+(?:с|со)"
    r"|расскажи\s+(?:про|о|об)"
    r")\s+",
    I,
)


def _person_word(word: str, first: bool) -> bool:
    """Имя («Иваном», «Машей») или название с большой буквы («Пульсом»)."""
    low = word.lower().replace("ё", "е")
    if low in STOPWORDS or len(word) < 3 or re.fullmatch(WEEKDAY_RE + r"|" + MONTH_RE, low):
        return False
    return word_key(word).startswith("@") or (word[:1].isupper() and not first)


def _instrumental(word: str) -> str:
    """«Ивана» → «Иваном», «Маша» → «Машей»; не имя — как сказали."""
    if _MORPH is not None:
        for form in _MORPH.parse(word)[:3]:
            if "Name" in form.tag:
                found = form.inflect({"ablt", "sing"})
                if found:
                    return capitalize(found.word)
    return capitalize(word)


def person_request(text: str) -> tuple[list[str], str] | None:
    """«Что у меня с Иваном и Машей?» → (ключи имён, «Иваном и Машей»)."""
    m = PERSON_RE.search(text)
    if not m:
        return None
    words = re.findall(r"[A-Za-zА-Яа-яЁё-]+", text[m.end():])
    keys, names = [], []
    for w in words:
        if names and w.lower() in ("и", "или"):
            continue
        if not _person_word(w, first=m.end() == 0):
            break
        keys.append(word_key(w))
        names.append(_instrumental(w))
    if not keys:
        return None
    return keys, " и ".join(names)


# ── Главная функция ───────────────────────────────────

THANKS_RE = re.compile(r"\b(спасибо|благодарю|благодарствую|спс)\b[,!.\s]*(стивенс|stevens)?[,!.]*", I)
HELP_RE = re.compile(r"(что\s+ты\s+умеешь|что\s+умеешь|чем\s+(?:ты\s+)?(?:можешь\s+)?помочь|^помощь|^справка|как\s+(?:тобой|этим)\s+пользоваться)", I)
DELETE_RE = re.compile(r"^(?:пожалуйста\s+)?(удали(?:те)?|удалить|убери(?:те)?|убрать|сотри|стереть|очисти(?:ть)?)\b", I)
# «Удали все дела на завтра», «убери всё за все дни», «очисти пятницу»
BULK_RE = re.compile(r"(\bвс[её]\b|\bвсех\b|^(?:пожалуйста\s+)?очисти)", I)
ALL_DAYS_RE = re.compile(r"\b(?:за\s+)?(?:все\s+дни|вс[её]\s+время|всю\s+историю)\b", I)
COMPLETE_RE = re.compile(rf"(\bотметь\b.*\bвыполнен|\bя\s+(?:уже\s+)?(?:сделал|сделала|выполнил|выполнила|закончил|закончила)\b|^(?:сделал|сделала|выполнил|выполнила)\b|\bотметь\b.*\bсделан)", I)
SEARCH_RE = re.compile(rf"(\bнайди\b|\bнайти\b|\bпоищи\b|\bищи\b|^когда\b(?!-)|\bкогда\s+(?:у\s+меня\s+)?(?:был|была|было|будет)\b|\bставил)", I)
LIST_RE = re.compile(rf"(что\s+у\s+меня|какие\s+(?:у\s+меня\s+)?(?:планы|дела|задачи|встречи|напоминания)|^покажи|что\s+(?:я\s+)?запланировано|есть\s+ли\s+у\s+меня|что\s+(?:было|будет)|мо[её]\s+расписание|\bпланы\s+на\b|чем\s+я\s+занят)", I)
OFFTOPIC_RE = re.compile(r"(погод|анекдот|шутк|курс\s+(?:доллар|евро|валют)|новост|рецепт|кто\s+так|переведи|столиц|сколько\s+будет|расскажи\s+(?:о|про)\s+себе|как\s+дела|фильм|музык|игр[ау]|стих)", I)
CREATE_VERB_RE = re.compile(r"^(?:пожалуйста\s+)?(напомни(?:те)?(?:\s+мне)?|поставь|запиши|добавь|создай|запланируй|назначь|внеси|заведи)\b", I)
DONE_RE = re.compile(r"^(?:готово|сделано|выполнено|сделал|сделала)[\s.!]*$", I)
REF_RE = re.compile(rf"\b(её|ее|его|это|эту|этот|эта|ней|нему|неё|него|этой|этому|последн\w*|предыдущ\w*)\b", I)
MOVE_RE = re.compile(r"^(?:перенеси(?:те)?|перенести|передвинь|сдвинь|подвинь|поменяй|измени)\b", I)
# Правка записи: название, тип, заметка
RENAME_RE = re.compile(r"^(?:переименуй(?:те)?|переименовать|переназови|назови|(?:поменяй|измени|смени)\s+название)\b\s*(?P<rest>.*)$", I)
RETYPE_RE = re.compile(
    r"^(?:сделай|сделать|преврати|переделай|поменяй|измени|смени)\s+(?P<target>.+?)\s+(?:тип\s+)?(?:(?:в|на)\s+)?"
    r"(?P<type>задач(?:ей|у|а)|напоминани(?:ем|е)|встреч(?:ей|у|а)|заметк(?:ой|у|а))[\s.!?]*$", I)
# «Это заметка», «перенеси её в заметки» — последняя запись становится заметкой
TO_NOTE_RE = re.compile(
    r"^(?:(?:нет|не)[\s,]+)?(?:это\s+(?:была\s+|же\s+)?(?:заметка|идея|мысль)"
    r"|(?:перенеси|убери|сохрани|положи|отправь|запиши|переложи|кинь)\s+(?P<target>.+?)\s+в\s+заметки)[\s.!?]*$", I)
NOTE_WORDS = r"(?:заметку|примечание|комментарий|пометку|описание)"
NOTE_RE = re.compile(
    rf"^(?:добавь|допиши|припиши|добавить|дописать)\s+(?:(?:к|ко|в|во)\s+(?P<target>.+?)\s+{NOTE_WORDS}[\s:,—-]+(?P<note>.+)"
    rf"|{NOTE_WORDS}\s+(?:(?:к|ко|в|во)\s+(?P<ref>ней|нему|не[её]|нему|этой\s+\w+|этому\s+\w+)[\s:,—-]+|(?:(?:к|ко|в|во)\s+(?P<target2>[^:—]+?))?\s*[:—]\s*)(?P<note2>.+))$",
    I)
# Заметка без времени: «запиши идею: …», «заметка: …», «сохрани в заметки …»
NOTE_WORD_RE = re.compile(r"\b(?:заметк\w*|иде(?:я|ю|и|ей|ям|ями|ях)|мысл(?:ь|и))\b", I)
NOTE_NEW_RE = re.compile(
    r"^(?:(?:пожалуйста\s+)?(?:запиши|сохрани|создай|сделай|добавь|заведи|запомни|внеси|оставь|набросай)(?:те)?"
    r"(?:\s+(?:мне|себе))?\s+)?(?:(?:в\s+заметк(?:и|у))"
    r"|(?:(?:новую|новая|одну|такую|быструю|быстрая)\s+)?(?:идею|идея|мысль|заметку|заметка))(?!\s+(?:к|ко)\s)"
    r"(?:(?:\s*[:,—-]\s*|\s+)(?:(?:что|о\s+том,?\s+что|про)\s+)?(?P<body>\S.*))?[\s.!?:]*$",
    I)
# «Покажи заметки», «мои идеи», «что у меня в заметках», «заметки про кофе»
NOTES_LIST_RE = re.compile(
    r"^(?:(?:покажи|открой|выведи|прочитай|прочти|зачитай|перечисли|какие|список|мои|все)\b.*?\b"
    r"|что\s+(?:у\s+меня\s+)?(?:есть\s+)?в\s+|)"
    r"(?:заметки|заметках|заметок|идеи|идей|мысли)\b(?P<rest>.*)$",
    I)
TYPE_BY_STEM = {"задач": "task", "напомин": "reminder", "встреч": "meeting", "заметк": "note"}
SHIFT_RE = re.compile(r"\bна\s+(полчаса|(\d+)?\s*час\w*|(\d+)\s*минут\w*)\s+(позже|раньше)\b", I)
ALL_RE = re.compile(r"\b(?:вс[её]|всех)\b", I)
SOURCE_RE = re.compile(
    r"\b(?:с|со)\s+(сегодня|завтра|послезавтра|понедельника|вторника|среды|четверга|пятницы|субботы|воскресенья)\b"
    r"|\b(сегодняшн\w*|завтрашн\w*)\b",
    I,
)
TIME_ON_RE = re.compile(r"\bна\s+(\d{1,2})\b(?![:.]\d|\s*(?:час|минут|числ|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр))", I)
REMIND_RE = re.compile(r"(^напомни|\bнапомн(?:и|ите|ить|ил|ила|им)\b|\bнапоминани)", I)


# Вводные слова и просьбы в начале фразы: «Слушай, а можешь напомнить, что…» → «напомни …»
LEAD_RE = re.compile(
    r"^(?:(?:слушай(?:те)?|послушай|слышь|стивенс|стивен|стив|stevens|steve|ну|а|так|эй|окей|ок|короче|пожалуйста"
    r"|давай(?:те)?|кстати|вот|в\s+общем|типа|значит|смотри)\b[\s,!.]*)+",
    I,
)
# «Хочу, чтобы ты напомнил…», «давай запишем…» → «напомни…», «запиши…»
WANT_RE = re.compile(r"^(?:я\s+)?(?:хочу|хотел[аи]?\s+бы|хотелось\s+бы)[\s,]*(?:чтобы|чтоб)[\s,]*(?:ты\s+)?", I)
WE_VERBS = (
    (r"напомн(?:ил|ила|им)", "напомни"), (r"запис(?:ал|ала)|запишем", "запиши"), (r"постав(?:ил|ила)|поставим", "поставь"),
    (r"добав(?:ил|ила)|добавим", "добавь"), (r"запланир(?:овал|овала|уем)", "запланируй"), (r"назнач(?:ил|ила|им)", "назначь"),
)
ASK_RE = re.compile(r"^(?:(?:ты|вы)\s+)?(?:не\s+)?(?:можешь|можете|мог\s+бы|могла\s+бы|могли\s+бы|сможешь)(?:\s+(?:ли|ты|вы|мне|пожалуйста))*\b[\s,]*", I)
ASK_VERBS = (
    (rf"(?:сделать|создать|поставить|завести|добавить)\s+(?:мне\s+)?напоминани[ея]", "напомни"),
    (r"напомнить", "напомни"), (r"поставить", "поставь"), (r"записать", "запиши"), (r"добавить", "добавь"),
    (r"(?:создать|сделать)", "создай"), (r"запланировать", "запланируй"), (r"назначить", "назначь"),
    (r"найти", "найди"), (r"показать", "покажи"), (r"удалить", "удали"), (r"отметить", "отметь"),
)


def strip_lead(text: str) -> str:
    rest = LEAD_RE.sub("", text)
    wanted = WANT_RE.match(rest)
    if wanted:
        rest = rest[wanted.end():]
    for verb, imperative in WE_VERBS:
        m = re.match(rf"(?:{verb})\b", rest, I)
        if m:
            rest = imperative + rest[m.end():]
            break
    asked = ASK_RE.match(rest)
    if asked:
        rest = rest[asked.end():]
        for verb, imperative in ASK_VERBS:
            m = re.match(rf"{verb}\b", rest, I)
            if m:
                rest = imperative + rest[m.end():]
                break
    rest = re.sub(r"^напомни(\s+мне)?(?:[\s,]+пожалуйста)?[\s,]+(?:о\s+том|про\s+то)?[\s,]*(?:что|чтобы)\b[\s,]*", r"напомни\1 ", rest, flags=I)
    rest = re.sub(r"^напомни(\s+мне)?\s+(?:о\s+том|про\s+то)\b[\s,]*", r"напомни\1 ", rest, flags=I)
    rest = rest.strip(" ,")
    return rest or text


FREE_RE = re.compile(
    r"(когда\s+(?:я\s+)?свобод|свободн\w*\s+(?:время|окн|час|полчас)|(?:найди|подбери|поищи)\s+(?:мне\s+)?"
    r"(?:время|окно|свободн|час|полчаса)|есть\s+ли\s+(?:у\s+меня\s+)?(?:свободн|окн|время)"
    r"|когда\s+(?:можно|могу|получится|лучше)\s+(?:встретиться|созвониться|поставить|назначить)"
    r"|сам\w+\s+(?:больш\w+\s+|длинн\w+\s+)?(?:свободн|окн)|свободне[ей]\s+всего|(?:больш|длинн)\w+\s+(?:свободн\w+\s+)?окн"
    r"|(?:меньше|менее)\s+всего\s+(?:дел|занят|загруж)|наименее\s+загруж)",
    I,
)
# «самый свободный день», «свободнее всего», «меньше всего дел» — сравниваем дни или недели
FREE_BEST_RE = re.compile(
    r"сам\w+\s+свободн|свободне[ей]\s+всего|(?:меньше|менее)\s+всего\s+(?:дел|занят|загруж)|наименее\s+загруж", I)
# «большое окно», «самое длинное окно», «самые свободные часы» — окна от больших к меньшим
FREE_LONG_RE = re.compile(r"(?:больш|длинн)\w+\s+(?:свободн\w+\s+)?окн|сам\w+\s+свободн\w+\s+(?:час|врем|окн)", I)
FREE_SUBJECT_RE = re.compile(
    r"\b(встретиться|встречу|встречи|созвониться|созвона|созвон)\s+(с\s+[^,.!?]+?)"
    r"(?=\s+(?:в|во|на|к|до|завтра|сегодня|послезавтра)\b|\s+\d|[,.!?]|$)",
    I,
)
NUM_WORDS = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4}


def _free_duration(lower: str) -> int | None:
    """Сколько нужно времени, если сказано: «на час», «на полчаса». None — не сказано."""
    if re.search(r"\bна\s+полчаса\b|\bполчаса\b", lower):
        return 30
    if re.search(r"\bна\s+полтора\s+часа\b", lower):
        return 90
    if re.search(r"\bна\s+(?:целый\s+)?час\b", lower):
        return 60
    m = re.search(r"\bна\s+(\d+|один|одну|два|две|три|четыре)\s+час", lower)
    if m:
        return 60 * (int(m.group(1)) if m.group(1).isdigit() else NUM_WORDS[m.group(1)])
    m = re.search(r"\bна\s+(\d+)\s+минут", lower)
    if m:
        return max(15, int(m.group(1)))
    return None


def parse(raw: str, now: datetime) -> Parse:
    text = strip_lead(" ".join(raw.strip().split()))
    result = Parse()

    # Благодарность: одна — thanks; с просьбой — выполняем просьбу (Р-124)
    thanks = THANKS_RE.search(text)
    if thanks:
        result.thanks = True
        rest = _cut(text, [thanks.span()]).strip(" ,.!")
        if not rest:
            result.intent = "thanks"
            return result
        text = rest

    lower = text.lower()
    time, ambiguous, time_spans, info = parse_when(text, now)

    if HELP_RE.search(lower):
        result.intent = "help"
        return result

    # «Переименуй её в созвон», «сделай встречу с Иваном задачей», «добавь к встрече с Машей заметку …»
    if edit := parse_edit(text, now):
        return edit

    # «Запиши идею: открыть кофейню», «Заметка: купить подарок маме» — без даты и времени
    if note := NOTE_NEW_RE.match(text):
        result.intent, result.type, result.date_kind = "note", "note", "none"
        result.text = capitalize((note["body"] or "").strip(" ,.!?:—-")) or None
        return result

    if DELETE_RE.search(lower) or COMPLETE_RE.search(lower) or DONE_RE.search(lower):
        result.intent = "delete" if DELETE_RE.search(lower) else "complete"
        result.filter_type = _filter_type(lower)
        if re.match(r"^(?:пожалуйста\s+)?очисти(?:ть)?\s+(?!на\b|в\b|во\b)", lower) and not info.date:
            # «Очисти пятницу» — как «на пятницу»
            text = re.sub(r"^((?:пожалуйста\s+)?очисти(?:ть)?)\s+", r"\1 на ", text, flags=I)
            time, ambiguous, time_spans, info = parse_when(text, now)
        spans = info.spans + time_spans
        all_days = ALL_DAYS_RE.search(text)
        if all_days:
            spans.append(all_days.span())
        rest = _cut(text, spans)
        result.keywords, result.required = search_keywords(rest, result.filter_type)
        result.ref = bool(REF_RE.search(rest)) or bool(DONE_RE.search(lower))
        result.bulk = result.intent == "delete" and bool(BULK_RE.search(lower))
        if not all_days:
            result.date = info.date
            if result.bulk and info.period and not info.date:
                result.period = info.period
        result.time = time if not ambiguous else None
        return result

    # «Перенеси её на завтра», «Сдвинь встречу с Иваном на час позже»
    if MOVE_RE.search(lower) and ALL_RE.search(lower):
        return _parse_move_all(text, now, result)
    if MOVE_RE.search(lower):
        result.intent = "move"
        result.filter_type = _filter_type(lower)
        spans = info.spans + time_spans
        shift = SHIFT_RE.search(text)
        if shift:
            spans.append(shift.span())
            amount = shift.group(1).lower()
            result.shift = 30 if amount == "полчаса" else 60 * int(shift.group(2) or 1) if "час" in amount else int(shift.group(3))
            result.shift *= -1 if shift.group(4).lower() == "раньше" else 1
        if not time and not shift:
            hour = TIME_ON_RE.search(text)
            if hour and int(hour.group(1)) < 24:
                h = int(hour.group(1))
                time = f"{h + 12 if 1 <= h <= 7 else h:02d}:00"
                spans.append(hour.span())
        result.keywords, result.required = search_keywords(_cut(text, spans), result.filter_type)
        result.ref = bool(REF_RE.search(lower)) or not result.keywords
        result.date, result.time = info.date, time
        return result

    # «Покажи заметки», «какие у меня идеи», «заметки про кофе»
    if notes := NOTES_LIST_RE.match(text):
        result.intent, result.filter_type = "notes", "note"
        result.keywords, result.required = search_keywords(notes["rest"], "note")
        return result

    # «Когда я свободен в четверг на час?», «Найди время встретиться с Ваней»
    if FREE_RE.search(lower):
        result.intent = "free"
        result.date = info.date or (info.period.start if info.period else None)
        result.period = info.period
        subject = FREE_SUBJECT_RE.search(text)
        if subject:
            noun = "Созвон" if subject.group(1).lower().startswith("созвон") else "Встреча"
            result.subject = f"{noun} {subject.group(2).strip()}"
        need = _free_duration(lower)
        result.duration = need or 60
        # Без «на час» показываем все окна от получаса, для встречи — от часа
        result.window = need or (60 if subject else 30)
        rest = _cut(lower, info.spans).lower()
        if FREE_LONG_RE.search(lower):
            result.free_mode = "longest"
        if FREE_BEST_RE.search(lower):
            if re.search(r"\b(?:день|дня|дни|дней)\b", rest):
                result.free_mode = "day"
            elif re.search(r"\bнедел", rest):
                result.free_mode = "week"
            elif info.period and not info.date:
                result.free_mode = "day"    # «Когда я свободнее всего на этой неделе?»
            else:
                result.free_mode = "longest"  # «Когда я свободнее всего завтра?»
        result.weekends = bool(re.search(r"выходн|суббот|воскрес", lower))
        return result

    # «Что у меня с Иваном?», «Досье на Петра», «Всё про Машу на этой неделе»
    person = None if CREATE_VERB_RE.search(lower) else person_request(_cut(text, info.spans + time_spans))
    if person:
        result.intent = "person"
        result.keywords, result.person = person
        result.filter_type = _filter_type(lower)
        if info.period:
            result.period = info.period
        elif info.date:
            result.period = _day_period(info.date, now.date())
        return result

    if SEARCH_RE.search(lower) and not CREATE_VERB_RE.search(lower):
        result.intent = "search"
        result.filter_type = _filter_type(lower)
        if re.search(r"\bкогда\b(?!-)", lower):
            result.ask = "when"
            result.tense = "past" if past_tense(lower) else "future"
        rest = _cut(text, info.spans + time_spans)
        result.keywords, result.required = search_keywords(rest, result.filter_type)
        if info.period:
            result.period = info.period
        elif info.date:
            result.period = _day_period(info.date, now.date())
        return result

    if LIST_RE.search(lower) and not CREATE_VERB_RE.search(lower):
        result.intent = "list"
        result.filter_type = _filter_type(lower)
        if re.search(r"есть\s+ли", lower):
            result.ask = "exists"
        today = now.date()
        if info.period:
            result.period = info.period
        elif info.date:
            result.period = _day_period(info.date, today)
        elif re.search(r"\bна\s+неделю\b|\bнеделе\b", lower):
            s, e = week_bounds(today)
            result.period = Period(s, e, "на этой неделе", "на этой неделе")
        else:
            result.period = _day_period(today, today)
        return result

    if OFFTOPIC_RE.search(lower) and not CREATE_VERB_RE.search(lower):
        result.intent = "off_topic"
        return result

    # Создание
    is_reminder = bool(REMIND_RE.search(lower))
    # Встреча важнее слова «напоминание»: «напомни про встречу в 15» — встреча, с проверкой конфликтов
    is_meeting = bool(re.search(MEETING_WORD_RE, lower))
    has_verb = bool(CREATE_VERB_RE.search(lower))
    has_need = bool(re.search(r"\b(нужно|надо|необходимо|не\s+забыть|должен|должна|должны|пора)\b", lower))
    is_task_word = bool(re.search(r"^(?:поставь|запиши|добавь|создай)\s+задач", lower))
    if not (has_verb or is_meeting or has_need or is_task_word or ((info.date or time) and len(keywords(_cut(text, info.spans + time_spans))) >= 1)):
        return result  # unknown

    result.intent = "create"
    result.type = "meeting" if is_meeting else "reminder" if is_reminder else "task"
    result.time = time
    result.time_ambiguous = ambiguous
    result.date = info.date
    result.date_kind = info.kind if info.date else ("none" if result.type == "task" and not time else "exact")
    result.vague_period = bool(info.period) and not info.date

    body = _cut(text, info.spans + time_spans)
    body = re.sub(r"^\s*(?:пожалуйста\s+)?(?:напомни(?:те)?|поставь|запиши|добавь|создай|запланируй|назначь|внеси|заведи)(?:\s+мне)?\b", " ", body, flags=I)
    body = re.sub(r"^\s*(?:новую\s+|новое\s+)?(?:задачу|задача|напоминание)\b", " ", body, flags=I)
    body = re.sub(r"\b(?:у\s+меня|мне|нужно|надо|необходимо|не\s+забыть|пожалуйста)\b", " ", body, flags=I)
    body = strip_fillers(body)
    body = re.sub(r"\b(?:утром|вечером|днём|днем)\b", " ", body, flags=I)
    # Мусор распознавания: «встретиться с вами с Иваном» → «встретиться с Иваном»
    body = re.sub(r"\bс\s+вами\s+(?=со?\s)", " ", body, flags=I)
    body = " ".join(body.split()).strip(" ,.:;!—-")
    body = _clean_clause(body)
    body = strip_fillers(body)
    if is_reminder:
        body = re.sub(r"^(?:про|о|об|насчёт|насчет)\s+", "", body, flags=I)
    # Существительное встречи — в именительный падеж: «встречу с Иваном» → «Встреча с Иваном»
    first = body.split(" ", 1)
    if first and first[0].lower() in MEETING_VERBS and len(first) > 1 and re.match(r"(?:с|со)\s", first[1], I):
        first[0] = MEETING_VERBS[first[0].lower()]
    if first and first[0].lower() in MEETING_NOUNS:
        noun = MEETING_NOUNS[first[0].lower()]
        rest = first[1] if len(first) > 1 else ""
        result.has_subject = bool(keywords(rest))
        body = f"{noun} {rest}".strip()
    elif result.type == "meeting":
        result.has_subject = bool(keywords(body))
    body = body.strip(" ,.:;!—-")
    result.text = capitalize(body) if body else None
    return result


def _parse_move_all(text: str, now: datetime, result: Parse) -> Parse:
    """«Перенеси все встречи на завтра», «Перенеси всё с пятницы на понедельник», «Сдвинь все дела на час позже»."""
    today = now.date()
    result.intent, result.all = "move", True
    result.filter_type = _filter_type(text.lower())
    result.source = today
    src = SOURCE_RE.search(text)
    if src:
        word = (src.group(1) or src.group(2)).lower()
        shifts = {"сегодня": 0, "сегодняшн": 0, "завтра": 1, "завтрашн": 1, "послезавтра": 2}
        key = next((k for k in shifts if word.startswith(k)), None)
        if key is not None:
            result.source = today + timedelta(days=shifts[key])
        else:
            result.source = today + timedelta(days=(_weekday_index(word) - today.weekday()) % 7)
        text = _cut(text, [src.span()])
    shift = SHIFT_RE.search(text)
    if shift:
        amount = shift.group(1).lower()
        result.shift = 30 if amount == "полчаса" else 60 * int(shift.group(2) or 1) if "час" in amount else int(shift.group(3))
        result.shift *= -1 if shift.group(4).lower() == "раньше" else 1
        return result
    time, ambiguous, _, info = parse_when(text, now)
    result.date = info.date
    return result


# «чтобы я проверил наушники» → «проверить наушники»: союз и «я» убираем, глагол — в инфинитив
CLAUSE_RE = re.compile(
    r"^(?:(?:о\s+том|про\s+то)?[\s,]*(?:что|чтобы|чтоб)\b[\s,]*(?:(?:я|мы)\s+)?|(?:я|мы)\s+)"
    r"(?:(?:должен|должна|должны|обязательно)\s+)?",
    I,
)

def _infinitive(word: str) -> str:
    if _MORPH is None:
        return word
    for form in _MORPH.parse(word):
        if form.tag.POS == "VERB" and form.score > 0.1:
            return form.normal_form
        if form.tag.POS == "INFN":
            return word
    return word


# Слова, которые в начале названия ничего не значат: обращения, модальные, «я», «можно», «бы»…
FILLER_RE = re.compile(
    r"^(?:слушай(?:те)?|слышь|стивенс|стивен|стив|stevens|ну|а|так|эй|окей|ок|короче|кстати|вот|типа|значит"
    r"|в\s+общем|давай(?:те)?|пожалуйста|мне|меня|у\s+меня|я|мы|ты|бы|же|ещё|еще|тогда|потом|уже|обязательно"
    r"|можно|можешь|нужно|надо|необходимо|хочу|хочется|хотел[аи]?|хотелось|должен|должна|должны|стоит|пора|не\s+забыть"
    r"|(?:о\s+том|про\s+то)?\s*(?:что|чтобы|чтоб))\b[\s,!.]*",
    I,
)


def strip_fillers(body: str) -> str:
    """«Слушай мне можно встретиться с Дашей» → «встретиться с Дашей». Глагол после «я»/«хочу» — в инфинитив."""
    before = body = body.strip(" ,")
    while True:
        m = FILLER_RE.match(body)
        if not m or not body[m.end():].strip():
            break
        body = body[m.end():]
    if body != before:
        head, _, tail = body.partition(" ")
        verb = _infinitive(head)
        if verb != head.lower():
            body = f"{verb} {tail}".strip()
    return body


def _clean_clause(body: str) -> str:
    m = CLAUSE_RE.match(body)
    if not m or not body[m.end():].strip():
        return body
    rest = body[m.end():].strip(" ,")
    head, _, tail = rest.partition(" ")
    verb = _infinitive(head)
    if verb != head.lower():
        rest = f"{verb} {tail}".strip()
    return rest


# ── Несколько дел одной фразой ────────────────────────

MULTI_SPLIT_RE = re.compile(
    r"\s*[;]\s*|\s*,\s*(?:а\s+|и\s+)?(?:потом|затем|ещё|еще|также|после\s+этого)\b\s*"
    r"|\s+(?:а\s+потом|и\s+потом|потом|затем|а\s+также|и\s+ещё|и\s+еще|а\s+ещё|а\s+еще)\s+"
    r"|\s*,?\s+(?:а|и)\s+(?=(?:в|во|к)\s+\d|(?:завтра|послезавтра|сегодня|через)\b|в\s+" + WEEKDAY_RE.replace("(", "(?:", 1) + r")",
    I,
)


def parse_many(raw: str, now: datetime) -> list[Parse] | None:
    """«Завтра в 10 созвон с Иваном, потом отправить договор Маше» → два создания.
    None — фраза про одно дело. Дата без явного дня берётся из предыдущей части."""
    text = strip_lead(" ".join(raw.strip().split()))
    parts = [part.strip(" ,.") for part in MULTI_SPLIT_RE.split(text) if part and part.strip(" ,.")]
    if len(parts) < 2 or len(parts) > 5:
        return None
    first = parse(parts[0], now)
    if first.intent != "create" or not first.text:
        return None
    result = [first]
    prefix = "напомни " if first.type == "reminder" else "запиши "
    for part in parts[1:]:
        p = parse(part, now)
        if p.intent != "create" or not p.text:
            p = parse(prefix + part, now)
        if p.intent != "create" or not p.text:
            return None
        # «Напомни в 10 позвонить Ивану и в 12 написать Маше» — оба напоминания
        if first.type == "reminder" and p.type == "task" and not re.search(r"\bзадач", part, I):
            p.type = "reminder"
        if not p.date and result[-1].date:
            p.date, p.date_kind = result[-1].date, "exact"
        result.append(p)
    return result


def _target(text: str, now: datetime) -> Parse:
    """О какой записи речь в правке: ключи, тип, день, «её»."""
    time, ambiguous, time_spans, info = parse_when(text, now)
    target = Parse()
    target.filter_type = _filter_type(text)
    target.keywords, target.required = search_keywords(_cut(text, info.spans + time_spans), target.filter_type)
    target.ref = bool(REF_RE.search(text)) or not (target.keywords or info.date)
    target.date = info.date
    target.time = time if not ambiguous else None
    return target


def _has_when(text: str, now: datetime) -> bool:
    time, _, _, info = parse_when(text, now)
    return bool(time or info.date or info.period)


def _new_name(text: str) -> str:
    return capitalize(re.sub(r"[«»\"„“”']", "", text).strip(" ,.!?:—-"))


def parse_edit(text: str, now: datetime) -> Parse | None:
    """Правка записи. Не правка — None."""
    result: Parse | None = None
    if m := RENAME_RE.match(text):
        rest = m["rest"].strip()
        # «переименуй встречу в субботу в созвон» — делим по тому «в», после которого не дата
        for split in re.finditer(r"(?:^|\s)(?:в|на|как)\s+", rest, I):
            new = rest[split.end():]
            if new.strip() and not _has_when(rest[split.start():], now):
                result = _target(rest[:split.start()], now)
                result.new_text = _new_name(new)
                break
        else:
            ref = re.match(r"^(её|ее|его|это|эту|этот)\s+(.+)$", rest, I)
            if not ref:
                return None
            result = _target(ref[1], now)
            result.new_text = _new_name(ref[2])
    elif m := RETYPE_RE.match(text):
        result = _target(re.sub(r"^(?:из|с)\s+", "", m["target"], flags=I), now)
        result.new_type = next(v for k, v in TYPE_BY_STEM.items() if m["type"].lower().startswith(k))
    elif m := TO_NOTE_RE.match(text):
        result = _target(m["target"] or "её", now)
        result.new_type = "note"
    elif m := NOTE_RE.match(text):
        result = _target(m["target"] or m["ref"] or m["target2"] or "", now)
        result.note = (m["note"] or m["note2"]).strip(" ,.!?:—-")
    if not result or not (result.new_text or result.new_type or result.note):
        return None
    result.intent = "edit"
    return result


CANCEL_REPLY_RE = re.compile(
    r"^(?:стоп|стой|отмена|отмени(?:ть)?|не\s+надо|забудь|убери\s+вс[её]|ничего\s+не\s+надо)[\s.!,]*$", I)
REPLY_TIME_RE = re.compile(r"(?<![\d:.])(\d{1,2})(?:[:.](\d{2}))?(?![\d:.])(?!\s*(?:" + "|".join(
    ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября",
     "декабря", "числа", "минут", "час", "дн", "недел"]) + r"))", I)


def is_cancel_reply(raw: str) -> bool:
    """«Стой», «отмена», «убери всё» — ответ на уточнение, который всё отменяет."""
    return bool(CANCEL_REPLY_RE.match(" ".join(raw.lower().split())))


def reply_time(raw: str) -> tuple[str | None, bool]:
    """Время из сбивчивого ответа: «есть 10 … стой … поставь 10» → последнее упомянутое, 10:00.
    Часы 1–7 без минут — вечер (13–19), с пометкой «неоднозначно»."""
    found = list(REPLY_TIME_RE.finditer(raw))
    if not found:
        return None, False
    hours, mins = int(found[-1].group(1)), int(found[-1].group(2) or 0)
    if hours > 23 or mins > 59:
        return None, False
    ambiguous = 1 <= hours <= 7 and not found[-1].group(2)
    if ambiguous:
        hours += 12
    return f"{hours:02d}:{mins:02d}", ambiguous


def parse_reply(raw: str, now: datetime) -> Parse:
    """Ответ на уточнение: только дата, время или участник («в 15», «завтра», «с Иваном»)."""
    return parse(raw, now)


def describe_period(start: date, end: date, today: date) -> Period:
    if start == end:
        return _day_period(start, today)
    label, speech = _range_label(start, end)
    return Period(start, end, label, speech)
