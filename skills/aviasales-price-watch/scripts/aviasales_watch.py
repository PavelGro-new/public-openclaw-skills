#!/usr/bin/env python3
"""Reusable Aviasales/Travelpayouts cached flight price watches."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WATCHES_FILE = Path("/opt/openclaw-state/aviasales-price-watch/watches.json")
SECRET_FILE = Path(os.environ.get("AVIASALES_ENV_FILE") or "/opt/openclaw-secrets/aviasales-price-watch.env")
API_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
AVIASALES_BASE_URL = "https://www.aviasales.com"
DEFAULT_LIMIT_PER_PAIR = 30
DEFAULT_TOP = 5
WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(message: str, code: int = 1) -> None:
    emit({"ok": False, "error": message})
    raise SystemExit(code)


def today() -> str:
    return date.today().isoformat()


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def watches_path() -> Path:
    configured = os.environ.get("AVIASALES_WATCHES_FILE")
    return Path(configured) if configured else DEFAULT_WATCHES_FILE


def load_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    env: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        try:
            parts = shlex.split(line, comments=False, posix=True)
        except ValueError as exc:
            fail(f"Cannot parse secret file line safely: {exc}", 66)
        if not parts:
            continue
        key, value = parts[0].split("=", 1)
        env[key] = value
    return env


def token() -> str:
    value = load_env(SECRET_FILE).get("TRAVELPAYOUTS_TOKEN") or os.environ.get("TRAVELPAYOUTS_TOKEN")
    if not value:
        fail("TRAVELPAYOUTS_TOKEN is not configured", 67)
    return value


def load_store() -> dict[str, Any]:
    path = watches_path()
    if not path.exists():
        return {"version": 1, "watches": []}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("version", 1)
    data.setdefault("watches", [])
    for watch in data["watches"]:
        watch.setdefault("report_enabled", False)
        watch.setdefault("report_days", [])
        watch.setdefault("report_time", None)
        watch.setdefault("report_timezone", None)
    return data


def save_store(store: dict[str, Any]) -> None:
    path = watches_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(store, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def slugify(value: str) -> str:
    text = value.lower()
    replacements = {
        "москва": "moscow",
        "стамбул": "istanbul",
        "тест": "test",
    }
    for src, dst in replacements.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "watch"


def unique_id(store: dict[str, Any], base: str) -> str:
    existing = {watch["id"] for watch in store["watches"]}
    if base not in existing:
        return base
    suffix = 2
    while f"{base}-{suffix}" in existing:
        suffix += 1
    return f"{base}-{suffix}"


def parse_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        fail(f"{field} must be YYYY-MM-DD, got: {value}", 68)


def parse_bool(value: str | bool | None) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "да"}:
        return True
    if normalized in {"0", "false", "no", "n", "нет"}:
        return False
    fail(f"Boolean value expected, got: {value}", 69)


def parse_days(value: str | None) -> list[str] | None:
    if value is None:
        return None
    days = [part.strip() for part in value.split(",") if part.strip()]
    invalid = [day for day in days if day not in WEEKDAYS]
    if invalid:
        fail(f"Unsupported report day(s): {', '.join(invalid)}", 76)
    return days


def parse_time(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.match(r"^\d{2}:\d{2}$", value):
        fail(f"report time must be HH:MM, got: {value}", 77)
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        fail(f"report time must be HH:MM, got: {value}", 77)
    return value


def parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except ValueError:
        return None


def compact(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def get_watch(store: dict[str, Any], watch_id: str) -> dict[str, Any]:
    for watch in store["watches"]:
        if watch.get("id") == watch_id:
            return watch
    fail(f"Watch not found: {watch_id}", 70)


def date_pairs(watch: dict[str, Any]) -> list[tuple[date, date, int | None]]:
    depart_from = date.fromisoformat(watch["departure_date_from"])
    depart_to = date.fromisoformat(watch["departure_date_to"])
    pairs: list[tuple[date, date, int | None]] = []
    current = depart_from
    duration = watch.get("trip_duration_days")
    if duration:
        min_days = int(duration["min"])
        max_days = int(duration["max"])
        while current <= depart_to:
            for days in range(min_days, max_days + 1):
                pairs.append((current, current + timedelta(days=days), days))
            current += timedelta(days=1)
        return pairs

    return_from = date.fromisoformat(watch["return_date_from"])
    return_to = date.fromisoformat(watch["return_date_to"])
    while current <= depart_to:
        ret = return_from
        while ret <= return_to:
            if ret >= current:
                pairs.append((current, ret, (ret - current).days))
            ret += timedelta(days=1)
        current += timedelta(days=1)
    return pairs


def request_prices(access_token: str, watch: dict[str, Any], depart: date, ret: date) -> dict[str, Any]:
    params = {
        "origin": watch["origin"].upper(),
        "destination": watch["destination"].upper(),
        "departure_at": depart.isoformat(),
        "return_at": ret.isoformat(),
        "one_way": "false",
        "direct": "true" if watch.get("direct_only", True) else "false",
        "currency": watch.get("currency", "rub"),
        "sorting": "price",
        "limit": str(DEFAULT_LIMIT_PER_PAIR),
        "page": "1",
        "market": watch.get("market", "ru"),
    }
    url = API_URL + "?" + urlencode(params)
    request = Request(
        url,
        headers={
            "X-Access-Token": access_token,
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": "openclaw-aviasales-price-watch/1.0",
        },
        method="GET",
    )
    try:
        with urlopen(request, timeout=30) as response:
            body = response.read()
            encoding = response.headers.get("Content-Encoding", "")
            if "gzip" in encoding:
                import gzip

                body = gzip.decompress(body)
            elif "deflate" in encoding:
                import zlib

                body = zlib.decompress(body)
            return {"ok": True, "status": response.status, "response": json.loads(body.decode("utf-8"))}
    except HTTPError as exc:
        return {"ok": False, "status": exc.code, "error": exc.reason}
    except (URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"ok": False, "status": None, "error": str(exc)}


def found_at_age(value: Any) -> str | None:
    if not value:
        return None
    text = str(value)
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    age_hours = round((datetime.now(timezone.utc) - dt.astimezone(timezone.utc)).total_seconds() / 3600, 1)
    return f"{text} ({age_hours}h ago)"


def price_category(watch: dict[str, Any], calculated_total: int) -> str:
    urgent = watch.get("urgent_threshold_total")
    alert = watch.get("alert_threshold_total")
    if urgent is not None and calculated_total <= int(urgent):
        return "СРОЧНО ПРОВЕРИТЬ / кандидат на покупку"
    if alert is not None and calculated_total <= int(alert):
        return "ХОРОШАЯ ЦЕНА / обратить внимание"
    return "обычное наблюдение"


def normalize_item(watch: dict[str, Any], item: dict[str, Any], depart: date, ret: date, days: int | None) -> dict[str, Any] | None:
    item_depart = parse_iso_date(item.get("departure_at"))
    item_return = parse_iso_date(item.get("return_at"))
    if item_depart != depart or item_return != ret:
        return None

    transfers = item.get("transfers")
    return_transfers = item.get("return_transfers")
    if watch.get("direct_only", True):
        if transfers not in (0, "0", None):
            return None
        if return_transfers not in (0, "0", None):
            return None

    try:
        one_ticket = int(round(float(item["price"])))
    except (KeyError, TypeError, ValueError):
        return None

    passengers = int(watch.get("passengers", 1))
    calculated_total = one_ticket * passengers
    link = item.get("link")
    if isinstance(link, str) and link.startswith("/"):
        link = AVIASALES_BASE_URL + link

    return {
        "watch_id": watch["id"],
        "watch_name": watch["name"],
        "origin": item.get("origin") or watch["origin"],
        "destination": item.get("destination") or watch["destination"],
        "origin_airport": item.get("origin_airport"),
        "destination_airport": item.get("destination_airport"),
        "departure_at": item.get("departure_at"),
        "return_at": item.get("return_at"),
        "depart_date": depart.isoformat(),
        "return_date": ret.isoformat(),
        "trip_duration_days": days,
        "airline": item.get("airline"),
        "flight_number": item.get("flight_number"),
        "transfers": transfers,
        "return_transfers": return_transfers,
        "duration": item.get("duration"),
        "duration_to": item.get("duration_to"),
        "duration_back": item.get("duration_back"),
        "price_one_ticket": one_ticket,
        "currency": watch.get("currency", "rub"),
        "passengers_for_calculation": passengers,
        "calculated_total": calculated_total,
        "price_category": price_category(watch, calculated_total),
        "found_at": item.get("found_at"),
        "found_at_age": found_at_age(item.get("found_at")),
        "link": link,
    }


def dedupe(options: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[Any, ...]] = set()
    unique: list[dict[str, Any]] = []
    for option in options:
        key = (
            option.get("watch_id"),
            option.get("departure_at"),
            option.get("return_at"),
            option.get("airline"),
            option.get("flight_number"),
            option.get("origin_airport"),
            option.get("destination_airport"),
            option.get("price_one_ticket"),
            option.get("link"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(option)
    return unique


def check_watch(store: dict[str, Any], watch: dict[str, Any], top: int) -> dict[str, Any]:
    access_token = token()
    requests_made: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    options: list[dict[str, Any]] = []
    for depart, ret, days in date_pairs(watch):
        result = request_prices(access_token, watch, depart, ret)
        requests_made.append(
            {
                "departure_at": depart.isoformat(),
                "return_at": ret.isoformat(),
                "duration_days": days,
                "status": result.get("status"),
                "ok": result.get("ok"),
            }
        )
        if not result["ok"]:
            errors.append(
                {
                    "departure_at": depart.isoformat(),
                    "return_at": ret.isoformat(),
                    "status": result.get("status"),
                    "error": result.get("error"),
                }
            )
            continue
        response = result.get("response") or {}
        if not response.get("success"):
            errors.append(
                {
                    "departure_at": depart.isoformat(),
                    "return_at": ret.isoformat(),
                    "status": result.get("status"),
                    "error": response.get("error"),
                }
            )
            continue
        for item in response.get("data") or []:
            normalized = normalize_item(watch, item, depart, ret, days)
            if normalized:
                options.append(normalized)

    sorted_options = sorted(dedupe(options), key=lambda item: (item["price_one_ticket"], item["departure_at"], item["return_at"]))
    best = sorted_options[:top]
    watch.setdefault("state", {})
    watch.setdefault("history", [])
    checked_at = now_iso()
    watch["state"].update(
        {
            "updated_at": today(),
            "last_checked_at": checked_at,
            "last_best_total": best[0]["calculated_total"] if best else None,
            "last_status": "ok" if not errors else "partial_error",
            "last_options_found": len(sorted_options),
        }
    )
    watch["history"].append(
        {
            "checked_at": checked_at,
            "options_found": len(sorted_options),
            "best_total": best[0]["calculated_total"] if best else None,
            "best_one_ticket": best[0]["price_one_ticket"] if best else None,
            "errors": len(errors),
        }
    )
    watch["history"] = watch["history"][-30:]
    save_store(store)
    return {
        "ok": True,
        "source": "Travelpayouts Aviasales Data API /aviasales/v3/prices_for_dates",
        "watch": watch,
        "requests_made": requests_made,
        "errors": errors,
        "options_found": len(sorted_options),
        "best": best,
        "direct_only_verified": all(
            option.get("transfers") in (0, "0", None) and option.get("return_transfers") in (0, "0", None)
            for option in sorted_options
        ),
        "disclaimer": "API prices are cached one-ticket radar values; calculated totals are not proof that all seats are available.",
    }


def create_watch(args: argparse.Namespace) -> dict[str, Any]:
    store = load_store()
    watch_id = args.id or unique_id(store, slugify(args.name))
    if any(watch.get("id") == watch_id for watch in store["watches"]):
        fail(f"Watch already exists: {watch_id}", 71)
    if not args.duration_min and not args.return_from:
        fail("Provide either --duration-min/--duration-max or --return-from/--return-to", 72)
    if args.duration_min and not args.duration_max:
        args.duration_max = args.duration_min
    if args.return_from and not args.return_to:
        args.return_to = args.return_from
    watch = {
        "id": watch_id,
        "name": args.name,
        "origin": args.origin.upper(),
        "destination": args.destination.upper(),
        "departure_date_from": parse_date(args.departure_from, "departure-from"),
        "departure_date_to": parse_date(args.departure_to, "departure-to"),
        "trip_duration_days": {"min": int(args.duration_min), "max": int(args.duration_max)} if args.duration_min else None,
        "return_date_from": parse_date(args.return_from, "return-from"),
        "return_date_to": parse_date(args.return_to, "return-to"),
        "passengers": int(args.passengers),
        "direct_only": parse_bool(args.direct_only),
        "currency": args.currency.lower(),
        "market": args.market,
        "alert_threshold_total": int(args.alert_threshold_total) if args.alert_threshold_total is not None else None,
        "urgent_threshold_total": int(args.urgent_threshold_total) if args.urgent_threshold_total is not None else None,
        "optional_preferences": {"notes": args.notes} if args.notes else {},
        "enabled": True,
        "check_cadence": args.check_cadence,
        "report_enabled": False,
        "report_days": [],
        "report_time": None,
        "report_timezone": None,
        "state": {"created_at": today(), "updated_at": today(), "last_checked_at": None, "last_best_total": None, "last_status": "created"},
        "history": [],
    }
    store["watches"].append(watch)
    save_store(store)
    return {"ok": True, "action": "created", "watch": watch}


def list_watches() -> dict[str, Any]:
    store = load_store()
    return {"ok": True, "watches": store["watches"], "count": len(store["watches"])}


def update_watch(args: argparse.Namespace) -> dict[str, Any]:
    store = load_store()
    watch = get_watch(store, args.watch)
    fields = {
        "name": args.name,
        "origin": args.origin.upper() if args.origin else None,
        "destination": args.destination.upper() if args.destination else None,
        "departure_date_from": parse_date(args.departure_from, "departure-from") if args.departure_from else None,
        "departure_date_to": parse_date(args.departure_to, "departure-to") if args.departure_to else None,
        "passengers": int(args.passengers) if args.passengers is not None else None,
        "direct_only": parse_bool(args.direct_only),
        "currency": args.currency.lower() if args.currency else None,
        "alert_threshold_total": int(args.alert_threshold_total) if args.alert_threshold_total is not None else None,
        "urgent_threshold_total": int(args.urgent_threshold_total) if args.urgent_threshold_total is not None else None,
        "check_cadence": args.check_cadence,
        "report_enabled": parse_bool(args.report_enabled),
        "report_time": parse_time(args.report_time) if args.report_time else None,
        "report_timezone": args.report_timezone,
    }
    for key, value in fields.items():
        if value is not None:
            watch[key] = value
    if args.duration_min is not None:
        watch["trip_duration_days"] = {"min": int(args.duration_min), "max": int(args.duration_max or args.duration_min)}
        watch["return_date_from"] = None
        watch["return_date_to"] = None
    if args.return_from is not None:
        watch["return_date_from"] = parse_date(args.return_from, "return-from")
        watch["return_date_to"] = parse_date(args.return_to or args.return_from, "return-to")
        watch["trip_duration_days"] = None
    if args.notes is not None:
        watch["optional_preferences"] = {"notes": args.notes}
    if args.report_days is not None:
        watch["report_days"] = parse_days(args.report_days)
    watch.setdefault("state", {})["updated_at"] = today()
    save_store(store)
    return {"ok": True, "action": "updated", "watch": watch}


def schedule_watch(args: argparse.Namespace) -> dict[str, Any]:
    store = load_store()
    watch = get_watch(store, args.watch)
    if args.show:
        return {
            "ok": True,
            "action": "schedule_show",
            "watch_id": watch["id"],
            "name": watch["name"],
            "report_enabled": watch.get("report_enabled", False),
            "report_days": watch.get("report_days") or [],
            "report_time": watch.get("report_time"),
            "report_timezone": watch.get("report_timezone"),
            "needs_clarification": bool(
                watch.get("report_enabled")
                and (not watch.get("report_days") or not watch.get("report_time") or not watch.get("report_timezone"))
            ),
        }
    if args.enable is not None:
        watch["report_enabled"] = parse_bool(args.enable)
    if args.days is not None:
        watch["report_days"] = parse_days(args.days)
    if args.time is not None:
        watch["report_time"] = parse_time(args.time)
    if args.timezone is not None:
        watch["report_timezone"] = args.timezone
    watch.setdefault("state", {})["updated_at"] = today()
    save_store(store)
    return {"ok": True, "action": "schedule_updated", "watch": watch}


def set_enabled(watch_id: str, enabled: bool) -> dict[str, Any]:
    store = load_store()
    watch = get_watch(store, watch_id)
    watch["enabled"] = enabled
    watch.setdefault("state", {})["updated_at"] = today()
    watch["state"]["last_status"] = "enabled" if enabled else "paused"
    save_store(store)
    return {"ok": True, "action": "resumed" if enabled else "paused", "watch": watch}


def delete_watch(watch_id: str) -> dict[str, Any]:
    store = load_store()
    before = len(store["watches"])
    store["watches"] = [watch for watch in store["watches"] if watch.get("id") != watch_id]
    if len(store["watches"]) == before:
        fail(f"Watch not found: {watch_id}", 70)
    save_store(store)
    return {"ok": True, "action": "deleted", "watch_id": watch_id}


def render_list(payload: dict[str, Any]) -> str:
    lines = ["# Aviasales Watches", ""]
    if not payload["watches"]:
        return "# Aviasales Watches\n\nNo watches configured."
    for watch in payload["watches"]:
        duration = watch.get("trip_duration_days")
        if duration:
            ret = f"{duration['min']}-{duration['max']} days"
        else:
            ret = f"{watch.get('return_date_from')}..{watch.get('return_date_to')}"
        state = watch.get("state", {})
        lines.append(
            f"- `{watch['id']}`: {watch['name']} | {watch['origin']}->{watch['destination']} | "
            f"{watch['departure_date_from']}..{watch['departure_date_to']} | return {ret} | "
            f"passengers={watch['passengers']} | direct={watch.get('direct_only')} | "
            f"enabled={watch.get('enabled')} | alert<={compact(watch.get('alert_threshold_total'))} | "
            f"urgent<={compact(watch.get('urgent_threshold_total'))} | last_best={compact(state.get('last_best_total'))} | "
            f"report={watch.get('report_enabled', False)} {','.join(watch.get('report_days') or [])} "
            f"{compact(watch.get('report_time'))} {compact(watch.get('report_timezone'))}"
        )
    return "\n".join(lines)


def render_check(payload: dict[str, Any]) -> str:
    watch = payload["watch"]
    lines = [
        f"# Aviasales Price Watch: {watch['name']}",
        "",
        f"Watch ID: `{watch['id']}`",
        f"Route: {watch['origin']} -> {watch['destination']}",
        f"Dates: {watch['departure_date_from']}..{watch['departure_date_to']}",
        f"Passengers for calculation: {watch['passengers']}",
        f"Options found: {payload['options_found']}",
        f"Direct-only verified: {payload['direct_only_verified']}",
        "",
        "Important: API price is for one ticket when returned by Travelpayouts. Total is calculated only, not proof that all seats are available.",
        "",
    ]
    if payload["errors"]:
        lines.append("## API Warnings")
        for error in payload["errors"]:
            lines.append(
                f"- {error['departure_at']} -> {error['return_at']}: status={compact(error.get('status'))}, error={compact(error.get('error'))}"
            )
        lines.append("")
    lines.append("## Best 5")
    if not payload["best"]:
        lines.append("No matching options returned for this watch.")
        return "\n".join(lines)
    for idx, item in enumerate(payload["best"], 1):
        lines.extend(
            [
                f"{idx}. {item['depart_date']} -> {item['return_date']} ({compact(item.get('trip_duration_days'))} days)",
                f"   - Airline / flight: {compact(item.get('airline'))} {compact(item.get('flight_number'))}",
                f"   - Airports: {compact(item.get('origin_airport'))} -> {compact(item.get('destination_airport'))}",
                f"   - Time: {compact(item.get('departure_at'))} / {compact(item.get('return_at'))}",
                f"   - Price: {item['price_one_ticket']} {item['currency'].upper()} per ticket; calculated x{item['passengers_for_calculation']}: {item['calculated_total']} {item['currency'].upper()}",
                f"   - Category: {item['price_category']}",
                f"   - Transfers: outbound={compact(item.get('transfers'))}, return={compact(item.get('return_transfers'))}",
                f"   - Found: {compact(item.get('found_at_age'))}",
                f"   - Link: {compact(item.get('link'))}",
            ]
        )
    return "\n".join(lines)


def add_common_watch_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--name")
    parser.add_argument("--origin")
    parser.add_argument("--destination")
    parser.add_argument("--departure-from")
    parser.add_argument("--departure-to")
    parser.add_argument("--duration-min", type=int)
    parser.add_argument("--duration-max", type=int)
    parser.add_argument("--return-from")
    parser.add_argument("--return-to")
    parser.add_argument("--passengers", type=int)
    parser.add_argument("--direct-only")
    parser.add_argument("--currency")
    parser.add_argument("--market")
    parser.add_argument("--alert-threshold-total", type=int)
    parser.add_argument("--urgent-threshold-total", type=int)
    parser.add_argument("--notes")
    parser.add_argument("--check-cadence")
    parser.add_argument("--report-enabled")
    parser.add_argument("--report-days", help="Comma-separated English weekday names")
    parser.add_argument("--report-time", help="HH:MM")
    parser.add_argument("--report-timezone")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    list_parser = sub.add_parser("list")
    list_parser.add_argument("--format", choices=("json", "markdown"), default="json")

    create_parser = sub.add_parser("create")
    create_parser.add_argument("--id")
    add_common_watch_fields(create_parser)
    create_parser.set_defaults(
        direct_only="true",
        currency="rub",
        market="ru",
        passengers=1,
        check_cadence="manual",
    )

    check_parser = sub.add_parser("check")
    check_parser.add_argument("--watch")
    check_parser.add_argument("--all", action="store_true")
    check_parser.add_argument("--top", type=int, default=DEFAULT_TOP)
    check_parser.add_argument("--format", choices=("json", "markdown"), default="json")

    update_parser = sub.add_parser("update")
    update_parser.add_argument("--watch", required=True)
    add_common_watch_fields(update_parser)

    pause_parser = sub.add_parser("pause")
    pause_parser.add_argument("--watch", required=True)

    resume_parser = sub.add_parser("resume")
    resume_parser.add_argument("--watch", required=True)

    delete_parser = sub.add_parser("delete")
    delete_parser.add_argument("--watch", required=True)

    schedule_parser = sub.add_parser("schedule")
    schedule_parser.add_argument("--watch", required=True)
    schedule_parser.add_argument("--show", action="store_true")
    schedule_parser.add_argument("--enable")
    schedule_parser.add_argument("--days", help="Comma-separated English weekday names")
    schedule_parser.add_argument("--time", help="HH:MM")
    schedule_parser.add_argument("--timezone")

    args = parser.parse_args()
    if args.command == "list":
        payload = list_watches()
        print(render_list(payload) if args.format == "markdown" else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "create":
        required = ["name", "origin", "destination", "departure_from", "departure_to", "passengers", "direct_only", "currency"]
        missing = [field for field in required if getattr(args, field, None) in (None, "")]
        if missing:
            fail("Missing required fields: " + ", ".join(missing), 73)
        emit(create_watch(args))
        return 0
    if args.command == "check":
        store = load_store()
        if args.all:
            payloads = [check_watch(store, watch, args.top) for watch in store["watches"] if watch.get("enabled", True)]
            emit({"ok": True, "checks": payloads})
            return 0
        if not args.watch:
            fail("Provide --watch WATCH_ID or --all", 74)
        payload = check_watch(store, get_watch(store, args.watch), args.top)
        print(render_check(payload) if args.format == "markdown" else json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    if args.command == "update":
        emit(update_watch(args))
        return 0
    if args.command == "pause":
        emit(set_enabled(args.watch, False))
        return 0
    if args.command == "resume":
        emit(set_enabled(args.watch, True))
        return 0
    if args.command == "delete":
        emit(delete_watch(args.watch))
        return 0
    if args.command == "schedule":
        emit(schedule_watch(args))
        return 0
    fail(f"Unsupported command: {args.command}", 75)


if __name__ == "__main__":
    raise SystemExit(main())
