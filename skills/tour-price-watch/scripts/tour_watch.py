#!/usr/bin/env python3
"""Universal tour package price watch for Gromik."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import sqlite3
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
PROVIDERS_DIR = ROOT / "providers"
sys.path.insert(0, str(PROVIDERS_DIR))

from tourvisor import TourvisorProvider  # noqa: E402
from tez import TezTemporaryError, TezTourProvider  # noqa: E402
from travelata import TravelataProvider, TravelataQuotaError, TravelataTemporaryError  # noqa: E402


SECRET_FILE = Path(os.environ.get("TOUR_WATCH_ENV_FILE") or "/opt/openclaw-secrets/tour-price-watch.env")
DEFAULT_STATE_DIR = Path("/opt/openclaw-state/tour-price-watch")
DEFAULT_DB = DEFAULT_STATE_DIR / "tour-price-watch.db"
DEFAULT_CACHE_DIR = DEFAULT_STATE_DIR / "cache"
WEEKDAYS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}
DEVELOPMENT_TOURVISOR_DAILY_BUDGET = 50


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def today() -> str:
    return date.today().isoformat()


def emit(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def fail(message: str, code: int = 1) -> None:
    emit({"ok": False, "error": message})
    raise SystemExit(code)


def load_env(path: Path = SECRET_FILE) -> dict[str, str]:
    env: dict[str, str] = {}
    if not path.exists():
        return env
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        parts = shlex.split(line, comments=False, posix=True)
        if not parts:
            continue
        key, value = parts[0].split("=", 1)
        env[key] = value
    return env


def db_path() -> Path:
    return Path(os.environ.get("TOUR_WATCH_DB") or DEFAULT_DB)


def cache_dir() -> Path:
    return Path(os.environ.get("TOUR_WATCH_CACHE_DIR") or DEFAULT_CACHE_DIR)


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    try:
        os.chmod(path, 0o600)
    except FileNotFoundError:
        pass
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS watches (
          id TEXT PRIMARY KEY,
          name TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          data_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS watch_hotels (
          watch_id TEXT NOT NULL,
          hotel_name TEXT,
          provider TEXT,
          provider_hotel_id TEXT,
          canonical_hotel_id TEXT,
          created_at TEXT NOT NULL,
          PRIMARY KEY (watch_id, provider, provider_hotel_id),
          FOREIGN KEY (watch_id) REFERENCES watches(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS provider_hotel_mapping (
          canonical_hotel_id TEXT NOT NULL,
          provider TEXT NOT NULL,
          provider_hotel_id TEXT NOT NULL,
          hotel_name TEXT NOT NULL,
          normalized_name TEXT NOT NULL,
          region TEXT,
          hotel_category TEXT,
          confidence REAL,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (provider, provider_hotel_id)
        );
        CREATE TABLE IF NOT EXISTS offers (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          watch_id TEXT NOT NULL,
          offer_key TEXT NOT NULL,
          comparison_key TEXT NOT NULL,
          provider TEXT NOT NULL,
          operator TEXT,
          hotel_id TEXT,
          canonical_hotel_id TEXT,
          hotel_name TEXT,
          resort TEXT,
          subregion TEXT,
          hotel_category TEXT,
          departure_date TEXT,
          return_date TEXT,
          nights INTEGER,
          adults INTEGER,
          children_ages TEXT,
          meal TEXT,
          room TEXT,
          flight_out TEXT,
          flight_back TEXT,
          direct_flight INTEGER,
          package_type TEXT,
          quoted_price INTEGER,
          actualized_price INTEGER,
          currency TEXT,
          availability TEXT,
          deeplink TEXT,
          tour_id TEXT,
          observed_at TEXT NOT NULL,
          actualized_at TEXT,
          raw_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_offers_watch_observed ON offers(watch_id, observed_at);
        CREATE INDEX IF NOT EXISTS idx_offers_comparison ON offers(watch_id, comparison_key);
        CREATE TABLE IF NOT EXISTS price_history (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          watch_id TEXT NOT NULL,
          comparison_key TEXT NOT NULL,
          provider TEXT NOT NULL,
          operator TEXT,
          hotel_name TEXT,
          observed_at TEXT NOT NULL,
          quoted_price INTEGER,
          actualized_price INTEGER,
          availability TEXT
        );
        CREATE TABLE IF NOT EXISTS alert_state (
          watch_id TEXT PRIMARY KEY,
          data_json TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          run_type TEXT NOT NULL,
          started_at TEXT NOT NULL,
          completed_at TEXT,
          status TEXT,
          data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_health (
          provider TEXT PRIMARY KEY,
          status TEXT NOT NULL,
          consecutive_errors INTEGER NOT NULL DEFAULT 0,
          last_success_at TEXT,
          last_error_at TEXT,
          last_error_message TEXT,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS report_state (
          watch_id TEXT PRIMARY KEY,
          last_report_at TEXT,
          last_report_price_total INTEGER,
          last_report_offer_key TEXT,
          data_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS provider_usage (
          provider TEXT NOT NULL,
          day TEXT NOT NULL,
          usage_type TEXT NOT NULL,
          count INTEGER NOT NULL DEFAULT 0,
          updated_at TEXT NOT NULL,
          PRIMARY KEY (provider, day, usage_type)
        );
        """
    )
    conn.commit()


