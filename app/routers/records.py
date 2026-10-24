"""REST для календаря и правки — раздел 15.5 контракта v1.4."""
import re
from datetime import date, datetime, timezone

from fastapi import APIRouter, Body, Depends, Query
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import DateKind, Record, TaskStatus, TaskType
from app import series
from app.owner import Owner, get_owner
from app.textfmt import shift_end

router = APIRouter(prefix="/api/records", tags=["records"])

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


def invalid(code: str = "invalid_fields") -> JSONResponse:
    return JSONResponse(status_code=422, content={"error": code})


def not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"error": "not_found"})


def valid_date(value: str) -> bool:
    if not DATE_RE.match(value):
        return False
    try:
        date.fromisoformat(value)
        return True
    except ValueError:
        return False


def order_key(record: Record):
    return (record.date or "9999-99-99", record.time or "99:99", record.id)


async def owned(db: AsyncSession, owner: Owner, record_id: int) -> Record | None:
    record = await db.get(Record, record_id)
    return record if record and owner.owns(record) else None


@router.get("")
async def list_records(
    from_: str = Query(alias="from"),
    to: str = Query(),
    owner: Owner = Depends(get_owner),
    db: AsyncSession = Depends(get_db),
):
    if not valid_date(from_) or not valid_date(to) or from_ > to:
        return invalid("invalid_period")
    await series.extend(db, owner, date.today())
    rows = (await db.execute(
        select(Record).where(owner.filter(), Record.deleted_at.is_(None), Record.date.is_not(None),
                             Record.date >= from_, Record.date <= to)
    )).scalars().all()
    return {"records": [r.to_contract() for r in sorted(rows, key=order_key)]}


def apply_patch(record: Record, body: dict) -> str | None:
    """Меняет запись по правилам 15.5. Возвращает код ошибки или None."""
    allowed = {"text", "date", "time", "end_time", "status", "remind_before"}
    if not body or set(body) - allowed:
        return "invalid_fields"
    text = body.get("text", record.text)
    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 300:
        return "invalid_text"
    new_date = body["date"] if "date" in body else record.date
    new_time = body["time"] if "time" in body else record.time
    if new_date is not None and (not isinstance(new_date, str) or not valid_date(new_date)):
        return "invalid_date"
    if new_time is not None and (not isinstance(new_time, str) or not TIME_RE.match(new_time)):
        return "invalid_time"
    status = body.get("status")
    if "status" in body:
        if status not in ("open", "done") or record.type in (TaskType.meeting, TaskType.note, TaskType.busy):
            return "invalid_status"

    remind = body.get("remind_before")
    if "remind_before" in body and (record.type != TaskType.meeting or isinstance(remind, bool)
                                    or not isinstance(remind, int) or not 0 <= remind <= 1440):
        return "invalid_remind_before"  # только у встречи, целое 0–1440

    if record.type == TaskType.note and (new_date is not None or new_time is not None):
        return "invalid_date"  # у заметки нет даты и времени
    new_end = body["end_time"] if "end_time" in body else record.end_time
    if "end_time" not in body and new_end and record.time and isinstance(new_time, str) and TIME_RE.match(new_time):
        new_end = shift_end(record.time, record.end_time, new_time)  # сдвинули начало — длина та же
    if new_end is not None and (not isinstance(new_end, str) or not TIME_RE.match(new_end)):
        return "invalid_time"
    if record.type == TaskType.busy and (new_end is None or new_end == new_time):
        return "date_time_required"  # у занятости есть и начало, и конец
    kind = record.date_kind
    if record.type in (TaskType.meeting, TaskType.reminder, TaskType.busy):
        # У встречи и напоминания дата и время обязательны
        if new_date is None or new_time is None:
            return "date_time_required"
        kind = DateKind.exact
    else:
        if "date" in body and new_date is None:
            new_time, kind = None, DateKind.none          # без срока
        elif new_date is None and new_time is not None:
            return "time_without_date"
        elif new_date is not None and record.date_kind == DateKind.none:
            kind = DateKind.exact                         # получила дату
        # deadline с новой датой остаётся deadline

    record.text = text.strip()
    record.date, record.time, record.date_kind = new_date, new_time, kind
    if "remind_before" in body:
        record.remind_before = remind
    record.end_time = new_end if record.type in (TaskType.busy, TaskType.meeting) and new_time else None
    if "status" in body:
        record.status = TaskStatus(status)
    return None


@router.patch("/{record_id}")
async def patch_record(
    record_id: int,
    body: object = Body(default=None),
    owner: Owner = Depends(get_owner),
    db: AsyncSession = Depends(get_db),
):
    record = await owned(db, owner, record_id)
    if not record or record.deleted_at:
        return not_found()
    if not isinstance(body, dict):
        return invalid()
    problem = apply_patch(record, body)
    if problem:
        await db.rollback()
        return invalid(problem)
    await db.commit()
    return {"record": record.to_contract()}


@router.delete("/{record_id}", status_code=204)
async def delete_record(record_id: int, owner: Owner = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    record = await owned(db, owner, record_id)
    if not record:
        return not_found()
    if not record.deleted_at:
        record.deleted_at = datetime.now(timezone.utc)  # мягкое удаление
        await db.commit()
    return Response(status_code=204)


@router.post("/{record_id}/restore")
async def restore_record(record_id: int, owner: Owner = Depends(get_owner), db: AsyncSession = Depends(get_db)):
    record = await owned(db, owner, record_id)
    if not record:
        return not_found()
    record.deleted_at = None
    await db.commit()
    return {"record": record.to_contract()}
