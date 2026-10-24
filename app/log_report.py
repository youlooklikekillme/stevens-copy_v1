"""Отчёт по журналу чата: где Stevens не понял, что переспрашивал, что отменяли.

    docker compose exec backend python -m app.log_report          # за 7 дней
    docker compose exec backend python -m app.log_report 30       # за 30 дней
    docker compose exec backend python -m app.log_report 7 --csv  # все строки в CSV
"""
import asyncio
import csv
import sys

from sqlalchemy import text

from app.database import engine

TOP = 15


async def rows(conn, sql: str, days: int) -> list:
    return list((await conn.execute(text(sql), {"days": days})).all())


def section(title: str, items: list, fmt) -> None:
    print(f"\n── {title}")
    if not items:
        print("   нет")
    for item in items:
        print("   " + fmt(item))


async def report(days: int) -> None:
    since = "at > now() - make_interval(days => :days)"
    async with engine.connect() as conn:
        total, users, convs, voice, llm, errors, p95 = (await rows(conn, f"""
            SELECT count(*), count(DISTINCT who), count(DISTINCT conversation_id),
                   count(*) FILTER (WHERE source = 'voice'), count(*) FILTER (WHERE llm),
                   count(*) FILTER (WHERE error IS NOT NULL),
                   coalesce(percentile_cont(0.95) WITHIN GROUP (ORDER BY ms), 0)
            FROM chat_log WHERE {since}""", days))[0]
        print(f"Журнал за {days} дн.: запросов {total}, людей {users}, диалогов {convs}")
        if not total:
            return
        print(f"Голосом {voice * 100 // total}%, через LLM {llm}, ошибок {errors}, 95% ответов быстрее {int(p95)} мс")

        section("Ответы по видам", await rows(conn, f"""
            SELECT kind, count(*) FROM chat_log WHERE {since} GROUP BY kind ORDER BY 2 DESC""", days),
            lambda r: f"{r[1]:>5}  {r[0]}")
        section("Не понял — эти фразы стоит научить", await rows(conn, f"""
            SELECT text, count(*) FROM chat_log
            WHERE {since} AND template IN ('not_understood', 'clarify_generic') AND text <> ''
            GROUP BY text ORDER BY 2 DESC, max(at) DESC LIMIT {TOP}""", days),
            lambda r: f"{r[1]:>3}× «{r[0]}»")
        section("Ответ на уточнение не понят — Stevens переспросил", await rows(conn, f"""
            SELECT waiting, text, reply FROM chat_log
            WHERE {since} AND waiting IS NOT NULL AND kind = 'clarify' AND action IS NULL
              AND (SELECT CASE WHEN chat_log.waiting = 'multi_ask' THEN p.reply = chat_log.reply
                               ELSE p.template = chat_log.template END
                   FROM chat_log p WHERE p.conversation_id = chat_log.conversation_id
                   AND p.at < chat_log.at ORDER BY p.at DESC LIMIT 1)
            ORDER BY at DESC LIMIT {TOP}""", days),
            lambda r: f"[{r[0]}] «{r[1]}» → {r[2]}")
        section("Отменили сразу после ответа — вероятно, понял не так", await rows(conn, f"""
            SELECT prev_text, prev_reply FROM (
                SELECT action, lag(text) OVER w AS prev_text, lag(reply) OVER w AS prev_reply
                FROM chat_log WHERE {since}
                WINDOW w AS (PARTITION BY conversation_id ORDER BY at)) t
            WHERE action IN ('undo', 'cancel') AND prev_text <> ''
            ORDER BY 1 LIMIT {TOP}""", days),
            lambda r: f"«{r[0]}» → {r[1]}")
        section("Ошибки сервера", await rows(conn, f"""
            SELECT text, error FROM chat_log WHERE {since} AND error IS NOT NULL
            ORDER BY at DESC LIMIT {TOP}""", days),
            lambda r: f"«{r[0]}»: {r[1][:120]}")
        section("Сбои сайта по видам (микрофон, автослушание, озвучка, серверное распознавание)", await rows(conn, f"""
            SELECT event, count(*), max(detail) FROM client_log WHERE {since}
            GROUP BY event ORDER BY 2 DESC LIMIT {TOP}""", days),
            lambda r: f"{r[0]}: {r[1]} (например: {r[2] or '—'})")


async def export(days: int) -> None:
    async with engine.connect() as conn:
        result = await conn.execute(text(
            "SELECT at, who, conversation_id, source, text, raw_text, action, waiting, intent, llm, kind, template, "
            "reply, records, ms, error FROM chat_log WHERE at > now() - make_interval(days => :days) ORDER BY at"),
            {"days": days})
        out = csv.writer(sys.stdout)
        out.writerow(result.keys())
        out.writerows(result.all())


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    period = int(args[0]) if args else 7
    asyncio.run(export(period) if "--csv" in sys.argv else report(period))
