import math
from typing import Any

import httpx

from fuellayer.core.config import settings
from fuellayer.modules.onboarding.schemas import (
    CoordinateStoreSearch,
    NearbyStore,
    StoreSearchRequest,
    StoreSearchResponse,
    TextStoreSearch,
)

GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places"
STORE_TYPES = ["grocery_store", "supermarket", "hypermarket", "discount_supermarket"]
STORE_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.location,places.addressComponents"
)


class StoreProviderUnavailableError(RuntimeError):
    pass


def _distance_meters(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> int:
    radius = 6_371_000
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    delta_phi = math.radians(lat_b - lat_a)
    delta_lng = math.radians(lng_b - lng_a)
    value = (
        math.sin(delta_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(delta_lng / 2) ** 2
    )
    return round(radius * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value)))


def _country_code(place: dict[str, Any]) -> str | None:
    for component in place.get("addressComponents", []):
        if "country" in component.get("types", []):
            code = component.get("shortText")
            return code.upper() if isinstance(code, str) else None
    return None


def _parse_stores(
    payload: dict[str, Any], origin: tuple[float, float] | None
) -> tuple[list[NearbyStore], str | None]:
    stores: list[NearbyStore] = []
    detected_country: str | None = None
    for place in payload.get("places", []):
        place_id = place.get("id")
        display_name = place.get("displayName", {}).get("text")
        if not isinstance(place_id, str) or not isinstance(display_name, str):
            continue
        location = place.get("location", {})
        distance = None
        if (
            origin
            and isinstance(location.get("latitude"), int | float)
            and isinstance(location.get("longitude"), int | float)
        ):
            distance = _distance_meters(
                origin[0], origin[1], location["latitude"], location["longitude"]
            )
        detected_country = detected_country or _country_code(place)
        stores.append(
            NearbyStore(
                place_id=place_id,
                name=display_name,
                address=str(place.get("formattedAddress", "")),
                distance_meters=distance,
            )
        )
    stores.sort(key=lambda store: store.distance_meters if store.distance_meters is not None else 0)
    return stores[:10], detected_country


async def _google_request(path: str, body: dict[str, Any]) -> dict[str, Any]:
    if not settings.google_places_api_key:
        raise StoreProviderUnavailableError("Store search is not configured yet.")
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_places_api_key,
        "X-Goog-FieldMask": STORE_FIELD_MASK,
    }
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            response = await client.post(f"{GOOGLE_PLACES_URL}:{path}", json=body, headers=headers)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise StoreProviderUnavailableError("The store provider returned invalid data.")
        return payload
    except (httpx.HTTPError, ValueError) as exc:
        raise StoreProviderUnavailableError("Nearby stores are temporarily unavailable.") from exc


async def search_stores(request: StoreSearchRequest) -> StoreSearchResponse:
    if isinstance(request, CoordinateStoreSearch):
        stores: list[NearbyStore] = []
        country_code = request.country_code.upper() if request.country_code else None
        for radius in (5_000, 15_000, 30_000):
            payload = await _google_request(
                "searchNearby",
                {
                    "includedTypes": STORE_TYPES,
                    "maxResultCount": 10,
                    "rankPreference": "DISTANCE",
                    "languageCode": request.locale,
                    "locationRestriction": {
                        "circle": {
                            "center": {
                                "latitude": request.latitude,
                                "longitude": request.longitude,
                            },
                            "radius": radius,
                        }
                    },
                },
            )
            stores, detected_country = _parse_stores(payload, (request.latitude, request.longitude))
            country_code = country_code or detected_country
            if len(stores) >= 3 or radius == 30_000:
                break
        return StoreSearchResponse(
            area_label=request.area_label or "Nearby stores",
            country_code=country_code,
            stores=stores,
        )

    assert isinstance(request, TextStoreSearch)
    payload = await _google_request(
        "searchText",
        {
            "textQuery": f"grocery stores near {request.query}",
            "includedType": "grocery_store",
            "maxResultCount": 10,
            "languageCode": request.locale,
        },
    )
    stores, country_code = _parse_stores(payload, None)
    return StoreSearchResponse(
        area_label=request.query,
        country_code=country_code,
        stores=stores,
    )
