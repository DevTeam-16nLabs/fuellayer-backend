import copy
import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from fuellayer.core.auth import AuthSubject, require_auth_subject
from fuellayer.core.config import settings
from fuellayer.core.database import Base, get_session
from fuellayer.main import create_app
from fuellayer.modules.courses import receipts
from fuellayer.modules.courses.models import ReceiptJob
from fuellayer.modules.courses.schemas import ExtractedReceipt
from fuellayer.modules.courses.service import reconcile
from fuellayer.modules.onboarding.models import GroceryListRecord, User

A = {"x-test-user": "alice"}
B = {"x-test-user": "bob"}
IMAGE = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwC"
    "AAAAC0lEQVR42mP8/x8AAwMCAO+aFZkAAAAASUVORK5CYII="
)
FOOD = {
    "name": "Pois chiches",
    "quantity": 2,
    "unit": "boîtes",
    "category": "Légumineuses",
    "location": "pantry",
    "aisle": "Épicerie",
}


def extracted(**overrides: Any) -> ExtractedReceipt:
    line = {
        "raw": "YAOURT NATURE 4 pots 2,89",
        "kind": "food",
        "name": "Yaourt nature",
        "identity_confident": True,
        "quantity": 4,
        "unit": "pots",
        "quantity_evidence": "4 pots",
        "category": "Produits laitiers",
        "location": "fridge",
        "location_confident": True,
        "price": "2,89",
    }
    return ExtractedReceipt.model_validate(
        {
            "readable": True,
            "merchant": "Marché",
            "purchase_date": "2026-09-08",
            "receipt_number": "A001",
            "total": "8,90",
            "lines": [
                line,
                {
                    **line,
                    "raw": "TOMATES 3,45",
                    "name": "Tomates",
                    "quantity": None,
                    "unit": None,
                    "quantity_evidence": None,
                    "category": "Fruits & légumes",
                    "location_confident": False,
                },
                {
                    **line,
                    "raw": "BIO NAT 2,49",
                    "name": None,
                    "kind": "uncertain",
                    "identity_confident": False,
                    "quantity": None,
                    "unit": None,
                    "quantity_evidence": None,
                },
                {**line, "raw": "TVA", "kind": "tax"},
                {**line, "raw": "TOTAL", "kind": "total"},
                {**line, "raw": "EPONGE", "kind": "non_food"},
                {**line, "raw": "REMISE", "kind": "discount"},
            ],
            **overrides,
        }
    )


@pytest_asyncio.fixture
async def setup(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]]]:
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add_all(
            [
                User(
                    clerk_subject="alice",
                    grocery_list=GroceryListRecord(
                        grouped_items=[
                            {
                                "aisle": "Épicerie",
                                "items": [{"name": "Riz", "quantity": 1, "unit": "kg"}],
                            }
                        ],
                        content_version="v1",
                    ),
                ),
                User(clerk_subject="bob"),
            ]
        )
        await session.commit()
    app = create_app()

    async def auth(request: Request) -> AuthSubject:
        if not request.headers.get("x-test-user"):
            raise HTTPException(401)
        return AuthSubject(request.headers["x-test-user"])

    async def sessions() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    async def fake_extract(image: str) -> ExtractedReceipt:
        return extracted()

    monkeypatch.setattr(receipts, "session_factory", factory)
    monkeypatch.setattr(receipts, "extract", fake_extract)
    monkeypatch.setattr(settings, "openai_api_key", "test-only-not-a-real-key")
    monkeypatch.setattr(settings, "openrouter_api_key", None)
    app.dependency_overrides[require_auth_subject] = auth
    app.dependency_overrides[get_session] = sessions
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client, factory
    await engine.dispose()


async def state(client: httpx.AsyncClient, headers: dict[str, str] = A) -> dict[str, Any]:
    r = await client.get("/api/v1/courses", headers=headers)
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "private, no-store"
    return r.json()


