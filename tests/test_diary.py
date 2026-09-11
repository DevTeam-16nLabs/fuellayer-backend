import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from fuellayer.modules.diary.models import DiaryDay, DiaryRecord, DiaryRevision
from fuellayer.modules.onboarding.models import NutritionTargetHistory, User
from test_preferences import ALICE, protected
from test_preferences import setup as setup  # noqa: F401

PATH = "/api/v1/me/diary"


def entry(id="food-1", calories=110):
    return {
        "id": id,
        "name": "Banana",
        "slot": "Snack",
        "calories": calories,
        "macros": None,
        "servings": 1.25,
        "foodSnapshot": {
            "amount": 125,
            "food": {
                "id": "ciqual:banana",
                "name_fr": "Banane",
                "name_en": "Banana",
                "source": "ciqual",
                "source_version": "2025",
                "source_url": "",
                "reference_amount": 100,
                "reference_unit": "g",
                "nutrient_notes": {},
                "nutrients": {
                    "calories_kcal": 88,
                    "protein_g": 1,
                    "carbohydrates_g": 20,
                    "fat_g": None,
                },
            },
        },
    }


async def mutate(
    client, action="put", revision=0, data=None, day="2026-09-08", key=None, headers=ALICE, **extra
):
    data = data or entry()
    body = {
        "action": action,
        "expected_revision": revision,
        "entry_id": data["id"],
        "diary_date": day,
        "timezone": "Africa/Dakar",
        "offset_minutes": 0,
        **extra,
    }
    if action != "delete":
        body["entry"] = data
    return await client.post(
        PATH + "/mutations",
        json=body,
        headers={**headers, "Idempotency-Key": key or str(uuid.uuid4())},
    )


@pytest.mark.asyncio
async def test_history_crud_snapshot_retry_undo_no_side_effects(setup):
    client, factory = setup
    before = await protected(factory)
    r = await mutate(client, key="create-unique")
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    assert (await mutate(client, key="create-unique")).json() == r.json()
    assert (await mutate(client, key="create-unique", data=entry(calories=115))).status_code == 409
    assert r.json()["record"]["diary_date"] == "2026-09-08"
    modified = entry(calories=220)
    modified["foodSnapshot"]["amount"] = 250
    modified["servings"] = 2.5
    edited = await mutate(client, revision=1, data=modified)
    assert edited.status_code == 200, edited.text
    assert (await mutate(client, revision=1)).json()["detail"]["current"]["revision"] == 2
    assert (await mutate(client, action="delete", revision=2)).json()["record"]["deleted"] is True
    assert (await mutate(client, revision=3)).status_code == 409
    assert (await mutate(client, action="restore", revision=3, data=modified)).json()["record"][
        "revision"
    ] == 4
    async with factory() as db:
        history = (await db.scalars(select(DiaryRevision).order_by(DiaryRevision.revision))).all()
        assert len(history) == 4
        assert history[0].record["entry"]["foodSnapshot"]["amount"] == 125
        assert history[-1].record["entry"]["foodSnapshot"]["amount"] == 250
    assert before == await protected(factory)


@pytest.mark.asyncio
async def test_auth_ownership_and_cross_account_ids(setup):
    client, _ = setup
    assert (await client.get(PATH + "/range?start=2026-09-07&end=2026-09-13")).status_code == 401
    await mutate(client)
    bob = {"x-test-user": "bob"}
    assert (await mutate(client, action="delete", revision=1, headers=bob)).status_code == 409
    assert (await client.get(PATH + "/changes", headers=bob)).json()["records"] == []
    assert (await mutate(client, headers=bob, data=entry(calories=500))).status_code == 200
    assert (await client.get(PATH + "/changes", headers=ALICE)).json()["records"][0]["entry"][
        "calories"
    ] == 110
    assert (await mutate(client, user_id="bob")).status_code == 422


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "day", ["2026-02-30", "2026-13-01", "2026-9-8", "2026-09-08T23:00:00Z", "9999-12-31"]
)
async def test_invalid_dates(setup, day):
    client, _ = setup
    assert (await mutate(client, day=day)).status_code == 422


@pytest.mark.asyncio
async def test_validation(setup):
    client, _ = setup
    for field, value in [
        ("calories", -1),
        ("calories", 20001),
        ("servings", 0),
        ("servings", 10001),
        ("calories", True),
        ("name", " "),
    ]:
        data = entry()
        data[field] = value
        assert (await mutate(client, data=data)).status_code == 422
    data = entry()
    data["foodSnapshot"]["food"]["nutrients"]["fat_g"] = -1
    assert (await mutate(client, data=data)).status_code == 422
    assert (await mutate(client, timezone="Mars/Base")).status_code == 422
    assert (
        await client.get(PATH + "/range?start=2026-09-13&end=2026-09-07", headers=ALICE)
    ).status_code == 422


@pytest.mark.asyncio
async def test_migration_is_idempotent_preserves_ids_and_detects_collision(setup):
    client, factory = setup
    assert (
        await mutate(client, action="migrate", timezone=None, offset_minutes=None)
    ).status_code == 200
    assert (
        await mutate(client, action="migrate", timezone=None, offset_minutes=None)
    ).status_code == 200
    assert (await mutate(client, action="migrate", data=entry(calories=200))).status_code == 409
    assert (await mutate(client, action="migrate", day="2026-09-07")).status_code == 409
    async with factory() as db:
        assert await db.scalar(select(func.count()).select_from(DiaryRecord)) == 1
        assert await db.scalar(select(func.count()).select_from(DiaryRevision)) == 1
        assert (await db.scalar(select(DiaryDay))).timezone is None


