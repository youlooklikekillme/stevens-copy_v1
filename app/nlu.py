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
    "сдвинь", "подвинь", "поменяй", "измени", "отредактируй", "отредактируйте", "редактируй", "отредактировать", "выполненными", "время", "дату", "позже", "раньше", "выполненной", "готово",
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
    repeat_days: list[int] | None = None  # create: «каждый вторник» → [1]; пн = 0
    until: date | None = None   # create: повтор «до конца года» — последний день; None — без конца
    series_all: bool = False    # delete: «убери планёрку вообще» — все предстоящие дни повтора
    series_one: bool = False    # delete: «удали только ближайшую планёрку» — один день повтора
    group: bool = False         # delete: «удали эти две», «удали их» — все показанные только что записи
    group_count: int | None = None  # «эти две» → 2: если показано другое число, переспросим
    day_part: str | None = None  # list: «сегодня вечером» — утром | днём | вечером
    next: bool = False          # search: «какое следующее дело», «ближайшая встреча» — ближайшая запись впереди
    only_when: bool = False     # во фразе только день: «а на завтра?» — список, а в уточнении это ответ
    doubt: list[dict] = field(default_factory=list)  # create: почему разбор неуверенный — спросим «Верно понял?»
    end_time: str | None = None  # «с 9 до 18»: до скольки (у занятости, у встречи — если сказали)


# ── Время ─────────────────────────────────────────────

TIME_HM = re.compile(r"(?:\bв\s+|\bна\s+|^|\s)(\d{1,2})[:.](\d{2})\b", I)
TIME_H = re.compile(r"\bв\s+(\d{1,2})(?:\s*(?:час(?:а|ов)?|ч\.?))?(?:\s+(утра|дня|вечера|ночи))?(?=\s|$|[,.!?])"
                    r"|\bна\s+(\d{1,2})(?:\s*(?:час(?:а|ов)?|ч\.?))?\s+(утра|дня|вечера|ночи)(?=\s|$|[,.!?])", I)
TIME_WORDS = re.compile(r"\b(утром|вечером|днём|днем)\b", I)


# Время словами: «в полдень», «в полночь», «в пол восьмого», «в половине девятого», «в четверть третьего»,
# «без пятнадцати три», «без четверти девять»
_HOUR_GEN = ("первого", "второго", "третьего", "четв[её]ртого", "пятого", "шестого", "седьмого", "восьмого",
             "девятого", "десятого", "одиннадцатого", "двенадцатого")
