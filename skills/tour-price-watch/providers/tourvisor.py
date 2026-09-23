"""Tourvisor Search API provider.

Official docs: https://api.tourvisor.ru/search/docs
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from base import ProviderStatus, TourOffer


BASE_URL = "https://api.tourvisor.ru/search/api/v1"
CACHE_TTL_SECONDS = 24 * 60 * 60


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm(text: str | None) -> str:
    return (text or "").casefold().replace("ё", "е").strip()


class TourvisorProvider:
    provider_name = "tourvisor"

    def __init__(self, token: str | None, cache_dir: Path, usage_callback=None) -> None:
        self.token = token
        self.cache_dir = cache_dir
        self.usage_callback = usage_callback

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "role": "primary",
            "requires_token": True,
            "official_api": True,
            "supports_market_search": True,
            "supports_actualization": True,
            "supports_references": True,
            "scraping": False,
        }

    def _request(self, path: str, params: dict[str, Any] | None = None, billable: bool = False) -> Any:
        if not self.token:
            raise PermissionError("TOURVISOR_TOKEN is not configured")
        url = f"{BASE_URL}{path}"
        if params:
            url += "?" + urlencode(params, doseq=True)
        req = Request(
            url,
            headers={
                "Authorization": "Bearer " + self.token,
                "Accept": "application/json",
                "User-Agent": "gromik-tour-price-watch/0.1",
            },
        )
        try:
            with urlopen(req, timeout=45) as response:
                raw = response.read().decode("utf-8", errors="replace")
                if billable and self.usage_callback:
                    self.usage_callback(self.provider_name, "billable", path)
                return json.loads(raw)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise PermissionError(f"Tourvisor authorization failed: HTTP {exc.code}") from exc
            raise
        except URLError:
            raise

    def _cached(self, name: str, loader) -> Any:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"tourvisor_{name}.json"
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            return json.loads(path.read_text(encoding="utf-8"))
        data = loader()
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return data

    def healthcheck(self) -> ProviderStatus:
        if not self.token:
            return ProviderStatus(self.provider_name, "ACCESS_REQUIRED", False, "TOURVISOR_TOKEN is missing", now_iso())
        try:
            self._request("/departures", {"departureCountryId": 1})
            return ProviderStatus(self.provider_name, "ACTIVE", True, "Tourvisor Search API auth works", now_iso())
        except PermissionError as exc:
            return ProviderStatus(self.provider_name, "AUTH_REQUIRED", False, str(exc), now_iso())
        except Exception as exc:
            return ProviderStatus(self.provider_name, "ERROR", False, f"{type(exc).__name__}: {exc}", now_iso())

    def departures(self) -> list[dict[str, Any]]:
        return self._cached("departures_ru", lambda: self._request("/departures", {"departureCountryId": 1}))

    def countries(self, departure_id: int | None = None, only_direct: bool | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if departure_id is not None:
            params["departureId"] = departure_id
        if only_direct is not None:
            params["onlyDirect"] = str(only_direct).lower()
        key = "countries_" + "_".join(f"{k}-{v}" for k, v in sorted(params.items())) if params else "countries"
        return self._cached(key, lambda: self._request("/countries", params))

    def regions(self, country_id: int) -> list[dict[str, Any]]:
        return self._cached(f"regions_{country_id}", lambda: self._request("/regions", {"countryId": country_id}))

    def subregions(self, country_id: int, region_id: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"countryId": country_id}
        if region_id is not None:
            params["regionId"] = region_id
        key = f"subregions_{country_id}_{region_id or 'all'}"
        return self._cached(key, lambda: self._request("/subregions", params))

    def hotels(self, country_id: int, region_id: int | None = None, query: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        found: list[dict[str, Any]] = []
        page = 1
        while len(found) < limit:
            params: dict[str, Any] = {"countryId": country_id, "page": page, "limit": min(100, limit - len(found))}
            if region_id is not None:
                params["regionId"] = region_id
            rows = self._request("/hotels", params)
            if not rows:
                break
            found.extend(rows)
            if len(rows) < params["limit"]:
                break
            page += 1
        if query:
            q = norm(query)
            found = [hotel for hotel in found if q in norm(hotel.get("name"))]
        return found

    def resolve_departure(self, name: str) -> dict[str, Any] | None:
        q = norm(name)
        for item in self.departures():
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        return None

    def resolve_country(self, name: str, departure_id: str | int | None = None) -> dict[str, Any] | None:
        q = norm(name)
        dep = int(departure_id) if departure_id else None
        for item in self.countries(dep):
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        return None

    def resolve_region(self, country_id: str | int, name: str) -> dict[str, Any] | None:
        q = norm(name)
        for item in self.regions(int(country_id)):
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        for item in self.subregions(int(country_id)):
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        return None

    def resolve_hotels(self, country_id: str | int, region_id: str | int | None, query: str | None = None) -> list[dict[str, Any]]:
        return self.hotels(int(country_id), int(region_id) if region_id else None, query)

    def list_operators(self, departure_id: str | int | None, country_id: str | int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if departure_id is not None:
            params["departureId"] = int(departure_id)
        if country_id is not None:
            params["countryId"] = int(country_id)
        key = "operators_" + "_".join(f"{k}-{v}" for k, v in sorted(params.items())) if params else "operators"
        return self._cached(key, lambda: self._request("/operators", params))

    def search(self, watch: dict[str, Any], exhaustive: bool = False) -> list[TourOffer]:
        departure_id = watch.get("provider_refs", {}).get("tourvisor", {}).get("departure_id")
        country_id = watch.get("provider_refs", {}).get("tourvisor", {}).get("country_id")
        region_id = watch.get("provider_refs", {}).get("tourvisor", {}).get("region_id")
        if not departure_id or not country_id:
            raise ValueError("Tourvisor departure/country ids are not resolved")
        params: dict[str, Any] = {
            "departureId": int(departure_id),
            "countryId": int(country_id),
            "dateFrom": watch["departure_date_from"],
            "dateTo": watch["departure_date_to"],
            "nightsFrom": int(watch["nights_min"]),
            "nightsTo": int(watch["nights_max"]),
            "adults": int(watch["adults"]),
            "currency": watch.get("currency", "RUB").upper(),
            "onlyCharter": str(bool(watch.get("charter_only", False))).lower(),
            "onlyDirect": str(bool(watch.get("direct_flight_only", False))).lower(),
        }
        if watch.get("children_ages"):
            params["childs"] = [int(x) for x in watch["children_ages"]]
        if region_id:
            params["regionIds"] = [int(region_id)]
        if watch.get("hotel_ids"):
            params["hotelIds"] = [int(x) for x in watch["hotel_ids"]]
        search = self._request("/tours/search", params, billable=True)
        search_id = search.get("searchId")
        if not search_id:
            return []
        time.sleep(3)
        data = self._request(f"/tours/search/{search_id}", {"limit": 100})
        return self._normalize_results(data, watch)

    def _normalize_results(self, data: Any, watch: dict[str, Any]) -> list[TourOffer]:
        offers: list[TourOffer] = []
        if not isinstance(data, list):
            return offers
        for hotel_group in data:
            hotel = hotel_group.get("hotel") if isinstance(hotel_group, dict) else None
            tours = hotel_group.get("tours") if isinstance(hotel_group, dict) else None
            if not isinstance(tours, list):
                continue
            for item in tours:
                if not isinstance(item, dict):
                    continue
                price = item.get("price")
                offers.append(
                    TourOffer(
                        provider=self.provider_name,
                        operator=(item.get("operator") or {}).get("russianName") or (item.get("operator") or {}).get("name"),
                        hotel_id=str((hotel or item.get("hotel") or {}).get("id") or "") or None,
                        canonical_hotel_id=None,
                        hotel_name=(hotel or item.get("hotel") or {}).get("name") or item.get("name"),
                        resort=((hotel or {}).get("region") or {}).get("name"),
                        subregion=((hotel or {}).get("subRegion") or {}).get("name"),
                        hotel_category=str((hotel or {}).get("category") or "") or None,
                        departure_date=item.get("date"),
                        return_date=None,
                        nights=item.get("nights"),
                        adults=watch.get("adults"),
                        children_ages=watch.get("children_ages") or [],
                        meal=(item.get("meal") or {}).get("russianName") or (item.get("meal") or {}).get("name"),
                        room=item.get("roomType") or item.get("placement"),
                        flight_out=None,
                        flight_back=None,
                        direct_flight=watch.get("direct_flight_only"),
                        package_type=watch.get("package_type", "full_package"),
                        quoted_price=int(price) if price is not None else None,
                        actualized_price=None,
                        currency=item.get("currency") or watch.get("currency"),
                        availability=None,
                        deeplink=item.get("operatorLink") or item.get("hotelDescriptionLink"),
                        tour_id=str(item.get("id") or "") or None,
                        observed_at=now_iso(),
                    )
                )
        return offers

    def actualize(self, offer: TourOffer) -> TourOffer:
        if not offer.tour_id:
            return offer
        data = self._request(f"/tours/{offer.tour_id}/flights", {"currency": offer.currency or "RUB"}, billable=True)
        if isinstance(data, dict):
            price = data.get("price") or data.get("totalPrice")
            if price:
                offer.actualized_price = int(price)
                offer.actualized_at = now_iso()
        return offer

    def get_deeplink(self, offer: TourOffer) -> str | None:
        return offer.deeplink