async def action(client: httpx.AsyncClient, **values: Any) -> dict[str, Any]:
    current = await state(client)
    r = await client.post(
        "/api/v1/courses/actions",
        headers=A,
        json={"request_id": str(uuid.uuid4()), "expected_version": current["version"], **values},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def scan(client: httpx.AsyncClient, image: str = IMAGE) -> dict[str, Any]:
    r = await client.post(
        "/api/v1/courses/receipts",
        headers=A,
        json={"request_id": str(uuid.uuid4()), "image": image},
    )
    assert r.status_code == 202, r.text
    return await state(client)


@pytest.mark.asyncio
async def test_account_scope_and_unknown_quantity(setup: Any) -> None:
    client, _ = setup
    assert (await client.get("/api/v1/courses")).status_code == 401
    assert (
        await client.get("/api/v1/courses", headers={"x-test-user": "missing"})
    ).status_code == 404
    s = await action(client, action="add", food={"name": "Tomates"})
    assert s["items"][-1]["quantity"] is None
    assert s["lots"] == []
    assert (await state(client, B))["items"] == []
    r = await client.post(
        "/api/v1/courses/actions",
        headers=B,
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": 1,
            "action": "check",
            "item_id": s["items"][-1]["id"],
            "checked": True,
        },
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_purchases_survive_plan_replacement_and_do_not_become_stock(setup: Any) -> None:
    client, factory = setup
    s = await state(client)
    await action(client, action="check", item_id=s["items"][0]["id"], checked=True)
    await action(client, action="add", food={"name": "Manuel"})
    async with factory() as session:
        plan = await session.scalar(select(GroceryListRecord))
        plan.grouped_items = []
        plan.content_version = "v2"
        await session.commit()
    s = await state(client)
    assert len(s["items"]) == 2
    assert s["items"][0]["status"] == "purchased"
    assert s["lots"] == []


@pytest.mark.asyncio
async def test_transfer_retry_undo_and_actual_quantity(setup: Any) -> None:
    client, _ = setup
    s = await action(client, action="add", food=FOOD)
    item = s["items"][-1]
    s = await action(client, action="check", item_id=item["id"], checked=True)
    payload = {
        "request_id": str(uuid.uuid4()),
        "expected_version": s["version"],
        "action": "transfer",
        "lines": [{"item_id": item["id"], "food": {**FOOD, "quantity": 3}}],
    }
    first = await client.post("/api/v1/courses/actions", headers=A, json=payload)
    assert first.status_code == 200, first.text
    retry = await client.post("/api/v1/courses/actions", headers=A, json=payload)
    assert len(retry.json()["lots"]) == 1
    assert retry.json()["lots"][0]["quantity"] == 3
    assert retry.json()["lots"][0]["date_on"] is None
    undo = await action(client, action="undo_transfer", item_id=payload["request_id"])
    assert undo["items"][-1]["status"] == "purchased"
    assert undo["lots"][0]["status"] == "removed"
    # Replaying the original command after undo cannot recreate stock.
    replay = await client.post("/api/v1/courses/actions", headers=A, json=payload)
    assert replay.json()["lots"][0]["status"] == "removed"


@pytest.mark.asyncio
async def test_receipt_auto_add_exclusions_unknown_and_exact_duplicate(setup: Any) -> None:
    client, factory = setup
    s = await scan(client)
    assert len(s["lots"]) == 2
    tomato = next(row for row in s["lots"] if row["name"] == "Tomates")
    assert (
        tomato["quantity"] is None and tomato["unit"] is None and tomato["location"] == "unassigned"
    )
    assert all(row["date_on"] is None and row["storage_date"] is None for row in s["lots"])
    r = s["receipts"][0]
    assert [row["status"] for row in r["lines"]].count("review") == 1
    assert [row["status"] for row in r["lines"]].count("excluded") == 4
    again = await scan(client)
    assert len(again["lots"]) == 2 and len(again["receipts"]) == 1
    async with factory() as session:
        job = await session.scalar(select(ReceiptJob))
        assert job.image is None
    assert (await state(client, B))["receipts"] == []


@pytest.mark.asyncio
async def test_different_photo_same_receipt_and_undo_persists(setup: Any) -> None:
    client, _ = setup
    first = await scan(client)
    # Different bytes, identical extracted receipt identity.
    import base64

    other = (
        "data:image/png;base64,"
        + base64.b64encode(base64.b64decode(IMAGE.split(",")[1]) + b"other").decode()
    )
    second = await scan(client, other)
    assert len(second["lots"]) == 2
    assert second["receipts"][-1]["status"] == "duplicate"
    await action(client, action="undo_receipt", receipt_id=first["receipts"][0]["id"])
    again = await scan(client)
    assert all(row["status"] == "removed" for row in again["lots"])
    assert again["receipts"][0]["status"] == "undone"


@pytest.mark.asyncio
async def test_existing_purchase_requires_resolution_not_second_stock(setup: Any) -> None:
    client, _ = setup
    await client.post(
        "/api/v1/kitchen/items",
        headers=A,
        json={
            "request_id": str(uuid.uuid4()),
            "name": "Yaourt nature",
            "quantity": 4,
            "unit": "pots",
        },
    )
    s = await scan(client)
    yogurt = next(row for row in s["receipts"][0]["lines"] if row["name"] == "Yaourt nature")
    assert yogurt["status"] == "review" and yogurt["reason"] == "possible_duplicate"
    existing = next(row for row in s["lots"] if row["name"] == "Yaourt nature")
    s = await action(
        client,
        action="resolve",
        receipt_id=s["receipts"][0]["id"],
        line_id=yogurt["id"],
        food={"name": "Yaourt nature", "quantity": 4, "unit": "pots"},
        existing_lot_id=existing["id"],
    )
    assert len(s["lots"]) == 2
    s = await action(client, action="undo_receipt", receipt_id=s["receipts"][0]["id"])
    assert next(row for row in s["lots"] if row["id"] == existing["id"])["status"] == "active"


@pytest.mark.asyncio
async def test_separate_lot_explicit_choice_and_edit_blocks_undo(setup: Any) -> None:
    client, _ = setup
    s = await scan(client)
    r = s["receipts"][0]
    line = next(row for row in r["lines"] if row["status"] == "review")
    s = await action(
        client,
        action="resolve",
        receipt_id=r["id"],
        line_id=line["id"],
        food={"name": "Yaourt nature"},
        new_purchase=True,
    )
    assert len([row for row in s["lots"] if row["name"] == "Yaourt nature"]) == 2
    lot = s["lots"][0]
    edit = await client.patch(
        "/api/v1/kitchen/items/" + lot["id"],
        headers=A,
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": lot["version"],
            "name": lot["name"],
            "quantity": 2,
            "unit": "pots",
            "location": lot["location"],
            "storage_date": None,
            "category": "Produits laitiers",
        },
    )
    assert edit.status_code == 200
    response = await client.post(
        "/api/v1/courses/actions",
        headers=A,
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": s["version"],
            "action": "undo_receipt",
            "receipt_id": r["id"],
        },
    )
    assert response.status_code == 409
    assert all(row["status"] == "active" for row in (await state(client))["lots"])