@pytest.mark.asyncio
async def test_concurrency_and_change_cursor_tombstones(setup):
    client, _ = setup
    await mutate(client)
    results = await asyncio.gather(
        mutate(client, revision=1, data=entry(calories=120)),
        mutate(client, revision=1, data=entry(calories=130)),
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    results = await asyncio.gather(
        mutate(client, data=entry("second")), mutate(client, data=entry("third"))
    )
    assert all(r.status_code == 200 for r in results)
    a = (await client.get(PATH + "/changes?after=0&limit=1", headers=ALICE)).json()
    assert a["has_more"] and len(a["records"]) == 1
    b = (await client.get(PATH + f"/changes?after={a['cursor']}", headers=ALICE)).json()
    assert len(b["records"]) == 2
    await mutate(client, action="delete", revision=2)
    c = (await client.get(PATH + f"/changes?after={b['cursor']}", headers=ALICE)).json()
    assert len(c["records"]) == 1 and c["records"][0]["deleted"]


@pytest.mark.asyncio
async def test_week_missing_macros_zero_and_future(setup):
    client, _ = setup
    await mutate(client)
    zero = entry("zero", 0)
    zero.pop("foodSnapshot")
    zero["macros"] = {"protein_g": 0, "carbohydrates_g": 0, "fat_g": 0}
    await mutate(client, day="2026-09-07", data=zero)
    r = await client.get(PATH + "/weekly?start=2026-09-07&today=2026-09-09", headers=ALICE)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["averages"]["calories"] == {"sum": 110, "denominator": 2, "mean": 55}
    assert data["averages"]["fat_g"] == {"sum": 0, "denominator": 1, "mean": 0}
    assert data["days"][1]["missing"]["fat_g"] == 1 and data["days"][2]["state"] == "no_entries"
    assert all(d["state"] == "future" for d in data["days"][3:])
    assert (
        await client.get(PATH + "/weekly?start=2026-09-08&today=2026-09-09", headers=ALICE)
    ).status_code == 422
    assert (
        await client.get(PATH + "/weekly?start=2026-08-31&today=2026-09-09", headers=ALICE)
    ).json()["averages"]["calories"]["mean"] is None


@pytest.mark.asyncio
async def test_date_timezone_and_verified_targets(setup):
    client, factory = setup
    await mutate(client, timezone="Pacific/Auckland", offset_minutes=720)
    await mutate(client, revision=1, timezone="America/Los_Angeles", offset_minutes=-420)
    async with factory() as db:
        row = await db.scalar(select(DiaryRecord))
        assert row.diary_date.isoformat() == "2026-09-08" and row.timezone == "Pacific/Auckland"
        user = await db.scalar(select(User).where(User.clerk_subject == "alice"))
        target = {
            "daily_energy_kcal": 2200,
            "macros": {"protein_g": 140, "carbohydrates_g": 275, "fat_g": 60},
        }
        db.add(
            NutritionTargetHistory(
                user_id=user.id,
                revision=99,
                target=target,
                effective_at=datetime(2026, 9, 1, tzinfo=UTC),
                verified=False,
            )
        )
        await db.commit()
    r = (await client.get(PATH + "/range?start=2026-09-08&end=2026-09-08", headers=ALICE)).json()
    assert r["targets"]["2026-09-08"]["status"] == "unavailable"
    async with factory() as db:
        db.add(
            NutritionTargetHistory(
                user_id=user.id,
                revision=100,
                target=target,
                effective_at=datetime(2026, 9, 2, tzinfo=UTC),
                verified=True,
            )
        )
        await db.commit()
    r = (await client.get(PATH + "/range?start=2026-09-08&end=2026-09-08", headers=ALICE)).json()
    assert r["targets"]["2026-09-08"]["status"] == "available"
    assert r["targets"]["2026-09-08"]["timezone"] == "Pacific/Auckland"
    yesterday = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    assert (await mutate(client, day=yesterday, data=entry("midnight"))).status_code == 200


@pytest.mark.asyncio
async def test_existing_date_stays_editable_after_timezone_travel(setup, monkeypatch):
    from fuellayer.modules.diary import service

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 9, 9, 1, tzinfo=UTC)

    monkeypatch.setattr(service, "datetime", Clock)
    client, _ = setup
    assert (
        await mutate(client, day="2026-09-09", timezone="Pacific/Kiritimati", offset_minutes=840)
    ).status_code == 200
    edited = await mutate(
        client, day="2026-09-09", revision=1, timezone="Pacific/Honolulu", offset_minutes=-600
    )
    assert edited.status_code == 200, edited.text
    assert edited.json()["record"]["timezone"] == "Pacific/Kiritimati"
    assert (
        await mutate(
            client,
            day="2026-09-09",
            data=entry("new"),
            timezone="Pacific/Honolulu",
            offset_minutes=-600,
        )
    ).status_code == 422
    summary = (
        await client.get(PATH + "/weekly?start=2026-09-07&today=2026-09-08", headers=ALICE)
    ).json()
    assert (
        summary["days"][2]["state"] == "recorded" and summary["averages"]["calories"]["mean"] == 110
    )