_HOUR_NOM = {"час": 1, "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6, "семь": 7, "восемь": 8,
             "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12}
_HOUR_GEN_ALT = "|".join(_HOUR_GEN) + r"|\d{1,2}-?го"
_MIN_WORDS = {"двадцати пяти": 25, "пяти": 5, "десяти": 10, "пятнадцати": 15, "четверти": 15, "двадцати": 20}
_MIN_ALT = "|".join(w.replace(" ", r"\s+") for w in _MIN_WORDS)
SPOKEN_TIME_RE = re.compile(
    r"(?:\b(?:в|на|к)\s+)?\b(?:"
    r"(?P<noon>полдень|полудня)|(?P<midnight>полночь|полуночи)"
    rf"|(?:пол\s*-?\s*|половин[уеы]\s+)(?P<half>{_HOUR_GEN_ALT})(?:\s+часа)?(?!\s+числ)"
    rf"|четверть\s+(?P<quarter>{_HOUR_GEN_ALT})(?:\s+часа)?(?!\s+числ)"
    rf"|без\s+(?P<before>\d{{1,2}}|{_MIN_ALT})(?:\s+минут)?\s+(?P<hour>\d{{1,2}}|{'|'.join(_HOUR_NOM)})"
    r")(?:\s+(?P<part>утра|дня|вечера|ночи))?(?=\s|$|[,.!?])", I)


def _hour_gen(word: str) -> int:
    """«восьмого» → 8, «9-го» → 9: час, к которому идёт «пол», «четверть»."""
    if m := re.match(r"\d+", word):
        return int(m.group(0))
    return next(i + 1 for i, g in enumerate(_HOUR_GEN) if re.match(g, word.lower()))


def _spoken_time(text: str) -> tuple[int, int, str, tuple[int, int], bool] | None:
    """Час, минуты, «утра/вечера», кусок фразы и признак «полночь» или None."""
    m = SPOKEN_TIME_RE.search(text)
    if not m:
        return None
    part = (m.group("part") or "").lower()
    if m.group("noon"):
        return 12, 0, "дня", m.span(), False
    if m.group("midnight"):
        return 0, 0, "ночи", m.span(), True
    if m.group("half") or m.group("quarter"):
        hour = _hour_gen(m.group("half") or m.group("quarter"))
        if not 1 <= hour <= 24:
            return None
        return (hour - 1) % 24, 30 if m.group("half") else 15, part, m.span(), False
    raw = m.group("before").lower()
    minutes = int(raw) if raw.isdigit() else _MIN_WORDS[re.sub(r"\s+", " ", raw)]
    word = m.group("hour").lower()
    hour = int(word) if word.isdigit() else _HOUR_NOM[word]
    if not 0 < minutes < 60 or not 1 <= hour <= 24:
        return None
    return (hour - 1) % 24, 60 - minutes, part, m.span(), False


def _parse_time(text: str) -> tuple[str | None, bool, list[tuple[int, int]]]:
    """Время HH:MM, признак «утро или вечер — неясно» и куски фразы, где оно было."""
    spans: list[tuple[int, int]] = []
    spoken = _spoken_time(text)
    if spoken:
        h, mm, part, span, _ = spoken
        spans.append(span)
        ambiguous = False
        word = TIME_WORDS.search(text)
        if part in ("вечера", "дня") and h < 12:
            h += 12
        elif part == "ночи" and h == 12:
            h = 0
        elif not part and word and word.group(1).lower() == "вечером" and h < 12:
            h += 12
        elif not part and word and word.group(1).lower() in ("днём", "днем") and h < 8:
            h += 12
        elif not part and not word and 1 <= h <= 7:
            h += 12
            ambiguous = True
        elif not part and h == 0 and "полноч" not in text[span[0]:span[1]].lower() and "полуноч" not in text[span[0]:span[1]].lower():
            h, ambiguous = 12, False  # «без пяти час» днём
        return f"{h:02d}:{mm:02d}", ambiguous, spans
    # «05.01 в 10» — это дата и время, а не 05:01
    valid = [x for x in TIME_HM.finditer(text) if int(x.group(1)) < 24 and int(x.group(2)) < 60]
    bare_dot = [x for x in valid if "." in x.group(0) and not re.match(r"\s*(?:в|на)\s", x.group(0), I)]
    if bare_dot and (len(valid) > len(bare_dot) or TIME_H.search(text)):
        valid = [x for x in valid if x not in bare_dot]
    m = valid[0] if valid else None
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
        h = int(m.group(1) or m.group(3))
        part = (m.group(2) or m.group(4) or "").lower()
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


# Интервал «с 9 до 18», «с 10:00 до 13:20», «с 9 вечера до 11», «9:00–18:00», «с 9-18»
_IV_H = r"(\d{1,2})(?:[:.](\d{2}))?(?:\s*(?:час(?:а|ов)?|ч\.?))?(?:\s+(утра|дня|вечера|ночи))?"
_IV_NOT_DATE = rf"(?!\s*(?:числ|{MONTH_RE}|[:.]?\d))"
INTERVAL_RES = (
    re.compile(rf"\b(?:с|со|от)\s+{_IV_H}\s+до\s+{_IV_H}\b{_IV_NOT_DATE}", I),
    re.compile(rf"(?:\b(?:с|со|в)\s+)?(\d{{1,2}})[:.](\d{{2}})()\s*[-–—]\s*(\d{{1,2}})[:.](\d{{2}})()\b{_IV_NOT_DATE}", I),
    re.compile(rf"\b(?:с|со)\s+(\d{{1,2}})()()\s*[-–—]\s*(\d{{1,2}})()()\b{_IV_NOT_DATE}", I),
)


def _iv_hour(h: int, part: str) -> int:
    if part in ("вечера", "дня") and h < 12:
        return h + 12
    if part == "ночи" and h == 12:
        return 0
    return h


def minutes_of(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def parse_interval(text: str) -> tuple[str, str, bool, tuple[int, int]] | None:
    """«с 9 до 18» → начало, конец, «утро или вечер — неясно» и кусок фразы. Не интервал — None."""
    for regex in INTERVAL_RES:
        m = regex.search(text)
        if not m:
            continue
        h1, m1, p1, h2, m2, p2 = m.groups()[:6]
        sh, eh = int(h1), int(h2)
        sm, em = int(m1 or 0), int(m2 or 0)
        p1, p2 = (p1 or "").lower(), (p2 or "").lower()
        if sh > 24 or eh > 24 or sm > 59 or em > 59:
            continue
        sh, eh = _iv_hour(sh, p1 or (p2 if p2 in ("утра", "дня", "вечера") and sh < eh else "")), _iv_hour(eh, p2)
        ambiguous = False
        if not p1 and not p2:
            if 1 <= sh <= 7 and eh <= 12 and eh + 12 <= 24:
                # «с 2 до 5» — днём; «с 7 до 9» — утро или вечер? Ставим вечер и переспрашиваем
                sh, eh, ambiguous = sh + 12, eh + 12, sh >= 6 or eh >= 8
            elif eh < sh and eh + 12 > sh and eh < 12:
                eh += 12  # «с 10 до 2» — до 14:00
        if p1 and not p2 and eh < sh and eh + 12 > sh and eh < 12:
            eh += 12      # «с 9 утра до 6»
        sh, eh = sh % 24, eh % 24
        start, end = f"{sh:02d}:{sm:02d}", f"{eh:02d}:{em:02d}"
        if start == end:
            continue
        return start, end, ambiguous, m.span()
    return None


# «через 11 минут», «через полчаса», «через час и 20 минут», «через пару дней», «через неделю»
_UNITS = {"ноль": 0, "один": 1, "одну": 1, "одна": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5,
          "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10, "одиннадцать": 11, "двенадцать": 12,
          "тринадцать": 13, "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16, "семнадцать": 17,
          "восемнадцать": 18, "девятнадцать": 19, "пару": 2, "пара": 2}
_TENS = {"двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50}
_NUM = rf"(?:\d+(?:[.,]5)?|(?:{'|'.join(_TENS)})(?:\s+(?:{'|'.join(_UNITS)}))?|{'|'.join(_UNITS)})"
_UNIT = r"(?:полчаса|полтора\s+часа|полминуты|минут[уы]?|мин\.?|час(?:а|ов)?|д(?:ень|ня|ней)|сутки|суток|недел[юи]|недель|месяц(?:а|ев)?)"
REL_RE = re.compile(
    rf"\bчерез\s+(?:({_NUM})\s+)?({_UNIT})(?:\s+(?:и\s+)?({_NUM})(?:\s+(минут[уы]?|мин\.?))?)?(?=\s|$|[,.!?])",
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
    span = m.span()
    if m.group(3) and not m.group(4) and not unit.startswith(("час", "полтора")):
        # «Через 2 дня 3 задачи» — число без «минут» здесь не минуты; «через час двадцать» — минуты
        span, extra = (m.start(), m.end(2)), timedelta()
    else:
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
        return (now + timedelta(days=int(n))).date(), None, span
    elif unit.startswith("недел"):
        return (now + timedelta(weeks=int(n))).date(), None, span
    else:
        month = now.month - 1 + int(n)
        year, month = now.year + month // 12, month % 12 + 1
        day = min(now.day, [31, 29 if year % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
        return date(year, month, day), None, span
    moment = now + delta + extra
    return moment.date(), moment.strftime("%H:%M"), span


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
    info = _parse_date(text, now, time)
    # «В полночь», «сегодня в 12 ночи» — это начало завтрашнего дня
    if time == "00:00" and re.search(r"\b(?:полноч|полуноч|12\s+ночи)", text, I) and info.date in (None, now.date()) \
            and not info.period:
        info.date, info.kind = now.date() + timedelta(days=1), "exact"
    return time, ambiguous, time_spans, info


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


def _near_year(day: date, today: date) -> date:
    """«1 января» 31 декабря — это следующий год, а не прошедший январь. Полгода назад и раньше — уже следующий."""
    if (today - day).days > 183:
        try:
            return day.replace(year=day.year + 1)
        except ValueError:
            return day
    return day


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
    m = re.search(r"\bв\s+следующем\s+месяце\b", text, I)
    if m:
        start = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
        found(m, period=Period(start, end, "в следующем месяце", "в следующем месяце"))
    m = re.search(r"\bв\s+(?:этом|текущем)\s+месяце\b", text, I)
    if m:
        start = today.replace(day=1)
        end = (start.replace(month=start.month % 12 + 1, year=start.year + (start.month == 12)) - timedelta(days=1))
        found(m, period=Period(start, end, "в этом месяце", "в этом месяце"))
    # «На выходных», «в эти выходные», «на следующих выходных» — ближайшие сб–вс
    m = re.search(r"\b(?:на|в)\s+(?:(эт(?:и|их)|следующ(?:ие|их))\s+)?выходн(?:ых|ые)\b", text, I)
    if m:
        start = today if today.weekday() >= 5 else today + timedelta(days=5 - today.weekday())
        start -= timedelta(days=start.weekday() - 5)
        label = "на выходных"
        if (m.group(1) or "").lower().startswith("следующ"):
            start += timedelta(days=7)
            label = "на следующих выходных"
        found(m, period=Period(max(start, today), start + timedelta(days=1), label, label))

    # Конкретный день
    if not info.date:
        m = re.search(r"\b(?:на\s+)?(позавчера|вчера|сегодня|послезавтра|завтра)(?:шн(?:ий|юю|ее|яя|его|ей|ие|их|ему|ем))?\b", text, I)
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
                day = date(int(m.group(3)) if m.group(3) else today.year, _month_index(m.group(2)) + 1, int(m.group(1)))
                found(m, day if m.group(3) else _near_year(day, today))
            except ValueError:
                pass
    if not info.date:
        # «25 числа», «на 3-е число» — ближайшее такое число: в этом месяце, а если прошло — в следующем
        m = re.search(r"\b(?:на\s+)?(\d{1,2})(?:-?(?:го|е|ое))?\s+числ[оа]\b", text, I)
        if m and 1 <= int(m.group(1)) <= 31:
            year, month = today.year, today.month
            if int(m.group(1)) < today.day:
                year, month = year + (month == 12), month % 12 + 1
            try:
                found(m, date(year, month, int(m.group(1))))
            except ValueError:
                pass
    if not info.date:
        m = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b|\b(\d{1,2})\.(\d{1,2})(?:\.(\d{4}))?\b(?!:)", text)
        if m and not (re.search(r"\d[:.]\d{2}\b", m.group(0)) and m.group(4)
                      and time == f"{int(m.group(4)):02d}:{int(m.group(5)):02d}"):
            try:
                if m.group(1):
                    found(m, date(int(m.group(1)), int(m.group(2)), int(m.group(3))))
                else:
                    day = date(int(m.group(6) or today.year), int(m.group(5)), int(m.group(4)))
                    found(m, day if m.group(6) else _near_year(day, today))
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
SEARCH_FILLERS = {"последний", "раз", "что-нибудь", "что-то", "нибудь", "вообще", "всё", "все", "весь", "дело", "раньше",
                  "ближайший", "следующий", "дальше"}

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
    if re.search(r"\bзанятост", lower):
        return "busy"
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
HELP_RE = re.compile(r"(^(?:привет\w*|здравствуй\w*|добр(?:ый|ое|ого)\s+(?:день|утро|вечер|утра|вечера|дня)|хай|салют)(?:[\s,!.]+(?:стивенс|stevens))?[\s,!.)]*$"
                     r"|^(?:кто\s+ты|ты\s+кто|как\s+тебя\s+зовут|что\s+ты\s+такое)[\s?!.]*$|что\s+ты\s+умеешь|что\s+умеешь|чем\s+(?:ты\s+)?(?:можешь\s+)?помочь|^помощь|^справка|как\s+(?:тобой|этим)\s+пользоваться)", I)
DELETE_RE = re.compile(r"^(?:пожалуйста\s+)?(удали(?:те)?|удалить|убери(?:те)?|убрать|сотри|стереть|очисти(?:ть)?"
                       r"|отмени(?:те)?(?=\s+(?!это\b|последн|вс[её]\b)\S))\b", I)
# «Удали все дела на завтра», «убери всё за все дни», «очисти пятницу»
BULK_RE = re.compile(r"(\bвс[её]\b|\bвсех\b|^(?:пожалуйста\s+)?очисти)", I)
ALL_DAYS_RE = re.compile(r"\b(?:за\s+)?(?:все\s+дни|вс[её]\s+время|всю\s+историю)\b", I)
COMPLETE_RE = re.compile(rf"(\bотметь\b.*\bвыполнен|\bя\s+(?:уже\s+)?(?:сделал|сделала|выполнил|выполнила|закончил|закончила)\b|^(?:сделал|сделала|выполнил|выполнила)\b|\bотметь\b.*\bсделан|\s(?:—\s*|-\s*)?(?:сделал|сделала|выполнил|выполнила|сделано|выполнено|готово)[\s.!]*$)", I)
SEARCH_RE = re.compile(rf"(\bнайди\b|\bнайти\b|\bпоищи\b|\bищи\b|^когда\b(?!-)|\bкогда\s+(?:у\s+меня\s+)?(?:был|была|было|будет)\b|\bставил)", I)
LIST_RE = re.compile(rf"(что\s+у\s+меня|какие\s+(?:у\s+меня\s+)?(?:планы|дела|задачи|встречи|напоминания)|^покажи|что\s+(?:я\s+)?запланировано|есть\s+ли\s+у\s+меня|^что\s+(?:в|во|на)\s|^сколько\s+(?:у\s+меня\s+)?(?:дел|задач|встреч|напоминаний|записей)|^что\s+я\s+(?:делал|делала|делаю|буду\s+делать)|^что\s+(?:(?:мне|ещё|еще|у\s+меня)\s+)*осталось|^что\s+(?:сегодня|завтра|послезавтра)\b|что\s+(?:было|будет)|мо[её]\s+расписание|\bпланы\s+на\b|чем\s+я\s+занят)", I)
OFFTOPIC_RE = re.compile(r"(погод|анекдот|шутк|курс\s+(?:доллар|евро|валют)|новост|рецепт|кто\s+так|переведи|столиц|сколько\s+будет|расскажи\s+(?:о|про)\s+себе|как\s+дела|фильм|музык|игр[ау]|стих)", I)
CREATE_VERB_RE = re.compile(r"^(?:пожалуйста\s+)?(напомни(?:те)?(?:\s+мне)?|поставь|запиши|добавь|создай|запланируй|назначь|внеси|заведи)\b", I)
DONE_RE = re.compile(r"^(?:готово|сделано|выполнено|сделал|сделала)[\s.!]*$", I)
# «Эти две», «их», «обе», «все три» — несколько записей, только что показанных
GROUP_RE = re.compile(r"\b(?:(?:эти|этих|их|обе|оба|обеих|обоих)(?:\s+(?:вс[еи]|две|два|три|четыре|пять))?"
                      r"|вс[еи]\s+(?:эти|две|два|три|четыре|пять)|(?:две|два|три|четыре|пять)\s+эти)\b", I)
REF_RE = re.compile(rf"\b(её|ее|его|это|эту|этот|эта|ней|нему|неё|него|этой|этому|последн\w*|предыдущ\w*)\b", I)
MOVE_RE = re.compile(r"^(?:перенеси(?:те)?|перенести|передвинь|сдвинь|подвинь|поменяй|измени|отредактируй(?:те)?|редактируй|отредактировать)\b", I)
# Правка записи: название, тип, заметка
RENAME_RE = re.compile(r"^(?:переименуй(?:те)?|переименовать|переназови|назови|(?:поменяй|измени|смени)\s+название)\b\s*(?P<rest>.*)$", I)
RETYPE_RE = re.compile(
    r"^(?:сделай|сделать|преврати|переделай|поменяй|измени|смени)\s+(?:(?P<target>.+?)\s+)?(?:тип\s+)?(?:(?:в|на)\s+)?"
    r"(?P<type>задач(?:ей|у|а)|напоминани(?:ем|е)|встреч(?:ей|у|а))[\s.!?]*$", I)
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
NEXT_RE = re.compile(r"\b(?:ближайш|следующ)\w*\s+(?:\w+\s+)?(?:встреч|дел[оа]?\b|задач|напоминани|созвон|запис)"
                     r"|^что\s+(?:у\s+меня\s+)?(?:дальше|следующее|на\s+очереди)\b|^(?:а\s+)?дальше\s+что\b", I)
REPEATS_LIST_RE = re.compile(r"^(?:покажи|какие|список|мои|все|что\s+за)\b(?:\s+(?:у\s+меня|мои|все))*\s+(?:повтор\w*|регулярн\w*)"
                             r"|^что\s+(?:у\s+меня\s+)?(?:повторя\w*|регулярно)", I)
UNDATED_RE = re.compile(r"\bбез\s+(?:срока|сроков|даты|дат|дедлайна)\b", I)
TYPE_BY_STEM = {"задач": "task", "напомин": "reminder", "встреч": "meeting"}
SHIFT_RE = re.compile(r"\bна\s+(полчаса|(\d+)?\s*час\w*|(\d+)\s*минут\w*)\s+(позже|раньше)\b", I)
ALL_RE = re.compile(r"\b(?:вс[её]|всех)\b", I)
SOURCE_RE = re.compile(
    r"\b(?:с|со)\s+(сегодня|завтра|послезавтра|понедельника|вторника|среды|четверга|пятницы|субботы|воскресенья)\b"
    r"|\b(сегодняшн\w*|завтрашн\w*)\b",
    I,
)
TIME_ON_RE = re.compile(r"\bна\s+(\d{1,2})\b(?![:.]\d|\s*(?:час|минут|числ|январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр))", I)
BUSY_WORD_RE = re.compile(r"\b(?:занятост\w*|(?:я\s+)?(?:буду\s+)?занят[аы]?)\b", I)
BUSY_PLACES = {"работе": "работа", "парах": "пары", "учёбе": "учёба", "учебе": "учёба", "лекциях": "лекции",
               "школе": "школа", "универе": "универ", "университете": "университет", "институте": "институт",
               "колледже": "колледж", "тренировке": "тренировка", "смене": "смена", "занятиях": "занятия"}
BUSY_VERBS = {"работаю": "работа", "работать": "работа", "учусь": "учёба", "учиться": "учёба", "тренируюсь": "тренировка",
              "тренироваться": "тренировка", "занимаюсь": "занятия", "заниматься": "занятия"}
BUSY_PLACE_RE = re.compile(rf"^(?:на|в|во)\s+({'|'.join(BUSY_PLACES)})\b", I)
REMIND_RE = re.compile(r"(^напомни|\bнапомн(?:и|ите|ить|ил|ила|им)\b|\bнапоминани)", I)


# Вводные слова и просьбы в начале фразы: «Слушай, а можешь напомнить, что…» → «напомни …»
LEAD_RE = re.compile(
    r"^(?:(?:слушай(?:те)?|послушай|слышь|стивенс|стивен|стив[еи]?[сз]|стифс|стив|stevens|steves?|ну|а|так|эй|окей|ок|короче|пожалуйста"
    r"|давай(?:те)?|кстати|вот|в\s+общем|типа|значит|смотри|и\s+ещ[её]|ещ[её]|также|и\s+также|плюс)\b[\s,!.]*)+",
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
    r"(когда\s+(?:я\s+)?свобод|\bсвобод(?:ен|на|ны)\b|^есть\s+(?:у\s+меня\s+)?(?:свободн\w*\s+|сейчас\s+)?(?:окн|окошк|время|час)|свободн\w*\s+(?:время|окн|час|полчас)|(?:найди|подбери|поищи)\s+(?:мне\s+)?"
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


# ── Повторы: «каждый вторник», «по средам и пятницам», «до конца года» ──

_DAY_STEMS = (("понедельник", 0), ("вторник", 1), ("сред", 2), ("четверг", 3), ("пятниц", 4), ("суббот", 5),
              ("воскресень", 6))
_DAY_ONE = r"(?:понедельник[уа]?|вторник[уа]?|сред[уае]|четверг[уа]?|пятниц[уае]|суббот[уае]|воскресень[еяю])"
_DAY_MANY = r"(?:понедельникам|вторникам|средам|четвергам|пятницам|субботам|воскресеньям)"
_JOIN = r"\s*(?:,|\bи\b)\s*(?:(?:в|во|по)\s+)?"
REPEAT_RES = (
    # «каждый вторник», «каждую среду и пятницу», «каждую неделю во вторник», «еженедельно по средам»
    re.compile(rf"\b(?:(?:каждую\s+неделю|еженедельно|раз\s+в\s+неделю)\s+(?:в|во|по)\s+|кажд(?:ый|ую|ое)\s+)"
               rf"(?P<days>(?:{_DAY_ONE}|{_DAY_MANY})(?:{_JOIN}(?:{_DAY_ONE}|{_DAY_MANY}))*)\b", I),
    # «по вторникам», «по понедельникам и четвергам». «С понедельника по среду» — не повтор: там «по» + один день
    re.compile(rf"(?:^|(?<=\s))(?:(?:каждую\s+неделю|еженедельно)\s+)?по\s+(?P<days>{_DAY_MANY}(?:{_JOIN}{_DAY_MANY})*)\b", I),
)
WEEKDAY_RANGE_RE = re.compile(r"\bс\s+(понедельника|вторника|среды|четверга|пятницы|субботы|воскресенья)\s+"
                              r"(?:по|до)\s+(понедельник|вторник|среду|четверг|пятницу|субботу|воскресенье|"
                              r"понедельника|вторника|среды|четверга|пятницы|субботы|воскресенья)\b", I)
REPEAT_WEEKDAYS_RE = re.compile(r"\b(?:по\s+будн(?:ям|им\s+дням|им)|кажд(?:ый|ые)\s+будни(?:й\s+день|е\s+дни)?|в\s+будни)\b", I)
REPEAT_WEEKEND_RE = re.compile(r"\b(?:по\s+выходным(?:\s+дням)?|кажд(?:ые|ый)\s+выходн(?:ые|ой))\b", I)
REPEAT_DAILY_RE = re.compile(r"\b(?:каждый\s+день|ежедневно)\b", I)

_SEASONS = {"весны": 3, "лета": 6, "осени": 9, "зимы": 12}
_COUNT = {"один": 1, "одну": 1, "два": 2, "две": 2, "три": 3, "четыре": 4, "пять": 5, "шесть": 6}
UNTIL_RES = (
    ("year", re.compile(r"\bдо\s+(?:конца\s+(?:этого\s+|текущего\s+)?года|нового\s+года)\b", I)),
    ("week", re.compile(r"\bдо\s+конца\s+(?:этой\s+)?недели\b", I)),
    ("month_end", re.compile(r"\bдо\s+конца\s+(?:этого\s+|текущего\s+)?месяца\b", I)),
    ("end_of", re.compile(rf"\bдо\s+конца\s+{MONTH_RE}\b", I)),
    ("day", re.compile(rf"\bдо\s+(\d{{1,2}})\s+{MONTH_RE}(?:\s+(\d{{4}}))?(?:\s+года)?\b", I)),
    ("month", re.compile(rf"\bдо\s+{MONTH_RE}\b", I)),
    ("season", re.compile(r"\bдо\s+(весны|лета|осени|зимы)\b", I)),
    ("span", re.compile(r"\b(?:на|в\s+течение)\s+(?:(\d+|один|одну|два|две|три|четыре|пять|шесть)\s+)?"
                        r"(недел[юиь]\w*|месяц\w*|полгода|год\w*)\b", I)),
)


@dataclass
class Repeat:
    days: list[int]
    until: date | None
    spans: list[tuple[int, int]]


def _day_of(word: str) -> int:
    word = word.lower()
    return next(i for stem, i in _DAY_STEMS if word.startswith(stem))


def _month_start(index: int, today: date, year: int | None = None) -> date:
    """1-е число месяца index (0 — январь): ближайшее впереди."""
    if year:
        return date(year, index + 1, 1)
    start = date(today.year, index + 1, 1)
    return start if start > today else date(today.year + 1, index + 1, 1)


def _until(text: str, today: date) -> tuple[date | None, tuple[int, int] | None]:
    for kind, regex in UNTIL_RES:
        m = regex.search(text)
        if not m:
            continue
        if kind == "year":
            return date(today.year, 12, 31), m.span()
        if kind == "month_end":
            return _month_start(today.month % 12, today) - timedelta(days=1), m.span()
        if kind == "end_of":
            index = _month_index(m.group(1))
            year = today.year if index + 1 >= today.month else today.year + 1
            return date(year + (index == 11), (index + 1) % 12 + 1, 1) - timedelta(days=1), m.span()
        if kind == "week":
            return week_bounds(today)[1], m.span()
        if kind == "day":
            try:
                day = date(int(m.group(3)) if m.group(3) else today.year, _month_index(m.group(2)) + 1, int(m.group(1)))
            except ValueError:
                continue
            if day < today and not m.group(3):
                day = day.replace(year=today.year + 1)
            return day, m.span()
        if kind == "month":
            return _month_start(_month_index(m.group(1)), today) - timedelta(days=1), m.span()
        if kind == "season":
            return _month_start(_SEASONS[m.group(1).lower()] - 1, today) - timedelta(days=1), m.span()
        n = int(m.group(1)) if m.group(1) and m.group(1).isdigit() else _COUNT.get((m.group(1) or "").lower(), 1)
        unit = m.group(2).lower()
        if unit.startswith("недел"):
            return today + timedelta(weeks=n) - timedelta(days=1), m.span()
        months = 6 if unit == "полгода" else 12 * n if unit.startswith("год") else n
        month = today.month - 1 + months
        year, month = today.year + month // 12, month % 12 + 1
        return date(year, month, min(today.day, 28)) - timedelta(days=1), m.span()
    return None, None


def parse_repeat(text: str, now: datetime) -> Repeat | None:
    """«Каждый вторник до конца года» → дни недели, последний день и куски фразы. Не повтор — None."""
    days: list[int] = []
    spans: list[tuple[int, int]] = []
    for regex in REPEAT_RES:
        for m in regex.finditer(text):
            days += [_day_of(w) for w in re.findall(rf"{_DAY_ONE}|{_DAY_MANY}", m["days"], I)]
            spans.append(m.span())
    for regex, group in ((REPEAT_WEEKDAYS_RE, [0, 1, 2, 3, 4]), (REPEAT_WEEKEND_RE, [5, 6]),
                         (REPEAT_DAILY_RE, [0, 1, 2, 3, 4, 5, 6])):
        if m := regex.search(text):
            days += group
            spans.append(m.span())
    if not days and parse_interval(text) and (m := WEEKDAY_RANGE_RE.search(text)):
        # «Учёба с понедельника по пятницу с 9 до 15» — каждую неделю эти дни. Без часов это период списка
        first, last = _day_of(m.group(1)), _day_of(m.group(2))
        days = [d % 7 for d in range(first, last + (7 if last < first else 0) + 1)]
        spans.append(m.span())
    if not days:
        return None
    until, until_span = _until(text, now.date())
    if until_span:
        spans.append(until_span)
    return Repeat(sorted(set(days)), until, spans)


def first_day(days: list[int], start: date, now: datetime, time: str | None) -> date:
    """Первый день повтора с start: сегодняшний — только если время ещё не прошло."""
    for shift in range(8):
        day = start + timedelta(days=shift)
        if day.weekday() not in days:
            continue
        if day == now.date() and time and time <= now.strftime("%H:%M"):
            continue
        return day
    return start


SERIES_ALL_RE = re.compile(
    r"\b(?:вообще|совсем|насовсем|целиком|полностью|навсегда|(?:вс[её]\s+)?(?:повтор\w*|серию)|за\s+все\s+(?:дни|недели)"
    r"|все\s+предстоящ\w+(?:\s+дни)?)\b", I)


def _with_repeat(text: str, now: datetime) -> Parse | None:
    """Фраза с повтором: разбираем без слов повтора и добавляем дни. None — повтор тут ни при чём."""
    rep = parse_repeat(text, now)
    if not rep:
        return None
    rest = " ".join(_cut(text, rep.spans).split()).strip(" ,.")
    if not rest:
        return None
    inner = parse(rest, now)
    if inner.intent == "unknown" and keywords(rest):
        inner = parse("запиши " + rest, now)  # «каждый понедельник бассейн»
    if inner.intent == "delete":
        inner.series_all = True                 # «удали планёрку по вторникам» — все дни повтора
        return inner
    if inner.intent != "create":
        return None
    start = max(inner.date or now.date(), now.date())
    inner.repeat_days, inner.until = rep.days, rep.until
    # Занятость сегодня подходит, пока не кончилась: «по будням работа с 9 до 18» в 10 утра — уже сегодня
    till = inner.end_time if inner.type == "busy" and inner.end_time and inner.end_time > (inner.time or "") else inner.time
    inner.date, inner.date_kind = first_day(rep.days, start, now, till), "exact"
    inner.vague_period = False
    return inner


# ── Неуверенный разбор: «Верно понял?» ───────────────

TYPO_WORDS = ["сегодня", "завтра", "послезавтра", "понедельник", "вторник", "среду", "четверг", "пятницу",
              "субботу", "воскресенье", "января", "февраля", "марта", "апреля", "июня", "июля", "августа",
              "сентября", "октября", "ноября", "декабря", "утра", "вечера"]


def _close(a: str, b: str) -> bool:
    """Одна опечатка: лишняя, пропущенная, заменённая буква или две соседние местами («зватра»)."""
    if a == b or abs(len(a) - len(b)) > 1:
        return False
    if len(a) == len(b):
        diff = [i for i in range(len(a)) if a[i] != b[i]]
        return len(diff) == 1 or (len(diff) == 2 and diff[1] == diff[0] + 1
                                  and a[diff[0]] == b[diff[1]] and a[diff[1]] == b[diff[0]])
    short, long = sorted((a, b), key=len)
    i = next((k for k in range(len(short)) if short[k] != long[k]), len(short))
    return short[i:] == long[i + 1:]


def fix_typos(text: str) -> tuple[str, list[dict]]:
    """«Зватра в 10» → «завтра в 10». Правим только слова, которых нет в словаре, и только на слова дат."""
    if _MORPH is None:
        return text, []
    fixes = []

    def fix(m: re.Match) -> str:
        word = m.group(0)
        lower = word.lower().replace("ё", "е")
        if len(lower) < 5 or _MORPH.word_is_known(lower):
            return word
        right = next((w for w in TYPO_WORDS if _close(lower, w)), None)
        if not right:
            return word
        fixes.append({"why": "typo", "word": word, "right": right})
        return right

    return re.sub(r"[А-Яа-яЁё]+", fix, text), fixes


# Расплывчатое время: угадываем и переспрашиваем
VAGUE_TIME = [
    (r"ближе\s+к\s+вечеру", "17:00"), (r"под\s+вечер", "18:00"), (r"после\s+работы", "18:00"),
    (r"после\s+обеда", "14:00"), (r"(?:до|перед)\s+обедом|до\s+обеда", "12:00"), (r"в\s+обед", "13:00"),
    (r"вечерком", "19:00"), (r"утречком|с\s+утра(?:\s+пораньше)?", "09:00"),
]
# Срок без даты: убираем из текста и переспрашиваем
VAGUE_WHEN_RE = re.compile(r"\b(?:попозже|позже|потом|скоро|на\s+днях|как-нибудь|когда-нибудь|в\s+ближайшее\s+время)\b", I)
HEDGE_RE = re.compile(r"\b(?:наверное|наверно|возможно|вроде\s+бы|вроде|кажется|примерно|может\s+быть)\b|^может\b|\bможет$", I)
OR_WHEN_RE = re.compile(rf"\s*\bили\s+(?:(?:в|во|на|к|ко)\s+)?(?:\d{{1,2}}(?:[:.]\d{{2}})?|{WEEKDAY_RE}|завтра|послезавтра|сегодня|\d{{1,2}}\s+{MONTH_RE})\b", I)
SELF_FIX_RE = re.compile(r"\s*,?\s*\b(?:а\s+нет|нет|точнее|вернее|ой)\b[,\s]+(?P<tail>(?:(?:в|во|на)\s+)?\S+(?:\s+\S+)?)$", I)


def _doubts(result: "Parse", now: datetime) -> None:
    """Что в разобранной фразе смущает: «или», «попозже», «наверное», «а нет, в 11», лишние числа."""
    body = result.text or ""
    if fix := SELF_FIX_RE.search(body):
        time, ambiguous, _, info = parse_when(fix["tail"], now)
        if not time:
            time, ambiguous = reply_time(fix["tail"])
        if time or info.date:
            result.time = time or result.time
            result.time_ambiguous = ambiguous if time else result.time_ambiguous
            result.date = info.date or result.date
            body = body[:fix.start()]
            result.doubt.append({"why": "fix"})
    if OR_WHEN_RE.search(body):
        body = OR_WHEN_RE.sub("", body)
        result.doubt.append({"why": "or"})
    for pattern, time in VAGUE_TIME:
        if m := re.search(rf"\b{pattern}\b", body, I):
            if not result.time:
                result.time, result.time_ambiguous = time, False
                if result.date_kind == "none":
                    result.date_kind = "exact"
            body = body[:m.start()] + " " + body[m.end():]
            result.doubt.append({"why": "vague", "word": m.group(0).lower(), "time": time})
            break
    if m := VAGUE_WHEN_RE.search(body):
        body = body[:m.start()] + " " + body[m.end():]
        result.doubt.append({"why": "vague", "word": m.group(0).lower()})
    if HEDGE_RE.search(body.strip(" ,.!?")):
        body = HEDGE_RE.sub(" ", body.strip(" ,.!?"))
        result.doubt.append({"why": "hedge"})
    body = " ".join(body.split()).strip(" ,.:;!—-")
    # Число, похожее на время, осталось в тексте («встречу 15 с Олегом»); «2 литра» — не в счёт
    if re.search(r"\b(?:[01]?\d|2[0-3])(?:[:.][0-5]\d)?\b(?!\s*(?:шт|кг|г\b|гр|л\b|литр|раз|минут|час|дн|недел|месяц|лет|год|%|руб|₽))",
                 body, I) or re.search(r"\bчто-(?:то|нибудь)\b", body, I):
        result.doubt.append({"why": "unclear"})
    result.text = capitalize(body) if body else None


def parse(raw: str, now: datetime) -> Parse:
    text, typos = fix_typos(strip_lead(" ".join(raw.strip().split())))
    if repeated := _with_repeat(text, now):
        return repeated
    result = Parse()

    # «Пока», «до свидания», «спокойной ночи» — целиком, как благодарность
    if re.fullmatch(r"(?:(?:ну|ладно|всё|все|ок)[\s,]+)*(?:пока(?:-пока)?|до\s+свидания|до\s+завтра|спокойной\s+ночи|бывай)"
                    r"(?:[\s,!.]+(?:стивенс|stevens))?[\s,!.)]*", text, I):
        result.intent = "thanks"
        return result

    # Благодарность: одна — thanks; с просьбой — выполняем просьбу (Р-124)
    thanks = THANKS_RE.search(text)
    if thanks:
        result.thanks = True
        rest = _cut(text, [thanks.span()]).strip(" ,.!")
        if not rest or re.fullmatch(r"(?:отлично|супер|класс|хорошо|ок|окей|понял|ясно|круто|да)[\s,.!)]*", rest, I):
            result.intent = "thanks"
            return result
        text = rest

    # «Работа с 9 до 18»: интервал вырезаем сразу, чтобы «до 18» не путало дату и время
    interval = parse_interval(text)
    if interval:
        text = " ".join(_cut(text, [interval[3]]).split()).strip(" ,.")
    busy_word = bool(BUSY_WORD_RE.search(text))

    lower = text.lower()
    time, ambiguous, time_spans, info = parse_when(text, now)
    if interval and not time:
        time, ambiguous = interval[0], interval[2]

    if HELP_RE.search(lower):
        result.intent = "help"
        return result

    # «32 октября», «31 ноября» — такого дня нет, переспрашиваем, а не молча берём сегодня
    if (bad := re.search(rf"\b(\d{{1,2}})\s+{MONTH_RE}\b", text, I)) and not info.date and not re.search(
            rf"\bдо\s+{bad.group(0)}", text, I):
        result.intent, result.text = "bad_date", bad.group(0)
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
        # «Убери планёрку вообще» — все предстоящие дни повтора
        if whole := SERIES_ALL_RE.search(text):
            result.series_all = True
            spans.append(whole.span())
        elif one := re.search(r"\bтолько\s+(?:(?:одн[уа]|один|эт[уоа]|этот|ближайш\w*|следующ\w*)\b)?", text, I):
            result.series_one = True
            spans.append(one.span())
        if group := GROUP_RE.search(text):
            result.group = True
            counts = {"обе": 2, "оба": 2, "обеих": 2, "обоих": 2, "две": 2, "два": 2, "три": 3, "четыре": 4, "пять": 5}
            result.group_count = next((counts[w] for w in group[0].lower().split() if w in counts), None)
            spans.append(group.span())
        rest = _cut(text, spans)
        result.keywords, result.required = search_keywords(rest, result.filter_type)
        result.ref = bool(REF_RE.search(rest)) or bool(DONE_RE.search(lower)) or result.group
        result.bulk = bool(BULK_RE.search(lower)) and not result.group
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

    # «Покажи задачи без срока», «что у меня без даты»
    if UNDATED_RE.search(lower) and not CREATE_VERB_RE.search(lower) and (LIST_RE.search(lower) or SEARCH_RE.search(lower)
                                                                         or re.match(r"^(?:какие|список|мои|все)\b", lower)):
        result.intent = "undated"
        return result

    # «Покажи заметки», «какие у меня идеи», «заметки про кофе»
    if notes := NOTES_LIST_RE.match(text):
        result.intent, result.filter_type = "notes", "note"
        result.keywords, result.required = search_keywords(notes["rest"], "note")
        return result

    # «Покажи повторы», «какие у меня регулярные дела», «что у меня повторяется»
    if REPEATS_LIST_RE.search(lower) and not CREATE_VERB_RE.search(lower):
        result.intent = "repeats"
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
        result.time = time  # «Я свободен в пятницу в 15?» — да или нет на это время
        if interval:  # «Я свободен завтра с 14 до 16?» — да или нет на весь промежуток
            result.duration = max(30, (minutes_of(interval[1]) - minutes_of(interval[0])) % (24 * 60))
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
        if re.search(r"\b(?:какие|какой|в\s+какой|в\s+какие)\s+(?:день|дни)\b", lower) and not info.date:
            result.free_mode = "day"  # «Какие дни свободны на следующей неделе» — сравнение дней, а не окна понедельника
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

    # «Какое у меня следующее дело?», «когда ближайшая встреча», «что у меня дальше»
    if NEXT_RE.search(lower) and not CREATE_VERB_RE.search(lower) and not info.date and not info.period:
        result.intent, result.ask, result.tense, result.next = "search", "when", "future", True
        result.filter_type = _filter_type(lower)
        keys, result.required = search_keywords(_cut(text, time_spans), result.filter_type)
        result.keywords = [k for k in keys if k.startswith("@") or k in result.required]  # «с Иваном» — да, «ближайшая» — нет
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
        if part := re.search(r"\b(утром|днём|днем|вечером)\b", lower):
            result.day_part = part.group(1).replace("днем", "днём")
        today = now.date()
        if info.period:
            result.period = info.period
        elif info.date:
            result.period = _day_period(info.date, today)
        elif bare := re.search(rf"\b{WEEKDAY_RE}\b", text, I):
            target = _weekday_index(bare.group(1))
            result.period = _day_period(today + timedelta(days=(target - today.weekday()) % 7), today)
        elif re.search(r"\bна\s+неделю\b|\bнеделе\b", lower):
            s, e = week_bounds(today)
            result.period = Period(s, e, "на этой неделе", "на этой неделе")
        else:
            result.period = _day_period(today, today)
        return result

    if (info.date or info.period) and not time and not _cut(text, info.spans).strip(" ,.!?"):
        result.intent, result.only_when = "list", True
        result.period = info.period or _day_period(info.date, now.date())
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
    if text.rstrip().endswith("?") and not (has_verb or has_need or is_meeting):
        return result  # вопрос без «напомни/запиши» — не создаём задачу «Сколько дел ?»
    # «Купить хлеб», «позвонить в банк» — голое дело без срока, как в списке покупок
    bare_todo = bool(re.match(r"^[а-яё]{3,}(?:ть|ться|чь|сти|зти)\s+\S", lower)) and len(keywords(text)) >= 2 \
        and not re.search(r"что-(?:нибудь|то|либо)|\bэто\b", lower)
    if not (has_verb or is_meeting or has_need or is_task_word or bare_todo or busy_word
            or ((info.date or info.period or time) and len(keywords(_cut(text, info.spans + time_spans))) >= 1)):
        return result  # unknown

    result.intent = "create"
    # Интервал «с … до …» без встречи и напоминания — занятость: пары, работа, учёба
    is_busy = busy_word or (bool(interval) and not is_meeting and not is_reminder)
    result.type = "busy" if is_busy else "meeting" if is_meeting else "reminder" if is_reminder else "task"
    if interval and result.type in ("busy", "meeting"):
        result.end_time = interval[1]
    result.time = time
    result.time_ambiguous = ambiguous
    result.date = info.date
    result.date_kind = info.kind if info.date else ("none" if result.type == "task" and not time else "exact")
    result.vague_period = bool(info.period) and not info.date
    if result.vague_period and result.type == "task" and not time and info.period.end >= now.date():
        # «На этой неделе позвонить бабушке» — задача со сроком до конца периода
        result.date, result.date_kind, result.vague_period = info.period.end, "deadline", False

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
    if result.type == "busy":
        # «Я занят: пары» → «Пары»; «занятость работа» → «Работа»; просто «я занят» — спросим, чем
        body = BUSY_WORD_RE.sub(" ", body)
        body = re.sub(r"^\s*(?:я|у\s+меня)\b|\b(?:я|буду|будут|будет|у\s+меня)\b", " ", body, flags=I)
        body = " ".join(body.split()).strip(" ,.:;!—-")
        if m := BUSY_PLACE_RE.match(body):  # «на работе» → «Работа», «на парах» → «Пары»
            body = BUSY_PLACES[m.group(1).lower()] + body[m.end():]
        word, _, tail = body.partition(" ")
        if word.lower() in BUSY_VERBS:  # «я работаю с 9 до 18» → «Работа», «учусь» → «Учёба»
            body = f"{BUSY_VERBS[word.lower()]} {tail}".strip()
    # Существительное встречи — в именительный падеж: «встречу с Иваном» → «Встреча с Иваном»
    first = body.split(" ", 1)
    if first and first[0].lower() in MEETING_VERBS and len(first) > 1 and re.match(r"(?:с|со)\s", first[1], I):
        first[0] = MEETING_VERBS[first[0].lower()]
    if first and first[0].lower() in MEETING_NOUNS:
        noun = MEETING_NOUNS[first[0].lower()]
        rest = first[1] if len(first) > 1 else ""
        # «Планёрка», «совещание», «демо» понятны и без участника; «встреча» и «созвон» — нет
        result.has_subject = bool(keywords(rest)) or noun in ("Планёрка", "Совещание", "Демо")
        body = f"{noun} {rest}".strip()
    elif result.type == "meeting":
        result.has_subject = bool(keywords(body))
    body = body.strip(" ,.:;!—-")
    # «Завтра в 9 с Петей» — только с кем, без дела: это встреча
    who = re.fullmatch(r"(?:с|со)\s+([А-ЯЁA-Zа-яё][\w-]*)(?:\s+(?:и|или)\s+[А-ЯЁA-Zа-яё][\w-]*)*", body, I)
    if who and result.type == "task" and word_key(who.group(1)).startswith("@"):
        result.type, result.has_subject = "meeting", True
        body = f"Встреча {body}"
    result.text = capitalize(body) if body else None
    _doubts(result, now)
    result.doubt = typos + result.doubt
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
    r"^(?:слушай(?:те)?|слышь|стивенс|стивен|стив[еи]?[сз]|стифс|стив|stevens|steves?|ну|а|так|эй|окей|ок|короче|кстати|вот|типа|значит"
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
    r"|\s*,?\s+(?:а|и)\s+(?=(?:в|во|к)\s+\d|(?:завтра|послезавтра|сегодня|через)\b|(?:в|во)\s+" + WEEKDAY_RE.replace("(", "(?:", 1) + r")",
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
        result = _target(re.sub(r"^(?:из|с)\s+", "", m["target"] or "", flags=I), now)
        result.new_type = next(v for k, v in TYPE_BY_STEM.items() if m["type"].lower().startswith(k))
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