@pytest.mark.asyncio
async def test_transfer_after_scan_must_link_or_explicitly_create(setup: Any) -> None:
    client, _ = setup
    s = await scan(client)
    lot = next(row for row in s["lots"] if row["name"] == "Yaourt nature")
    s = await action(
        client, action="add", food={"name": "Yaourt nature", "quantity": 4, "unit": "pots"}
    )
    item = s["items"][-1]
    s = await action(client, action="check", item_id=item["id"], checked=True)
    payload = {
        "request_id": str(uuid.uuid4()),
        "expected_version": s["version"],
        "action": "transfer",
        "lines": [
            {
                "item_id": item["id"],
                "food": {"name": "Yaourt nature", "quantity": 4, "unit": "pots"},
            }
        ],
    }
    assert (
        await client.post("/api/v1/courses/actions", headers=A, json=payload)
    ).status_code == 409
    payload["lines"][0]["existing_lot_id"] = lot["id"]
    result = await client.post("/api/v1/courses/actions", headers=A, json=payload)
    assert result.status_code == 200 and len(result.json()["lots"]) == 2


@pytest.mark.asyncio
async def test_invalid_transfer_rolls_back_entire_batch(setup: Any) -> None:
    client, _ = setup
    s = await action(client, action="add", food=FOOD)
    item = s["items"][-1]
    s = await action(client, action="check", item_id=item["id"], checked=True)
    r = await client.post(
        "/api/v1/courses/actions",
        headers=A,
        json={
            "request_id": str(uuid.uuid4()),
            "expected_version": s["version"],
            "action": "transfer",
            "lines": [
                {"item_id": item["id"], "food": FOOD},
                {"item_id": s["items"][0]["id"], "food": FOOD},
            ],
        },
    )
    assert r.status_code == 409
    assert (await state(client))["lots"] == []