def normalize_name(text: str | None) -> str:
    value = (text or "").casefold().replace("ё", "е")
    value = re.sub(r"[^a-zа-я0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def slugify(value: str) -> str:
    translit = {
        "москва": "moscow",
        "египет": "egypt",
        "турция": "turkey",
        "макади": "makadi",
        "белек": "belek",
        "тест": "test",
    }
    text = normalize_name(value)
    for src, dst in translit.items():
        text = text.replace(src, dst)
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "watch"


def parse_bool(value: str | bool | None) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    v = value.casefold().strip()
    if v in {"1", "true", "yes", "y", "да"}:
        return True
    if v in {"0", "false", "no", "n", "нет"}:
        return False
    fail(f"Boolean expected, got {value}")


def parse_days(value: str | None) -> list[str] | None:
    if value is None:
        return None
    days = [part.strip() for part in value.split(",") if part.strip()]
    invalid = [day for day in days if day not in WEEKDAYS]
    if invalid:
        fail(f"Unsupported report day(s): {', '.join(invalid)}")
    return days


def parse_time(value: str | None) -> str | None:
    if value is None:
        return None
    if not re.match(r"^\d{2}:\d{2}$", value):
        fail(f"report time must be HH:MM, got {value}")
    hour, minute = map(int, value.split(":"))
    if hour > 23 or minute > 59:
        fail(f"report time must be HH:MM, got {value}")
    return value


def load_watch(conn: sqlite3.Connection, watch_id: str) -> dict[str, Any]:
    row = conn.execute("SELECT data_json FROM watches WHERE id=?", (watch_id,)).fetchone()
    if not row:
        fail(f"Watch not found: {watch_id}")
    return json.loads(row["data_json"])


def save_watch(conn: sqlite3.Connection, watch: dict[str, Any]) -> None:
    ts = now_iso()
    watch.setdefault("created_at", ts)
    watch["updated_at"] = ts
    conn.execute(
        """
        INSERT INTO watches(id, name, enabled, data_json, created_at, updated_at)
        VALUES(?,?,?,?,?,?)
        ON CONFLICT(id) DO UPDATE SET
          name=excluded.name,
          enabled=excluded.enabled,
          data_json=excluded.data_json,
          updated_at=excluded.updated_at
        """,
        (
            watch["id"],
            watch["name"],
            1 if watch.get("enabled", True) else 0,
            json.dumps(watch, ensure_ascii=False, sort_keys=True),
            watch["created_at"],
            watch["updated_at"],
        ),
    )
    conn.commit()


def all_watches(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT data_json FROM watches ORDER BY created_at, id").fetchall()
    return [json.loads(row["data_json"]) for row in rows]


def usage_callback(conn: sqlite3.Connection):
    def cb(provider: str, usage_type: str, endpoint: str) -> None:
        row = conn.execute(
            "SELECT count FROM provider_usage WHERE provider=? AND day=? AND usage_type=?",
            (provider, today(), usage_type),
        ).fetchone()
        count = int(row["count"]) if row else 0
        if provider == "tourvisor" and usage_type == "billable" and count >= DEVELOPMENT_TOURVISOR_DAILY_BUDGET:
            raise RuntimeError(f"Tourvisor development daily budget exceeded: {count}")
        conn.execute(
            """
            INSERT INTO provider_usage(provider, day, usage_type, count, updated_at)
            VALUES(?,?,?,?,?)
            ON CONFLICT(provider, day, usage_type) DO UPDATE SET
              count=count+1,
              updated_at=excluded.updated_at
            """,
            (provider, today(), usage_type, 1, now_iso()),
        )
        conn.commit()

    return cb


def providers(conn: sqlite3.Connection) -> dict[str, Any]:
    env = load_env()
    cb = usage_callback(conn)
    cache = cache_dir()
    return {
        "tourvisor": TourvisorProvider(env.get("TOURVISOR_TOKEN") or os.environ.get("TOURVISOR_TOKEN"), cache, cb),
        "tez": TezTourProvider(cache, cb),
        "travelata": TravelataProvider(
            env.get("TRAVELATA_LOGIN") or os.environ.get("TRAVELATA_LOGIN"),
            env.get("TRAVELATA_PASSWORD") or os.environ.get("TRAVELATA_PASSWORD"),
            cache,
            cb,
        ),
    }


def update_provider_health(conn: sqlite3.Connection, provider: str, status: str, message: str | None = None) -> None:
    row = conn.execute("SELECT consecutive_errors FROM provider_health WHERE provider=?", (provider,)).fetchone()
    prev = int(row["consecutive_errors"]) if row else 0
    success = status == "ACTIVE"
    neutral = status in {"AUTH_REQUIRED", "ACCESS_REQUIRED", "DISABLED"}
    conn.execute(
        """
        INSERT INTO provider_health(provider, status, consecutive_errors, last_success_at, last_error_at, last_error_message, updated_at)
        VALUES(?,?,?,?,?,?,?)
        ON CONFLICT(provider) DO UPDATE SET
          status=excluded.status,
          consecutive_errors=excluded.consecutive_errors,
          last_success_at=COALESCE(excluded.last_success_at, provider_health.last_success_at),
          last_error_at=COALESCE(excluded.last_error_at, provider_health.last_error_at),
          last_error_message=COALESCE(excluded.last_error_message, provider_health.last_error_message),
          updated_at=excluded.updated_at
        """,
        (
            provider,
            status,
            0 if success or neutral else prev + 1,
            now_iso() if success else None,
            None if success or neutral else now_iso(),
            message if neutral else None if success else message,
            now_iso(),
        ),
    )
    conn.commit()


def provider_status(args: argparse.Namespace) -> None:
    with connect() as conn:
        result = []
        for name, provider in providers(conn).items():
            status = provider.healthcheck()
            update_provider_health(conn, name, status.status, status.message)
            result.append(status.__dict__ | {"capabilities": provider.capabilities()})
        emit({"ok": True, "providers": result, "db": str(db_path())})


def unique_id(conn: sqlite3.Connection, base: str) -> str:
    existing = {row["id"] for row in conn.execute("SELECT id FROM watches").fetchall()}
    if base not in existing:
        return base
    i = 2
    while f"{base}-{i}" in existing:
        i += 1
    return f"{base}-{i}"


def create_watch(args: argparse.Namespace) -> None:
    children = [int(x) for x in (args.children_ages.split(",") if args.children_ages else []) if str(x).strip()]
    if args.production and (args.adults is None):
        fail("Production watch requires explicit adults/children composition")
    with connect() as conn:
        watch_id = args.id or unique_id(conn, slugify(args.name))
        if conn.execute("SELECT 1 FROM watches WHERE id=?", (watch_id,)).fetchone():
            fail(f"Watch already exists: {watch_id}")
        watch = {
            "id": watch_id,
            "name": args.name,
            "enabled": not args.disabled,
            "departure_city": args.departure_city,
            "country": args.country,
            "resort": args.resort,
            "subregion": args.subregion,
            "hotel_ids": [x.strip() for x in args.hotel_ids.split(",")] if args.hotel_ids else [],
            "hotel_names": [x.strip() for x in args.hotel_names.split(",")] if args.hotel_names else [],
            "departure_date_from": args.departure_from,
            "departure_date_to": args.departure_to,
            "nights_min": args.nights_min,
            "nights_max": args.nights_max,
            "adults": args.adults,
            "children_ages": children,
            "meal_types": [x.strip() for x in args.meal_types.split(",")] if args.meal_types else [],
            "room_preferences": args.room_preferences,
            "package_type": args.package_type,
            "direct_flight_only": parse_bool(args.direct_flight_only),
            "charter_only": parse_bool(args.charter_only),
            "operator_allowlist": [x.strip() for x in args.operator_allowlist.split(",")] if args.operator_allowlist else [],
            "operator_blocklist": [x.strip() for x in args.operator_blocklist.split(",")] if args.operator_blocklist else [],
            "currency": args.currency.upper(),
            "alert_threshold_total": args.alert_threshold_total,
            "urgent_threshold_total": args.urgent_threshold_total,
            "poll_interval_hours": args.poll_interval_hours,
            "report_enabled": False,
            "report_days": [],
            "report_time": None,
            "report_timezone": None,
            "primary_provider": args.primary_provider,
            "cross_check_providers": [x.strip() for x in args.cross_check_providers.split(",") if x.strip()],
            "provider_mode": args.provider_mode,
            "production_ready": bool(args.production),
            "notes": args.notes,
            "created_at": now_iso(),
            "updated_at": now_iso(),
            "provider_refs": {},
        }
        save_watch(conn, watch)
        emit({"ok": True, "action": "created", "watch": watch})


def list_watches(args: argparse.Namespace) -> None:
    with connect() as conn:
        watches = all_watches(conn)
        if args.format == "markdown":
            lines = ["| id | name | enabled | route | dates | nights | providers | reports |", "|---|---|---:|---|---|---|---|---|"]
            for w in watches:
                lines.append(
                    f"| {w['id']} | {w['name']} | {w.get('enabled', True)} | {w.get('departure_city')} -> {w.get('country')} / {w.get('resort') or '-'} | {w.get('departure_date_from')}..{w.get('departure_date_to')} | {w.get('nights_min')}..{w.get('nights_max')} | {w.get('primary_provider')} + {','.join(w.get('cross_check_providers') or [])} | {w.get('report_enabled')} {','.join(w.get('report_days') or [])} {w.get('report_time') or ''} |"
                )
            print("\n".join(lines))
        else:
            emit({"ok": True, "count": len(watches), "watches": watches})


def show_watch(args: argparse.Namespace) -> None:
    with connect() as conn:
        emit({"ok": True, "watch": load_watch(conn, args.watch)})


def update_watch(args: argparse.Namespace) -> None:
    with connect() as conn:
        watch = load_watch(conn, args.watch)
        mapping = {
            "name": args.name,
            "departure_city": args.departure_city,
            "country": args.country,
            "resort": args.resort,
            "departure_date_from": args.departure_from,
            "departure_date_to": args.departure_to,
            "nights_min": args.nights_min,
            "nights_max": args.nights_max,
            "adults": args.adults,
            "currency": args.currency.upper() if args.currency else None,
            "alert_threshold_total": args.alert_threshold_total,
            "urgent_threshold_total": args.urgent_threshold_total,
            "poll_interval_hours": args.poll_interval_hours,
            "provider_mode": args.provider_mode,
        }
        for key, value in mapping.items():
            if value is not None:
                watch[key] = value
        if args.enabled is not None:
            watch["enabled"] = bool(parse_bool(args.enabled))
        if args.direct_flight_only is not None:
            watch["direct_flight_only"] = bool(parse_bool(args.direct_flight_only))
        if args.hotel_names is not None:
            watch["hotel_names"] = [x.strip() for x in args.hotel_names.split(",") if x.strip()]
        save_watch(conn, watch)
        emit({"ok": True, "action": "updated", "watch": watch})


def schedule_watch(args: argparse.Namespace) -> None:
    with connect() as conn:
        watch = load_watch(conn, args.watch)
        if args.show:
            needs = bool(watch.get("report_enabled")) and not (watch.get("report_days") and watch.get("report_time") and watch.get("report_timezone"))
            emit({
                "ok": True,
                "action": "schedule_show",
                "watch_id": watch["id"],
                "name": watch["name"],
                "report_enabled": bool(watch.get("report_enabled")),
                "report_days": watch.get("report_days") or [],
                "report_time": watch.get("report_time"),
                "report_timezone": watch.get("report_timezone"),
                "needs_clarification": needs,
            })
            return
        if args.enable is not None:
            watch["report_enabled"] = bool(parse_bool(args.enable))
        days = parse_days(args.days)
        if days is not None:
            watch["report_days"] = days
        if args.time is not None:
            watch["report_time"] = parse_time(args.time)
        if args.timezone is not None:
            watch["report_timezone"] = args.timezone
        save_watch(conn, watch)
        emit({"ok": True, "action": "schedule_updated", "watch": watch})


def pause_resume(args: argparse.Namespace, enabled: bool) -> None:
    with connect() as conn:
        watch = load_watch(conn, args.watch)
        watch["enabled"] = enabled
        save_watch(conn, watch)
        emit({"ok": True, "action": "resumed" if enabled else "paused", "watch_id": watch["id"]})


def delete_watch(args: argparse.Namespace) -> None:
    with connect() as conn:
        if not conn.execute("SELECT 1 FROM watches WHERE id=?", (args.watch,)).fetchone():
            fail(f"Watch not found: {args.watch}")
        conn.execute("DELETE FROM watches WHERE id=?", (args.watch,))
        conn.commit()
        emit({"ok": True, "action": "deleted", "watch_id": args.watch})


def resolve_refs_for_provider(provider, watch: dict[str, Any]) -> dict[str, Any]:
    refs: dict[str, Any] = {}
    dep = provider.resolve_departure(watch["departure_city"])
    if dep:
        refs["departure_id"] = dep.get("id") or dep.get("cityId")
        refs["departure_name"] = dep.get("name")
    country = provider.resolve_country(watch["country"], refs.get("departure_id"))
    if country:
        refs["country_id"] = country.get("id") or country.get("countryId")
        refs["country_name"] = country.get("name")
    if refs.get("country_id") and watch.get("resort"):
        region = provider.resolve_region(refs["country_id"], watch["resort"])
        if region:
            refs["region_id"] = (region.get("tourId") or [None])[0] if isinstance(region.get("tourId"), list) else region.get("id") or region.get("regionId")
            refs["region_name"] = region.get("name")
    if refs.get("country_id") and watch.get("hotel_names"):
        ids = []
        for query in watch.get("hotel_names") or []:
            hotels = provider.resolve_hotels(refs["country_id"], refs.get("region_id"), query)
            if len(hotels) == 1:
                ids.append(str(hotels[0].get("id") or hotels[0].get("hotelId")))
        if ids:
            refs["hotel_ids"] = ids
    if hasattr(provider, "accommodation_id") and refs.get("departure_id") and refs.get("country_id"):
        children_ages = [int(age) for age in (watch.get("children_ages") or [])]
        refs["accommodation_id"] = provider.accommodation_id(
            int(refs["departure_id"]),
            int(refs["country_id"]),
            int(watch.get("adults") or 2),
            children_ages,
        )
    return refs


def resolve_watch(args: argparse.Namespace) -> None:
    with connect() as conn:
        watch = load_watch(conn, args.watch)
        registry = providers(conn)
        resolved = {}
        for name in [watch.get("primary_provider"), *(watch.get("cross_check_providers") or [])]:
            if name not in registry:
                continue
            try:
                resolved[name] = resolve_refs_for_provider(registry[name], watch)
            except Exception as exc:
                resolved[name] = {"error": f"{type(exc).__name__}: {exc}"}
        watch["provider_refs"] = resolved
        save_watch(conn, watch)
        emit({"ok": True, "watch_id": watch["id"], "provider_refs": resolved})


def canonical_hotel_id(provider: str, hotel_id: str | None, hotel_name: str | None, resort: str | None) -> str:
    if not hotel_name:
        return f"unknown-{provider}-{hotel_id or 'hotel'}"
    return slugify(f"{normalize_name(hotel_name)}-{normalize_name(resort)}")


def offer_key(offer: dict[str, Any]) -> str:
    parts = [
        offer.get("provider"),
        offer.get("operator"),
        offer.get("hotel_id"),
        offer.get("departure_date"),
        offer.get("nights"),
        normalize_name(offer.get("meal")),
        normalize_name(offer.get("room")),
        offer.get("tour_id"),
    ]
    return "|".join(str(x or "") for x in parts)


def comparison_key(offer: dict[str, Any]) -> str:
    parts = [
        offer.get("canonical_hotel_id"),
        offer.get("departure_date"),
        offer.get("nights"),
        offer.get("adults"),
        ",".join(map(str, offer.get("children_ages") or [])),
        normalize_name(offer.get("meal")),
        normalize_name(offer.get("room")),
    ]
    return "|".join(str(x or "") for x in parts)


def store_offers(conn: sqlite3.Connection, watch_id: str, offers: list[dict[str, Any]]) -> None:
    for offer in offers:
        if not offer.get("canonical_hotel_id"):
            offer["canonical_hotel_id"] = canonical_hotel_id(offer.get("provider"), offer.get("hotel_id"), offer.get("hotel_name"), offer.get("resort"))
        ok = offer_key(offer)
        ck = comparison_key(offer)
        conn.execute(
            """
            INSERT INTO offers(
              watch_id, offer_key, comparison_key, provider, operator, hotel_id, canonical_hotel_id, hotel_name, resort, subregion,
              hotel_category, departure_date, return_date, nights, adults, children_ages, meal, room, flight_out, flight_back,
              direct_flight, package_type, quoted_price, actualized_price, currency, availability, deeplink, tour_id, observed_at,
              actualized_at, raw_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                watch_id, ok, ck, offer.get("provider"), offer.get("operator"), offer.get("hotel_id"), offer.get("canonical_hotel_id"),
                offer.get("hotel_name"), offer.get("resort"), offer.get("subregion"), offer.get("hotel_category"), offer.get("departure_date"),
                offer.get("return_date"), offer.get("nights"), offer.get("adults"), json.dumps(offer.get("children_ages") or [], ensure_ascii=False),
                offer.get("meal"), offer.get("room"), offer.get("flight_out"), offer.get("flight_back"),
                None if offer.get("direct_flight") is None else (1 if offer.get("direct_flight") else 0),
                offer.get("package_type"), offer.get("quoted_price"), offer.get("actualized_price"), offer.get("currency"),
                offer.get("availability"), offer.get("deeplink"), offer.get("tour_id"), offer.get("observed_at"), offer.get("actualized_at"),
                json.dumps(offer, ensure_ascii=False, sort_keys=True),
            ),
        )
        conn.execute(
            """
            INSERT INTO price_history(watch_id, comparison_key, provider, operator, hotel_name, observed_at, quoted_price, actualized_price, availability)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (watch_id, ck, offer.get("provider"), offer.get("operator"), offer.get("hotel_name"), offer.get("observed_at"), offer.get("quoted_price"), offer.get("actualized_price"), offer.get("availability")),
        )
    conn.commit()


def latest_saved_offers(conn: sqlite3.Connection, watch_id: str, limit: int) -> tuple[str | None, list[dict[str, Any]]]:
    row = conn.execute("SELECT MAX(observed_at) AS observed_at FROM offers WHERE watch_id=?", (watch_id,)).fetchone()
    observed_at = row["observed_at"] if row else None
    if not observed_at:
        return None, []
    rows = conn.execute(
        """
        SELECT raw_json FROM offers
        WHERE watch_id=? AND observed_at=?
        ORDER BY COALESCE(actualized_price, quoted_price, 1000000000000) ASC
        LIMIT ?
        """,
        (watch_id, observed_at, limit),
    ).fetchall()
    offers = [json.loads(item["raw_json"]) for item in rows]
    return observed_at, offers


def error_payload(provider: str, exc: Exception) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "provider": provider,
        "type": type(exc).__name__,
        "temporary": isinstance(exc, (TezTemporaryError, TravelataTemporaryError, TravelataQuotaError)),
        "error": f"{type(exc).__name__}: {exc}",
    }
    for attr in ("endpoint", "params", "status_code", "attempts"):
        if hasattr(exc, attr):
            payload[attr] = getattr(exc, attr)
    return payload


def providers_for_check(watch: dict[str, Any], registry: dict[str, Any], exhaustive: bool) -> list[str]:
    if exhaustive or watch.get("provider_mode") == "exhaustive":
        return [name for name, provider in registry.items() if provider.healthcheck().active]
    names = [watch.get("primary_provider")]
    primary_status = registry[names[0]].healthcheck() if names[0] in registry else None
    if primary_status and not primary_status.active:
        names.extend(watch.get("cross_check_providers") or [])
    elif args_force_cross_check := False:
        names.extend(watch.get("cross_check_providers") or [])
    return [name for name in names if name in registry]


def check_watch(args: argparse.Namespace) -> None:
    with connect() as conn:
        watch = load_watch(conn, args.watch)
        if not watch.get("provider_refs"):
            registry = providers(conn)
            watch["provider_refs"] = {}
            for name in [watch.get("primary_provider"), *(watch.get("cross_check_providers") or [])]:
                if name in registry:
                    try:
                        watch["provider_refs"][name] = resolve_refs_for_provider(registry[name], watch)
                    except Exception as exc:
                        watch["provider_refs"][name] = {"error": f"{type(exc).__name__}: {exc}"}
            save_watch(conn, watch)
        registry = providers(conn)
        if args.exhaustive:
            selected = [name for name, provider in registry.items() if provider.healthcheck().active]
        elif args.provider:
            selected = [args.provider]
        else:
            primary = watch.get("primary_provider", "tourvisor")
            status = registry[primary].healthcheck() if primary in registry else None
            selected = [primary] if status and status.active else []
            if not selected:
                selected = [name for name in watch.get("cross_check_providers", []) if name in registry and registry[name].healthcheck().active]
            if not selected:
                selected = [name for name, provider in registry.items() if name != primary and provider.healthcheck().active]
        all_offers: list[dict[str, Any]] = []
        errors = []
        started = now_iso()
        for name in selected:
            try:
                refs = watch.setdefault("provider_refs", {})
                if name in registry and (not refs.get(name) or refs.get(name, {}).get("error")):
                    refs[name] = resolve_refs_for_provider(registry[name], watch)
                    save_watch(conn, watch)
                if getattr(args, "simulate_temporary_error", False) and name == "tez":
                    raise TezTemporaryError(
                        "TEZ temporary HTTP 502: simulated Bad Gateway",
                        endpoint="/getResult",
                        params={"simulation": True},
                        status_code=502,
                        attempts=3,
                    )
                offers = [offer.to_dict() for offer in registry[name].search(watch, exhaustive=args.exhaustive)]
                all_offers.extend(offers)
                update_provider_health(conn, name, "ACTIVE")
            except Exception as exc:
                update_provider_health(conn, name, "ERROR", str(exc)[:500])
                errors.append(error_payload(name, exc))
        all_offers = dedupe_offers(all_offers)
        store_offers(conn, watch["id"], all_offers)
        top = sorted(all_offers, key=lambda x: x.get("actualized_price") or x.get("quoted_price") or 10**12)[: args.top]
        stale_observed_at = None
        stale_top: list[dict[str, Any]] = []
        user_message = None
        temporary_provider_error = any(item.get("temporary") for item in errors)
        if temporary_provider_error and not all_offers:
            stale_observed_at, stale_top = latest_saved_offers(conn, watch["id"], args.top)
            user_message = "TEZ TOUR временно недоступен, свежий срез получить нельзя. Повторите позже."
        conn.execute(
            "INSERT INTO runs(run_type, started_at, completed_at, status, data_json) VALUES(?,?,?,?,?)",
            (
                "manual_check",
                started,
                now_iso(),
                "ok" if all_offers else "provider_temporary_error" if temporary_provider_error else "partial" if errors else "empty",
                json.dumps({"watch_id": watch["id"], "providers": selected, "errors": errors, "offers": len(all_offers), "stale_observed_at": stale_observed_at}, ensure_ascii=False),
            ),
        )
        conn.commit()
        payload: dict[str, Any] = {
            "ok": bool(all_offers) or bool(errors),
            "watch_id": watch["id"],
            "providers_used": selected,
            "offers_found": len(all_offers),
            "errors": errors,
            "top": top,
        }
        if user_message:
            payload["provider_status"] = "temporary_error"
            payload["user_message"] = user_message
            payload["stale_observed_at"] = stale_observed_at
            payload["stale_top"] = stale_top
        emit(payload)


def dedupe_offers(offers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: dict[str, dict[str, Any]] = {}
    for offer in offers:
        if not offer.get("canonical_hotel_id"):
            offer["canonical_hotel_id"] = canonical_hotel_id(offer.get("provider"), offer.get("hotel_id"), offer.get("hotel_name"), offer.get("resort"))
        key = offer_key(offer)
        prev = seen.get(key)
        price = offer.get("actualized_price") or offer.get("quoted_price") or 10**12
        prev_price = (prev or {}).get("actualized_price") or (prev or {}).get("quoted_price") or 10**12
        if prev is None or price < prev_price:
            seen[key] = offer
    return list(seen.values())


def coverage(args: argparse.Namespace) -> None:
    with connect() as conn:
        registry = providers(conn)
        result: dict[str, Any] = {}
        for name, provider in registry.items():
            status = provider.healthcheck()
            entry: dict[str, Any] = {"status": status.__dict__, "operators": [], "regions": [], "hotels_count": None, "coverage": {}}
            try:
                dep = provider.resolve_departure(args.departure_city)
                country = provider.resolve_country(args.country, (dep or {}).get("id") or (dep or {}).get("cityId"))
                if dep and country:
                    country_id = country.get("id") or country.get("countryId")
                    dep_id = dep.get("id") or dep.get("cityId")
                    entry["operators"] = provider.list_operators(dep_id, country_id)
                    if args.resort:
                        region = provider.resolve_region(country_id, args.resort)
                        entry["region"] = region
                        region_id = None
                        if region:
                            region_id = (region.get("tourId") or [None])[0] if isinstance(region.get("tourId"), list) else region.get("id") or region.get("regionId")
                        hotels = provider.resolve_hotels(country_id, region_id, None)
                        entry["hotels_count"] = len(hotels)
                targets = ["PEGAS", "ANEX", "Coral", "FUN&SUN", "Sunmar", "Библио", "TEZ", "Intourist"]
                names = " | ".join(json.dumps(op, ensure_ascii=False) for op in entry.get("operators") or [])
                for target in targets:
                    entry["coverage"][target] = "FOUND" if target.casefold() in names.casefold() else f"не обнаружен в доступной выдаче {name} для Moscow/Egypt"
            except Exception as exc:
                entry["error"] = f"{type(exc).__name__}: {exc}"
            result[name] = entry
        emit({"ok": True, "coverage": result})


def hotels(args: argparse.Namespace) -> None:
    with connect() as conn:
        provider = providers(conn)[args.provider]
        dep = provider.resolve_departure(args.departure_city)
        country = provider.resolve_country(args.country, (dep or {}).get("id") or (dep or {}).get("cityId"))
        if not country:
            fail("Country not resolved")
        country_id = country.get("id") or country.get("countryId")
        region_id = None
        if args.resort:
            region = provider.resolve_region(country_id, args.resort)
            if region:
                region_id = (region.get("tourId") or [None])[0] if isinstance(region.get("tourId"), list) else region.get("id") or region.get("regionId")
        rows = provider.resolve_hotels(country_id, region_id, args.query)
        emit({"ok": True, "provider": args.provider, "count": len(rows), "hotels": rows[: args.limit]})


def operators(args: argparse.Namespace) -> None:
    with connect() as conn:
        provider = providers(conn)[args.provider]
        dep = provider.resolve_departure(args.departure_city)
        country = provider.resolve_country(args.country, (dep or {}).get("id") or (dep or {}).get("cityId"))
        rows = provider.list_operators((dep or {}).get("id") or (dep or {}).get("cityId"), (country or {}).get("id") or (country or {}).get("countryId"))
        emit({"ok": True, "provider": args.provider, "count": len(rows), "operators": rows})


def history(args: argparse.Namespace) -> None:
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT observed_at, provider, operator, hotel_name, quoted_price, actualized_price, availability
            FROM price_history WHERE watch_id=?
            ORDER BY observed_at DESC LIMIT ?
            """,
            (args.watch, args.limit),
        ).fetchall()
        emit({"ok": True, "watch_id": args.watch, "count": len(rows), "history": [dict(row) for row in rows]})


def add_common_create_fields(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--id")
    parser.add_argument("--name", required=True)
    parser.add_argument("--departure-city", required=True)
    parser.add_argument("--country", required=True)
    parser.add_argument("--resort")
    parser.add_argument("--subregion")
    parser.add_argument("--hotel-ids", default="")
    parser.add_argument("--hotel-names", default="")
    parser.add_argument("--departure-from", required=True)
    parser.add_argument("--departure-to", required=True)
    parser.add_argument("--nights-min", type=int, required=True)
    parser.add_argument("--nights-max", type=int, required=True)
    parser.add_argument("--adults", type=int)
    parser.add_argument("--children-ages", default="")
    parser.add_argument("--meal-types", default="")
    parser.add_argument("--room-preferences")
    parser.add_argument("--package-type", default="full_package")
    parser.add_argument("--direct-flight-only", default="false")
    parser.add_argument("--charter-only", default="false")
    parser.add_argument("--operator-allowlist", default="")
    parser.add_argument("--operator-blocklist", default="")
    parser.add_argument("--currency", default="RUB")
    parser.add_argument("--alert-threshold-total", type=int)
    parser.add_argument("--urgent-threshold-total", type=int)
    parser.add_argument("--poll-interval-hours", type=int, default=6)
    parser.add_argument("--primary-provider", default="tourvisor")
    parser.add_argument("--cross-check-providers", default="tez,travelata")
    parser.add_argument("--provider-mode", choices=["smart", "exhaustive"], default="smart")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--disabled", action="store_true")
    parser.add_argument("--notes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db")
    ps = sub.add_parser("providers")
    ps.set_defaults(func=provider_status)
    ps2 = sub.add_parser("provider-status")
    ps2.set_defaults(func=provider_status)
    c = sub.add_parser("create")
    add_common_create_fields(c)
    c.set_defaults(func=create_watch)
    l = sub.add_parser("list")
    l.add_argument("--format", choices=["json", "markdown"], default="json")
    l.set_defaults(func=list_watches)
    s = sub.add_parser("show")
    s.add_argument("--watch", required=True)
    s.set_defaults(func=show_watch)
    u = sub.add_parser("update")
    u.add_argument("--watch", required=True)
    u.add_argument("--name")
    u.add_argument("--departure-city")
    u.add_argument("--country")
    u.add_argument("--resort")
    u.add_argument("--hotel-names")
    u.add_argument("--departure-from", dest="departure_from")
    u.add_argument("--departure-to", dest="departure_to")
    u.add_argument("--nights-min", type=int)
    u.add_argument("--nights-max", type=int)
    u.add_argument("--adults", type=int)
    u.add_argument("--currency")
    u.add_argument("--alert-threshold-total", type=int)
    u.add_argument("--urgent-threshold-total", type=int)
    u.add_argument("--poll-interval-hours", type=int)
    u.add_argument("--provider-mode", choices=["smart", "exhaustive"])
    u.add_argument("--direct-flight-only")
    u.add_argument("--enabled")
    u.set_defaults(func=update_watch)
    sch = sub.add_parser("schedule")
    sch.add_argument("--watch", required=True)
    sch.add_argument("--show", action="store_true")
    sch.add_argument("--enable")
    sch.add_argument("--days")
    sch.add_argument("--time")
    sch.add_argument("--timezone")
    sch.set_defaults(func=schedule_watch)
    p = sub.add_parser("pause")
    p.add_argument("--watch", required=True)
    p.set_defaults(func=lambda args: pause_resume(args, False))
    r = sub.add_parser("resume")
    r.add_argument("--watch", required=True)
    r.set_defaults(func=lambda args: pause_resume(args, True))
    d = sub.add_parser("delete")
    d.add_argument("--watch", required=True)
    d.set_defaults(func=delete_watch)
    rw = sub.add_parser("resolve")
    rw.add_argument("--watch", required=True)
    rw.set_defaults(func=resolve_watch)
    chk = sub.add_parser("check")
    chk.add_argument("--watch", required=True)
    chk.add_argument("--provider")
    chk.add_argument("--exhaustive", action="store_true")
    chk.add_argument("--top", type=int, default=10)
    chk.add_argument("--simulate-temporary-error", action="store_true")
    chk.set_defaults(func=check_watch)
    cov = sub.add_parser("coverage")
    cov.add_argument("--departure-city", default="Москва")
    cov.add_argument("--country", default="Турция")
    cov.add_argument("--resort", default="Анталья")
    cov.set_defaults(func=coverage)
    h = sub.add_parser("hotels")
    h.add_argument("--provider", choices=["tourvisor", "tez", "travelata"], default="tez")
    h.add_argument("--departure-city", default="Москва")
    h.add_argument("--country", default="Турция")
    h.add_argument("--resort")
    h.add_argument("--query")
    h.add_argument("--limit", type=int, default=20)
    h.set_defaults(func=hotels)
    o = sub.add_parser("operators")
    o.add_argument("--provider", choices=["tourvisor", "tez", "travelata"], default="tez")
    o.add_argument("--departure-city", default="Москва")
    o.add_argument("--country", default="Турция")
    o.set_defaults(func=operators)
    hist = sub.add_parser("history")
    hist.add_argument("--watch", required=True)
    hist.add_argument("--limit", type=int, default=20)
    hist.set_defaults(func=history)
    args = parser.parse_args()
    if args.command == "init-db":
        with connect():
            pass
        emit({"ok": True, "db": str(db_path()), "state_dir": str(db_path().parent)})
        return 0
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
