"""Travelata Partners API provider.

Official docs:
- https://support.travelpayouts.com/hc/ru/articles/360022674591-API-%D0%BE%D1%82-Travelata
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import time
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from base import ProviderStatus, TourOffer


BASE_URL = "https://api-gateway.travelata.ru"
CACHE_TTL_SECONDS = 24 * 60 * 60
RETRY_STATUS_CODES = {429, 500, 502, 503, 504}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def norm(text: str | None) -> str:
    value = (text or "").casefold().replace("ё", "е")
    for src, dst in {
        "-": " ",
        "_": " ",
        "makadi": "макади",
        "bay": "бей",
        "hurghada": "хургада",
        "egypt": "египет",
        "moscow": "москва",
    }.items():
        value = value.replace(src, dst)
    return " ".join(value.split())


def return_date(checkin: str | None, nights: int | None) -> str | None:
    if not checkin or nights is None:
        return None
    try:
        dt = datetime.fromisoformat(checkin)
    except ValueError:
        return None
    return (dt + timedelta(days=int(nights))).date().isoformat()


def safe_params(params: dict[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in sorted((params or {}).items()):
        if any(part in key.casefold() for part in ("password", "login", "token", "authorization")):
            result[key] = "<redacted>"
        else:
            result[key] = value
    return result


class TravelataTemporaryError(RuntimeError):
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
        self.params = safe_params(params)
        self.status_code = status_code
        self.attempts = attempts


class TravelataQuotaError(TravelataTemporaryError):
    pass


class TravelataProvider:
    provider_name = "travelata"

    def __init__(
        self,
        login: str | None,
        password: str | None,
        cache_dir: Path,
        usage_callback=None,
        timeout_seconds: int = 25,
        max_attempts: int = 3,
    ) -> None:
        self.login = login
        self.password = password
        self.cache_dir = cache_dir
        self.usage_callback = usage_callback
        self.timeout_seconds = timeout_seconds
        self.max_attempts = max(1, max_attempts)
        self.last_rate_limit: dict[str, str | None] = {}

    def capabilities(self) -> dict[str, Any]:
        return {
            "provider": self.provider_name,
            "role": "fallback_market",
            "requires_credentials": True,
            "official_api": True,
            "supports_market_search": True,
            "supports_actualization": False,
            "supports_references": True,
            "supports_children": True,
            "supports_links": True,
            "supports_direct_flight_filter": False,
            "scraping": False,
            "coverage_note": "Travelata cheapestTours, not the whole tour-operator market",
        }

    def _auth_header(self) -> str:
        if not self.login or not self.password:
            raise PermissionError("TRAVELATA_LOGIN/TRAVELATA_PASSWORD are not configured")
        raw = f"{self.login}:{self.password}".encode("utf-8")
        return "Basic " + base64.b64encode(raw).decode("ascii")

    def _request(self, path: str, params: dict[str, Any] | None = None, counted: bool = False) -> Any:
        url = BASE_URL + path
        if params:
            url += "?" + urlencode(params, doseq=True)
        req = Request(
            url,
            headers={
                "Authorization": self._auth_header(),
                "Accept": "application/json",
                "User-Agent": "gromik-tour-price-watch/0.1",
            },
        )
        last_error: Exception | None = None
        for attempt in range(1, self.max_attempts + 1):
            try:
                with urlopen(req, timeout=self.timeout_seconds) as response:
                    raw = response.read().decode("utf-8", errors="replace")
                    self.last_rate_limit = {
                        "limit": response.headers.get("X-RateLimit-Limit"),
                        "remaining": response.headers.get("X-RateLimit-Remaining"),
                        "reset": response.headers.get("X-RateLimit-Reset"),
                    }
                data = json.loads(raw)
                if not data.get("success"):
                    raise RuntimeError(f"Travelata API returned success=false on {path}: {data.get('error')}")
                if counted and self.usage_callback:
                    self.usage_callback(self.provider_name, "search", path)
                return data.get("result")
            except HTTPError as exc:
                body = exc.read().decode("utf-8", errors="replace")[:300]
                if exc.code in {401, 403}:
                    raise PermissionError(f"Travelata authorization failed: HTTP {exc.code}") from exc
                if exc.code == 429:
                    raise TravelataQuotaError(
                        f"Travelata quota limited: HTTP 429",
                        endpoint=path,
                        params=params,
                        status_code=429,
                        attempts=attempt,
                    ) from exc
                if exc.code in RETRY_STATUS_CODES:
                    last_error = exc
                    if attempt < self.max_attempts:
                        time.sleep(attempt)
                        continue
                    raise TravelataTemporaryError(
                        f"Travelata temporary HTTP {exc.code}: {body or exc.reason}",
                        endpoint=path,
                        params=params,
                        status_code=exc.code,
                        attempts=attempt,
                    ) from exc
                raise RuntimeError(f"Travelata HTTP {exc.code} on {path}; params={safe_params(params)}; body={body}") from exc
            except (URLError, TimeoutError, socket.timeout) as exc:
                last_error = exc
                if attempt < self.max_attempts:
                    time.sleep(attempt)
                    continue
                raise TravelataTemporaryError(
                    f"Travelata temporary network error: {exc}",
                    endpoint=path,
                    params=params,
                    attempts=attempt,
                ) from exc
        raise TravelataTemporaryError(
            f"Travelata temporary error after retries: {last_error}",
            endpoint=path,
            params=params,
            attempts=self.max_attempts,
        )

    def _cached(self, name: str, loader) -> Any:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self.cache_dir / f"travelata_{name}.json"
        if path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS:
            return json.loads(path.read_text(encoding="utf-8"))
        data = loader()
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return data

    def healthcheck(self) -> ProviderStatus:
        try:
            rows = self.countries()
            active = bool(rows)
            message = "Travelata API auth works" if active else "Travelata countries directory is empty"
            return ProviderStatus(self.provider_name, "ACTIVE" if active else "ERROR", active, message, now_iso())
        except PermissionError as exc:
            return ProviderStatus(self.provider_name, "AUTH_REQUIRED", False, str(exc), now_iso())
        except TravelataQuotaError as exc:
            return ProviderStatus(self.provider_name, "QUOTA_LIMITED", False, str(exc), now_iso())
        except TravelataTemporaryError as exc:
            return ProviderStatus(self.provider_name, "ERROR", False, str(exc), now_iso())
        except Exception as exc:
            return ProviderStatus(self.provider_name, "ERROR", False, f"{type(exc).__name__}: {exc}", now_iso())

    def countries(self) -> list[dict[str, Any]]:
        return self._cached("countries", lambda: self._request("/partners/directory/countries", {"disabled": 0}))

    def departure_cities(self) -> list[dict[str, Any]]:
        return self._cached("departure_cities", lambda: self._request("/partners/directory/departureCities", {"disabled": 0}))

    def resorts(self, country_id: int | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"disabled": 0, "limit": 1000, "offset": 0}
        if country_id is not None:
            params["country[]"] = [country_id]
        name = f"resorts_{country_id or 'all'}"
        return self._cached(name, lambda: self._request("/partners/directory/resorts", params))

    def hotels(self, resort_id: int | None = None, query: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset = 0
        while True:
            params: dict[str, Any] = {"disabled": 0, "limit": 1000, "offset": offset}
            if resort_id is not None:
                params["resort[]"] = [resort_id]
            page = self._cached(
                f"hotels_{resort_id or 'all'}_{offset}",
                lambda params=params: self._request("/partners/directory/hotels", params),
            )
            rows.extend(page)
            if len(page) < 1000:
                break
            offset += 1000
            if offset >= 10000:
                break
        if query:
            q = norm(query)
            rows = [item for item in rows if q in norm(item.get("name"))]
        return rows

    def meals(self) -> list[dict[str, Any]]:
        return self._cached("meals", lambda: self._request("/partners/directory/meals"))

    def hotel_categories(self) -> list[dict[str, Any]]:
        return self._cached("hotel_categories", lambda: self._request("/partners/directory/hotelCategories"))

    def resolve_departure(self, name: str) -> dict[str, Any] | None:
        q = norm(name)
        for item in self.departure_cities():
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        return None

    def resolve_country(self, name: str, departure_id: str | int | None = None) -> dict[str, Any] | None:
        q = norm(name)
        for item in self.countries():
            if norm(item.get("name")) == q or q in norm(item.get("name")):
                return item
        return None

    def resolve_region(self, country_id: str | int, name: str) -> dict[str, Any] | None:
        q = norm(name)
        for item in self.resorts(int(country_id)):
            value = norm(item.get("name"))
            if value == q or q in value or value in q:
                return item
        return None

    def resolve_hotels(self, country_id: str | int, region_id: str | int | None, query: str | None = None) -> list[dict[str, Any]]:
        return self.hotels(int(region_id) if region_id else None, query)

    def list_operators(self, departure_id: str | int | None, country_id: str | int | None) -> list[dict[str, Any]]:
        return []

    def meal_id(self, meal: str | None) -> int | None:
        if not meal:
            return None
        q = norm(meal)
        preferred_ai = any(token in q for token in ("all inclusive", "все включено", "всё включено", "ai"))
        for item in self.meals():
            value = norm((item.get("name") or "") + " " + (item.get("code") or ""))
            if (preferred_ai and ("ai" in value or "все включено" in value)) or q in value:
                return int(item["id"])
        return None

    def five_star_category_id(self) -> int | None:
        for item in self.hotel_categories():
            if norm(item.get("name")) in {"5", "5*", "5 *"}:
                return int(item["id"])
        return None

    def search(self, watch: dict[str, Any], exhaustive: bool = False) -> list[TourOffer]:
        refs = watch.get("provider_refs", {}).get("travelata", {})
        departure_id = refs.get("departure_id")
        country_id = refs.get("country_id")
        resort_id = refs.get("region_id")
        if not departure_id or not country_id:
            raise ValueError("Travelata departure/country ids are not resolved")
        children_ages = [int(age) for age in (watch.get("children_ages") or [])]
        kids_ages = [age for age in children_ages if 2 <= age <= 11]
        infants = [age for age in children_ages if age < 2]
        adult_like_children = [age for age in children_ages if age >= 12]
        adults = int(watch.get("adults") or 2) + len(adult_like_children)
        params: dict[str, Any] = {
            "countries[]": [int(country_id)],
            "departureCity": int(departure_id),
            "touristGroup[adults]": adults,
            "touristGroup[kids]": len(kids_ages),
            "touristGroup[infants]": len(infants),
            "checkInDateRange[from]": watch["departure_date_from"],
            "checkInDateRange[to]": watch["departure_date_to"],
            "nightRange[from]": int(watch["nights_min"]),
            "nightRange[to]": int(watch["nights_max"]),
        }
        if kids_ages:
            params["touristGroup[kidsAges][]"] = kids_ages
        if resort_id:
            params["resorts[]"] = [int(resort_id)]
        meal = self.meal_id((watch.get("meal_types") or [None])[0])
        if meal:
            params["meals[]"] = [meal]
        if watch.get("hotel_ids"):
            params["hotels[]"] = [int(x) for x in watch["hotel_ids"]]
        elif refs.get("hotel_ids"):
            params["hotels[]"] = [int(x) for x in refs["hotel_ids"]]
        category_id = self.five_star_category_id()
        if category_id:
            params["hotelCategories[]"] = [category_id]
        data = self._request("/partners/statistic/cheapestTours", params, counted=True)
        return self._normalize_results(data if isinstance(data, list) else [], watch)

    def _normalize_results(self, rows: list[Any], watch: dict[str, Any]) -> list[TourOffer]:
        meal_names = {int(item["id"]): item.get("name") for item in self.meals() if item.get("id") is not None}
        resort_names = {int(item["id"]): item.get("name") for item in self.resorts(int(watch.get("provider_refs", {}).get("travelata", {}).get("country_id") or 0)) if item.get("id") is not None}
        offers: list[TourOffer] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            price = row.get("price")
            resort_id = row.get("resortId")
            offers.append(
                TourOffer(
                    provider=self.provider_name,
                    operator=str(row.get("operatorId")) if row.get("operatorId") is not None else None,
                    hotel_id=str(row.get("hotelId")) if row.get("hotelId") is not None else None,
                    canonical_hotel_id=None,
                    hotel_name=row.get("hotelName"),
                    resort=resort_names.get(int(resort_id)) if resort_id is not None and str(resort_id).isdigit() else None,
                    subregion=None,
                    hotel_category=row.get("hotelCategoryName") or (str(row.get("hotelCategory")) if row.get("hotelCategory") else None),
                    departure_date=row.get("checkinDate"),
                    return_date=return_date(row.get("checkinDate"), row.get("nights")),
                    nights=int(row["nights"]) if row.get("nights") is not None else None,
                    adults=int(watch.get("adults") or 2),
                    children_ages=[int(age) for age in (watch.get("children_ages") or [])],
                    meal=meal_names.get(int(row["mealId"])) if row.get("mealId") is not None and str(row.get("mealId")).isdigit() else None,
                    room=None,
                    flight_out=None,
                    flight_back=None,
                    direct_flight=None,
                    package_type=watch.get("package_type", "full_package"),
                    quoted_price=int(price) if price is not None else None,
                    actualized_price=None,
                    currency="RUB",
                    availability="available" if not row.get("expired") else f"expires {row.get('expired')}",
                    deeplink=row.get("tourPageUrl") or row.get("searchPageUrl"),
                    tour_id=row.get("tourIdentity"),
                    observed_at=now_iso(),
                )
            )
        return offers

    def actualize(self, offer: TourOffer) -> TourOffer:
        return offer

    def get_deeplink(self, offer: TourOffer) -> str | None:
        return offer.deeplink
