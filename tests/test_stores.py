import pytest

from fuellayer.modules.onboarding import stores as stores_module
from fuellayer.modules.onboarding.schemas import CoordinateStoreSearch, TextStoreSearch


def place(index: int, latitude: float = 14.7, longitude: float = -17.4) -> dict[str, object]:
    return {
        "id": f"place-{index}",
        "displayName": {"text": f"Market {index}"},
        "formattedAddress": f"Address {index}",
        "location": {"latitude": latitude, "longitude": longitude},
        "addressComponents": [{"types": ["country"], "shortText": "SN"}],
    }


@pytest.mark.asyncio
async def test_coordinate_search_expands_radius_until_three_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radii: list[int] = []

    async def request_stub(path: str, body: dict[str, object]) -> dict[str, object]:
        assert path == "searchNearby"
        restriction = body["locationRestriction"]
        assert isinstance(restriction, dict)
        circle = restriction["circle"]
        assert isinstance(circle, dict)
        radii.append(int(circle["radius"]))
        count = 2 if len(radii) == 1 else 3
        return {"places": [place(index) for index in range(count)]}

    monkeypatch.setattr(stores_module, "_google_request", request_stub)
    result = await stores_module.search_stores(
        CoordinateStoreSearch(source="coordinates", latitude=14.7, longitude=-17.4)
    )

    assert radii == [5_000, 15_000]
    assert len(result.stores) == 3
    assert result.country_code == "SN"


@pytest.mark.asyncio
async def test_text_search_returns_only_ephemeral_store_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def request_stub(path: str, body: dict[str, object]) -> dict[str, object]:
        assert path == "searchText"
        assert body["textQuery"] == "grocery stores near Paris 11"
        return {"places": [place(1)]}

    monkeypatch.setattr(stores_module, "_google_request", request_stub)
    result = await stores_module.search_stores(TextStoreSearch(source="text", query="Paris 11"))

    assert result.area_label == "Paris 11"
    assert result.stores[0].place_id == "place-1"
