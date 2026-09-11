import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from fuellayer.modules.diary.models import DiaryDay, DiaryReceipt, DiaryRecord, DiaryRevision
from fuellayer.modules.diary.schemas import Mutation, calendar_date
from fuellayer.modules.onboarding.models import NutritionTargetHistory, User


def fail(message: str, status=422, code="diary_invalid", **extra):
    raise HTTPException(status, {"code": code, "message": message, **extra})


async def owner(db: AsyncSession, subject: str, write=False):
    # A database lock, not a process mutex: also serializes first-entry creation
    # and idempotency checks across workers. SQLite obtains a writer lock here.
    if write or db.bind.dialect.name == "sqlite":
        uid = await db.scalar(
            update(User)
            .where(User.clerk_subject == subject)
            .values(diary_revision=User.diary_revision)
            .returning(User.id)
        )
        if uid is None:
            fail("Account not found.", 404, "account_missing")
    user = await db.scalar(
        select(User)
        .where(User.clerk_subject == subject)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if user is None or user.onboarding_status != "completed":
        fail("Complete your profile before using the diary.", 404, "account_missing")
    return user


def serialize(row: DiaryRecord):
    return {
        "entry": copy.deepcopy(row.payload),
        "diary_date": row.diary_date.isoformat(),
        "revision": row.revision,
        "change_seq": row.change_seq,
        "deleted": row.deleted,
        "timezone": row.timezone,
        "offset_minutes": row.offset_minutes,
        "updated_at": row.updated_at.replace(tzinfo=UTC).isoformat(),
    }


def digest(body):
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


async def mutate(db: AsyncSession, subject: str, key: str, body: Mutation):
    user = await owner(db, subject, write=True)
    request_hash = digest(body.model_dump(mode="json"))
    receipt = await db.scalar(
        select(DiaryReceipt).where(DiaryReceipt.user_id == user.id, DiaryReceipt.request_id == key)
    )
    if receipt:
        if receipt.request_hash != request_hash:
            fail("This retry key was used for a different change.", 409, "key_reused")
        return receipt.response
    row = await db.scalar(
        select(DiaryRecord).where(
            DiaryRecord.user_id == user.id, DiaryRecord.entry_id == body.entry_id
        )
    )
    day = calendar_date(body.diary_date)
    now = datetime.now(UTC)
    # A saved date is never shifted by a timezone change. For offline/migration
    # records without a zone allow any date that has begun anywhere on Earth.
    today = (
        now.astimezone(ZoneInfo(body.timezone)).date()
        if body.timezone
        else (now + timedelta(hours=14)).date()
    )
    if row is None and day > today:
        fail("Diary entries cannot be recorded in the future.")
    payload = body.entry.model_dump(mode="json", exclude_none=True) if body.entry else None
    if payload is not None:
        # macros:null is a meaningful legacy representation, unlike absent optionals.
        payload["macros"] = body.entry.macros.model_dump() if body.entry.macros else None
        if body.entry.foodSnapshot:
            payload["foodSnapshot"]["food"]["nutrients"] = (
                body.entry.foodSnapshot.food.nutrients.model_dump()
            )
    same_import = (
        body.action == "migrate"
        and row
        and not row.deleted
        and row.diary_date == day
        and row.payload == payload
    )
    if not same_import:
        if row and (row.diary_date != day or row.revision != body.expected_revision):
            fail(
                "This entry changed on another device.",
                409,
                "entry_conflict",
                current=serialize(row),
            )
        if not row and body.expected_revision != 0:
            fail("This entry is no longer available.", 409, "entry_conflict", current=None)
        if body.action in ("delete", "restore") and row is None:
            fail("Entry not found.", 404, "entry_missing")
        if row and row.deleted and body.action in ("put", "migrate"):
            fail(
                "This entry was removed on another device.",
                409,
                "entry_conflict",
                current=serialize(row),
            )
        if body.action == "migrate" and row:
            fail(
                "An entry with this ID already exists.",
                409,
                "entry_conflict",
                current=serialize(row),
            )
        user.diary_revision += 1
        if row is None:
            row = DiaryRecord(
                user_id=user.id,
                entry_id=body.entry_id,
                diary_date=day,
                timezone=body.timezone,
                offset_minutes=body.offset_minutes,
                revision=0,
                change_seq=user.diary_revision,
                payload=payload,
                deleted=False,
            )
            db.add(row)
            if await db.get(DiaryDay, (user.id, day)) is None:
                db.add(DiaryDay(user_id=user.id, diary_date=day, timezone=body.timezone))
        if payload is not None:
            row.payload = payload
        row.deleted = body.action == "delete"
        row.revision += 1
        row.change_seq = user.diary_revision
        row.updated_at = now
        await db.flush()
        db.add(
            DiaryRevision(
                user_id=user.id, entry_id=row.entry_id, revision=row.revision, record=serialize(row)
            )
        )
    result = {"owner": subject, "record": serialize(row)}
    db.add(
        DiaryReceipt(user_id=user.id, request_id=key, request_hash=request_hash, response=result)
    )
    await db.commit()
    return result


def days_between(start: str, end: str):
    try:
        first, last = calendar_date(start), calendar_date(end)
    except ValueError as exc:
        fail(str(exc))
    if not 0 <= (last - first).days <= 92:
        fail("Request between 1 and 93 calendar days.")
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


async def target_for_dates(db, user_id, days):
    # Only prospectively verified events count. Legacy created_at baselines
    # deliberately cannot establish a historical daily target.
    rows = (
        await db.scalars(
            select(NutritionTargetHistory)
            .where(
                NutritionTargetHistory.user_id == user_id, NutritionTargetHistory.verified.is_(True)
            )
            .order_by(NutritionTargetHistory.effective_at)
        )
    ).all()
    result = {}
    for day in days:
        # UTC-day comparison is explicitly metadata, not a user-day score. We
        # withhold comparison until a zone for the historical day is available.
        result[day.isoformat()] = {"status": "unavailable", "events": []}
    return result, [
        {
            "effective_at": r.effective_at.replace(tzinfo=UTC).isoformat(),
            "revision": r.revision,
            "target": r.target,
        }
        for r in rows
    ]


async def read_range(db, subject, start, end):
    days = days_between(start, end)
    user = await owner(db, subject)
    rows = (
        await db.scalars(
            select(DiaryRecord)
            .where(
                DiaryRecord.user_id == user.id,
                DiaryRecord.diary_date >= days[0],
                DiaryRecord.diary_date <= days[-1],
            )
            .order_by(DiaryRecord.change_seq)
        )
    ).all()
    targets, events = await target_for_dates(db, user.id, days)
    for day in days:
        context = await db.get(DiaryDay, (user.id, day))
        if not context or not context.timezone:
            continue
        zone = ZoneInfo(context.timezone)
        beginning = datetime.combine(day, datetime.min.time(), tzinfo=zone).astimezone(UTC)
        ending = datetime.combine(
            day + timedelta(days=1), datetime.min.time(), tzinfo=zone
        ).astimezone(UTC)
        before = [e for e in events if datetime.fromisoformat(e["effective_at"]) <= beginning]
        during = [
            e for e in events if beginning < datetime.fromisoformat(e["effective_at"]) < ending
        ]
        if before:
            targets[day.isoformat()] = {
                "status": "changed" if during else "available",
                "events": [before[-1], *during],
                "timezone": str(zone),
            }
    return {
        "owner": subject,
        "records": [serialize(r) for r in rows],
        "covered_dates": [d.isoformat() for d in days],
        "targets": targets,
        "has_history": bool(
            await db.scalar(
                select(DiaryRecord.id)
                .where(DiaryRecord.user_id == user.id, DiaryRecord.deleted.is_(False))
                .limit(1)
            )
        ),
    }


async def changes(db, subject, after, limit):
    user = await owner(db, subject)
    if after > user.diary_revision:
        fail("Diary cursor is ahead of the account.", 409, "cursor_invalid")
    rows = (
        await db.scalars(
            select(DiaryRecord)
            .where(DiaryRecord.user_id == user.id, DiaryRecord.change_seq > after)
            .order_by(DiaryRecord.change_seq)
            .limit(limit + 1)
        )
    ).all()
    more = len(rows) > limit
    selected = rows[:limit]
    return {
        "owner": subject,
        "records": [serialize(r) for r in selected],
        "cursor": selected[-1].change_seq if more else user.diary_revision,
        "has_more": more,
    }


def totals(entries):
    values = dict(calories=0, protein_g=0, carbohydrates_g=0, fat_g=0)
    missing = dict(protein_g=0, carbohydrates_g=0, fat_g=0)
    for e in entries:
        values["calories"] += e["calories"]
        m = e["macros"]
        if m is None and e.get("foodSnapshot"):
            s = e["foodSnapshot"]
            m = {
                k: None if v is None else v * s["amount"] / s["food"]["reference_amount"]
                for k, v in s["food"]["nutrients"].items()
            }
        for k in missing:
            v = m.get(k) if m else None
            if v is None:
                missing[k] += 1
            else:
                values[k] += v
    return {"values": values, "missing": missing, "entry_count": len(entries)}


async def weekly(db, subject, start, today):
    first = calendar_date(start)
    if first.weekday() != 0:
        fail("A diary week starts on Monday.")
    current = calendar_date(today)
    data = await read_range(db, subject, start, (first + timedelta(days=6)).isoformat())
    days = []
    for value in data["covered_dates"]:
        entries = [
            r["entry"] for r in data["records"] if r["diary_date"] == value and not r["deleted"]
        ]
        t = totals(entries)
        days.append(
            {
                "date": value,
                "state": "recorded"
                if entries
                else "future"
                if value > current.isoformat()
                else "no_entries",
                **t,
            }
        )
    averages = {}
    for nutrient in ("calories", "protein_g", "carbohydrates_g", "fat_g"):
        valid = [d for d in days if d["state"] == "recorded" and not d["missing"].get(nutrient)]
        total = sum(d["values"][nutrient] for d in valid)
        averages[nutrient] = {
            "sum": total,
            "denominator": len(valid),
            "mean": total / len(valid) if valid else None,
        }
    return {"owner": subject, "days": days, "averages": averages, "targets": data["targets"]}