@pytest.mark.asyncio
async def test_scan_unavailable_and_untrusted_input(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = setup
    monkeypatch.setattr(settings, "openai_api_key", None)
    assert not (await state(client))["scan_available"]
    r = await client.post(
        "/api/v1/courses/receipts",
        headers=A,
        json={"request_id": str(uuid.uuid4()), "image": IMAGE},
    )
    assert r.status_code == 503
    assert (await state(client))["receipts"] == []
    r = await client.post(
        "/api/v1/courses/receipts",
        headers=A,
        json={"request_id": str(uuid.uuid4()), "image": "https://internal.invalid/secret"},
    )
    assert r.status_code == 422


def test_price_cannot_supply_quantity_or_missing_evidence() -> None:
    result = extracted()
    result.lines[0].quantity = 2.89
    result.lines[0].unit = "pots"
    result.lines[0].quantity_evidence = "2,89"
    rows, _, _ = receipts.prepare(result)
    assert rows[0]["quantity"] is None and rows[0]["unit"] is None


def test_reconciliation_keeps_manual_edits_and_removed_suggestions() -> None:
    data = {"items": [], "plan_version": None}
    groups = [{"aisle": "Épicerie", "items": [{"name": "Riz", "quantity": 1, "unit": "kg"}]}]
    reconcile(data, groups, "v1")
    data["items"][0]["status"] = "removed"
    revised = copy.deepcopy(groups)
    revised[0]["items"][0]["quantity"] = 3
    reconcile(data, revised, "v2")
    assert data["items"][0]["status"] == "removed" and data["items"][0]["quantity"] == 1


def test_reordering_plan_preserves_checked_identity_without_reintroducing_it() -> None:
    data = {"items": [], "plan_version": None}
    groups = [
        {
            "aisle": "Épicerie",
            "items": [
                {"name": "Riz", "quantity": 1, "unit": "kg"},
                {"name": "Pâtes", "quantity": 2, "unit": "paquets"},
            ],
        }
    ]
    reconcile(data, groups, "v1")
    rice = data["items"][0]
    rice["status"] = "purchased"
    original_id = rice["id"]
    reordered = copy.deepcopy(groups)
    reordered[0]["items"].reverse()
    reconcile(data, reordered, "v2")
    rows = [item for item in data["items"] if item["name"] == "Riz"]
    assert len(rows) == 1
    assert rows[0]["id"] == original_id and rows[0]["status"] == "purchased"


@pytest.mark.asyncio
async def test_worker_rechecks_completion_after_waiting_for_account_lock(
    setup: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fuellayer.modules.courses.schemas import ReceiptUpload

    client, factory = setup
    job_id = uuid.uuid4()
    async with factory() as session:
        await receipts.upload(session, "alice", ReceiptUpload(request_id=job_id, image=IMAGE))
    original_get_user = receipts.get_user

    async def completed_while_waiting(session: AsyncSession, subject: str, **kwargs: Any) -> User:
        user = await original_get_user(session, subject, **kwargs)
        job = await session.get(ReceiptJob, job_id)
        assert job is not None
        # Simulate the state that another worker committed while the lock was awaited.
        job.status = "completed"
        job.image = None
        await session.commit()
        return user

    async def must_not_extract(image: str) -> ExtractedReceipt:
        pytest.fail("a completed receipt must never be analyzed again")

    monkeypatch.setattr(receipts, "get_user", completed_while_waiting)
    monkeypatch.setattr(receipts, "extract", must_not_extract)
    await receipts.process(job_id)
    async with factory() as session:
        job = await session.get(ReceiptJob, job_id)
        assert job is not None and job.status == "completed" and job.image is None


@pytest.mark.asyncio
async def test_receipt_does_not_readd_a_transferred_purchase_that_was_finished(setup: Any) -> None:
    from fuellayer.modules.kitchen.models import KitchenItem

    client, factory = setup
    food = {**FOOD, "name": "Yaourt nature", "quantity": 4, "unit": "pots"}
    s = await action(client, action="add", food=food)
    item_id = s["items"][-1]["id"]
    await action(client, action="check", item_id=item_id, checked=True)
    s = await action(client, action="transfer", lines=[{"item_id": item_id, "food": food}])
    lot_id = s["lots"][0]["id"]
    async with factory() as session:
        lot = await session.get(KitchenItem, uuid.UUID(lot_id))
        assert lot is not None
        lot.status = "finished"
        lot.version += 1
        await session.commit()
    s = await scan(client)
    receipt = s["receipts"][0]
    line = receipt["lines"][0]
    assert line["status"] == "review" and line["reason"] == "possible_duplicate"
    assert line["candidates"][0]["status"] == "finished"
    s = await action(
        client,
        action="resolve",
        receipt_id=receipt["id"],
        line_id=line["id"],
        food=food,
        existing_lot_id=lot_id,
    )
    yogurt = [lot for lot in s["lots"] if lot["name"] == "Yaourt nature"]
    assert len(yogurt) == 1 and yogurt[0]["status"] == "finished"
    assert s["receipts"][0]["lines"][0]["status"] == "linked"


@pytest.mark.asyncio
async def test_openrouter_only_configuration_enables_receipt_upload(
    setup: Any, monkeypatch
) -> None:
    client, _ = setup
    monkeypatch.setattr(settings, "openrouter_api_key", "test-only-router-key")
    monkeypatch.setattr(settings, "openai_api_key", None)
    s = await state(client)
    assert s["scan_available"] is True and s["scan_provider"] == "OpenRouter"
    s = await scan(client)
    assert s["receipts"][0]["status"] == "completed"
