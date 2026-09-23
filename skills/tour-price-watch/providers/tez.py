"""TEZ TOUR public XML/JSON gate provider.

Official docs:
- https://www.tez-tour.com/article.html?id=7015574
- https://www.tez-tour.com/en/msk/article.html?id=7015574
"""

from __future__ import annotations

from datetime import date, datetime, timezone
import json
from pathlib import Path
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

from base import ProviderStatus, TourOffer


BASE_URL = "https://search.tez-tour.com/tariffsearch"
XML_BASE_URL = "https://xml.tez-tour.com/tariffsearch"
CACHE_TTL_SECONDS = 24 * 60 * 60
RETRY_STATUS_CODES = {429, 502, 503, 504}
TEMPORARY_URL_ERRORS = {
    "Connection refused",
    "Connection reset",
    "Connection aborted",
    "timed out",
    "Temporary failure",
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm(text: str | None) -> str:
    return (text or "").casefold().replace("ё", "е").strip()


def int_or(value: Any, fallback: int) -> int:
    if value is None or value == "":
        return fallback
    return int(value)


def date_ru(value: str) -> str:
    return datetime.fromisoformat(value).strftime("%d.%m.%Y")


def return_date(departure: str | None, nights: int | None) -> str | None:
    if not departure or nights is None:
        return None
    try:
        dt = datetime.strptime(departure, "%d.%m.%Y")
    except ValueError:
        return None
    return datetime.fromtimestamp((dt.timestamp() + nights * 86400)).strftime("%d.%m.%Y")


def sanitize_params(params: dict[str, Any] | None) -> dict[str, Any]:
    """Return request params that are safe to include in diagnostics."""
    result: dict[str, Any] = {}
    for key, value in sorted((params or {}).items()):
        if "token" in key.casefold() or "key" in key.casefold() or "password" in key.casefold():
            result[key] = "<redacted>"
        else:
            result[key] = value
    return result


class TezTemporaryError(RuntimeError):
    """Raised when TEZ is temporarily unavailable or rate-limiting."""

    def __init__(
        self,
        message: str,
        endpoint: str,
        params: dict[str, Any] | None,
        status_code: int | None = None,
        attempts: int = 1,
    ) -> None:
        super().__init__(message)
        self.endpoint = endpoint
        self.params = sanitize_params(params)
        self.status_code = status_code
        self.attempts = attempts


class TezTourProvider:
    provider_name = "tez"

    def __init__(self, cache_dir: Path, usage_callback=None, timeout_seconds: int = 20, max_attempts: int = 3) -> None:
        self.cache_dir = cache_dir
        self.usage_callback = usage_callback
        self._last_city_id: int | None = None
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.last_request: dict[str, Any] | None = None

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "role": "direct_cross_check",
            "requires_token": False,
            "official_api": True,
            "supports_market_search": True,
            "supports_actualization": False,
            "supports_references": True,
            "supports_children": True,
            "supports_links": True,
            "supports_direct_flight_filter": False,
            "scraping": False,
            "limits": {"requests_per_minute": 50, "date_range_days": 30, "nights_delta": 8},
        }

    def _request(self, path: str, params: dict[str, Any] | None = None, base_url: str = BASE_URL) -> Any:
        url = f"{base_url}{path}"
        if params:
            url += "?" + urlencode(params, doseq=True)
        safe_params = sanitize_params(params)
        self.last_request = {"base_url": base_url, "endpoint": path, "params": safe_params}
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urlopen(url, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                return json.loads(raw)
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:300]
                if exc.code in RETRY_STATUS_CODES:
                    last_error = exc
                    if attempt < self.max_attempts:
                        time.sleep(attempt)
                        continue
                    raise TezTemporaryError(
                        f"TEZ temporary HTTP {exc.code}: {body or exc.reason}",
                        endpoint=path,
                        params=safe_params,
                        status_code=exc.code,
                        attempts=attempt,
                    ) from exc
                raise RuntimeError(f"TEZ HTTP {exc.code} on {path}; params={safe_params}; body={body}") from exc
            except (URLError, TimeoutError, socket.timeout) as exc:
                text = str(exc)
                temporary = isinstance(exc, (TimeoutError, socket.timeout)) or any(part in text for part in TEMPORARY_URL_ERRORS)
                if temporary:
                    last_error = exc
                    if attempt < self.max_attempts:
                        time.sleep(attempt)
                        continue
                    raise TezTemporaryError(
                        f"TEZ temporary network error: {text}",
                        endpoint=path,
                        params=safe_params,
                        attempts=attempt,
                    ) from exc
                raise RuntimeError(f"TEZ network error on {path}; params={safe_params}; error={text}") from exc
        raise TezTemporaryError(
            f"TEZ temporary error after retries: {last_error}",
            endpoint=path,
            params=safe_params,
            attempts=self.max_attempts,
        )

    def _cached(self, name: str, loader) -> Any:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"tez_{name}.json"
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            return json.loads(path.read_text(encoding="utf-8"))
        data = loader()
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return data

    def healthcheck(self) -> ProviderStatus:
        try:
            refs = self.references()
            active = bool(refs.get("success"))
            return ProviderStatus(self.provider_name, "ACTIVE" if active else "ERROR", active, "TEZ public references reachable", now_iso())
        except Exception as exc:
            return ProviderStatus(self.provider_name, "ERROR", False, f"{type(exc).__name__}: {exc}", now_iso())

    def references(self) -> dict[str, Any]:
        return self._cached("references", lambda: self._request("/references", {"locale": "ru", "formatResult": "true", "xml": "false"}))

    def country_refs(self, country_id: int, city_id: int) -> dict[str, Any]:
        return self._cached(
            f"bycountry_{country_id}_{city_id}",
            lambda: self._request(
                "/byCountry",
                {"countryId": country_id, "cityId": city_id, "locale": "ru", "formatResult": "true", "xml": "false"},
            ),
        )

    def accommodations(self, country_id: int, city_id: int) -> list[dict[str, Any]]:
        data = self._cached(
            f"accommodations_{country_id}_{city_id}",
            lambda: self._request(
                "/accommodations",
                {"countryId": country_id, "cityId": city_id, "locale": "ru", "formatResult": "true", "xml": "false"},
                XML_BASE_URL,
            ),
        )
        if not data.get("success"):
            raise RuntimeError(f"TEZ accommodations failed: {data}")
        return data.get("accommodations", [])

    def resolve_departure(self, name: str) -> dict[str, Any] | None:
        q = norm(name)
        for item in self.references().get("cities", []):
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                self._last_city_id = int(item["cityId"])
                return item
        return None

    def resolve_country(self, name: str, departure_id: str | int | None = None) -> dict[str, Any] | None:
        if departure_id is not None:
            self._last_city_id = int(departure_id)
        q = norm(name)
        for item in self.references().get("countries", []):
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        return None

    def resolve_region(self, country_id: str | int, name: str) -> dict[str, Any] | None:
        q = norm(name)
        city_id = self._last_city_id or 345
        refs = self.country_refs(int(country_id), city_id)
        for item in refs.get("tours", []):
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        for item in refs.get("regions", []):
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        return None

    def resolve_hotels(self, country_id: str | int, region_id: str | int | None, query: str | None = None) -> list[dict[str, Any]]:
        refs = self.country_refs(int(country_id), self._last_city_id or 345)
        hotels = refs.get("hotels", [])
        if region_id:
            region_id_int = int(region_id)
            hotels = [hotel for hotel in hotels if int(hotel.get("tourId") or 0) == region_id_int]
        if query:
            q = norm(query)
            hotels = [hotel for hotel in hotels if q in norm(hotel.get("name"))]
        return hotels

    def list_operators(self, departure_id: str | int | None, country_id: str | int | None) -> list[dict[str, Any]]:
        return [{"id": "TEZ", "name": "TEZ TOUR", "russianName": "TEZ TOUR"}]

    def currency_id(self, currency: str) -> int:
        code = "RUR" if currency.upper() in {"RUB", "RUR"} else currency.upper()
        for item in self.references().get("currencies", []):
            if item.get("code") == code:
                return int(item["currencyId"])
        return 8390

    def hotel_class_id(self, minimum: str = "5 *") -> int:
        for item in self.references().get("hotelClasses", []):
            if item.get("name") == minimum:
                return int(item["classId"])
        return 2570

    def meal_id(self, meal: str | None) -> int:
        preferred = norm(meal) if meal else "все включено"
        for item in self.references().get("rAndBs", []):
            if preferred in norm(item.get("name")) or preferred in norm(item.get("russianName")):
                return int(item["rAndBId"])
        return 5737

    def accommodation_id(self, city_id: int, country_id: int, adults: int, children_ages: list[int]) -> int:
        children = len(children_ages)
        rows = self.accommodations(country_id, city_id)
        for item in rows:
            if int_or(item.get("adult"), -1) == adults and int_or(item.get("children"), -1) == children:
                return int(item["accommodationId"])
        raise ValueError(f"TEZ accommodation not found for adults={adults}, children={children}")

    def child_birthdays(self, children_ages: list[int], departure_from: str) -> str | None:
        if not children_ages:
            return None
        try:
            travel_date = date.fromisoformat(departure_from)
        except ValueError:
            travel_date = date.today()
        birthdays = []
        for age in children_ages[:4]:
            birth_year = travel_date.year - int(age)
            birthdays.append(date(birth_year, travel_date.month, travel_date.day).strftime("%d.%m.%Y"))
        return ",".join(birthdays)

    def search(self, watch: dict[str, Any], exhaustive: bool = False) -> list[TourOffer]:
        refs = watch.get("provider_refs", {}).get("tez", {})
        city_id = int(refs.get("departure_id") or 345)
        country_id = int(refs.get("country_id") or 5732)
        region_id = refs.get("region_id")
        hotel_ids = watch.get("hotel_ids") or refs.get("hotel_ids") or []
        adults = int(watch.get("adults") or 2)
        children_ages = [int(age) for age in (watch.get("children_ages") or [])]
        if int(watch.get("nights_max", 0)) - int(watch.get("nights_min", 0)) > 8:
            raise ValueError("TEZ supports max 8 nights difference between nightsMin and nightsMax")
        params: dict[str, Any] = {
            "accommodationId": int(refs.get("accommodation_id") or self.accommodation_id(city_id, country_id, adults, children_ages)),
            "after": date_ru(watch["departure_date_from"]),
            "before": date_ru(watch["departure_date_to"]),
            "cityId": city_id,
            "countryId": country_id,
            "nightsMin": int(watch["nights_min"]),
            "nightsMax": int(watch["nights_max"]),
            "currency": self.currency_id(watch.get("currency", "RUB")),
            "priceMin": 0,
            "priceMax": int(watch.get("price_max") or 9999999),
            "hotelClassId": self.hotel_class_id(),
            "hotelClassBetter": "true",
            "rAndBId": self.meal_id((watch.get("meal_types") or [None])[0]),
            "rAndBBetter": "true",
            "locale": "ru",
            "xml": "false",
            "formatResult": "true",
        }
        birthdays = self.child_birthdays(children_ages, watch["departure_date_from"])
        if birthdays:
            params["birthdays"] = birthdays
        params["tourType"] = 1
        params["noTicketsFrom"] = "false"
        params["noTicketsTo"] = "false"
        if region_id:
            params["tourId"] = int(region_id)
        for hid in hotel_ids[:10]:
            params.setdefault("hotelId", []).append(int(hid))
        data = self._request("/getResult", params)
        if self.usage_callback:
            self.usage_callback(self.provider_name, "search", "/getResult")
        if not data.get("success"):
            raise RuntimeError(f"TEZ search failed: {data}")
        return self._normalize_rows(data.get("data", []), watch)

    def _normalize_rows(self, rows: list[Any], watch: dict[str, Any]) -> list[TourOffer]:
        offers: list[TourOffer] = []
        for row in rows:
            if not isinstance(row, list) or len(row) < 13:
                continue
            depart = row[0] if isinstance(row[0], str) else None
            nights = row[3] if isinstance(row[3], int) else None
            region = row[5] if isinstance(row[5], list) else []
            hotel = row[6] if isinstance(row[6], list) else []
            meal = row[7] if isinstance(row[7], list) else []
            room = row[8] if isinstance(row[8], list) else []
            price_meta = row[10] if isinstance(row[10], dict) else {}
            links = row[11] if isinstance(row[11], list) else []
            availability = row[12] if isinstance(row[12], str) else None
            flags = row[14] if len(row) > 14 and isinstance(row[14], dict) else {}
            total = price_meta.get("total")
            if total is None and isinstance(room, list) and room:
                total = room[0]
            deeplink = None
            if links and isinstance(links[0], list) and links[0]:
                deeplink = links[0][0]
            offers.append(
                TourOffer(
                    provider=self.provider_name,
                    operator="TEZ TOUR",
                    hotel_id=str(hotel[3]) if len(hotel) > 3 else None,
                    canonical_hotel_id=None,
                    hotel_name=hotel[1] if len(hotel) > 1 else None,
                    resort=region[0] if len(region) > 0 else None,
                    subregion=region[8] if len(region) > 8 else None,
                    hotel_category=None,
                    departure_date=depart,
                    return_date=return_date(depart, nights),
                    nights=nights,
                    adults=int(watch.get("adults") or 0),
                    children_ages=[int(age) for age in (watch.get("children_ages") or [])],
                    meal=meal[1] if len(meal) > 1 else None,
                    room=room[1] if len(room) > 1 else None,
                    flight_out=None,
                    flight_back=None,
                    direct_flight=None,
                    package_type=watch.get("package_type", "full_package"),
                    quoted_price=int(float(total)) if total not in {None, ""} else None,
                    actualized_price=None,
                    currency="RUB" if price_meta.get("currencyId") == 8390 else str(price_meta.get("currency") or watch.get("currency")),
                    availability=availability,
                    deeplink=deeplink,
                    tour_id=str(room[2]) if len(room) > 2 else None,
                    observed_at=now_iso(),
                )
            )
        return offers

    def actualize(self, offer: TourOffer) -> TourOffer:
        return offer

    def get_deeplink(self, offer: TourOffer) -> str | None:
        return offer.deeplink
