"""Provider contracts and normalized tour models for tour-price-watch."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Protocol


@dataclass
class ProviderStatus:
    provider: str
    status: str
    active: bool
    message: str
    checked_at: str | None = None


@dataclass
class TourOffer:
    provider: str
    operator: str | None
    hotel_id: str | None
    canonical_hotel_id: str | None
    hotel_name: str | None
    resort: str | None
    subregion: str | None
    hotel_category: str | None
    departure_date: str | None
    return_date: str | None
    nights: int | None
    adults: int | None
    children_ages: list[int]
    meal: str | None
    room: str | None
    flight_out: str | None
    flight_back: str | None
    direct_flight: bool | None
    package_type: str | None
    quoted_price: int | None
    actualized_price: int | None
    currency: str | None
    availability: str | None
    deeplink: str | None
    tour_id: str | None
    observed_at: str
    actualized_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TourProvider(Protocol):
    provider_name: str

    def capabilities(self) -> dict[str, Any]:
        ...

    def healthcheck(self) -> ProviderStatus:
        ...

    def resolve_departure(self, name: str) -> dict[str, Any] | None:
        ...

    def resolve_country(self, name: str, departure_id: str | int | None = None) -> dict[str, Any] | None:
        ...

    def resolve_region(self, country_id: str | int, name: str) -> dict[str, Any] | None:
        ...

    def resolve_hotels(self, country_id: str | int, region_id: str | int | None, query: str | None = None) -> list[dict[str, Any]]:
        ...

    def list_operators(self, departure_id: str | int | None, country_id: str | int | None) -> list[dict[str, Any]]:
        ...

    def search(self, watch: dict[str, Any], exhaustive: bool = False) -> list[TourOffer]:
        ...

    def actualize(self, offer: TourOffer) -> TourOffer:
        ...

    def get_deeplink(self, offer: TourOffer) -> str | None:
        ...
