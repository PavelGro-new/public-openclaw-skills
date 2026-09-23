#!/usr/bin/env python3
"""Scheduled alerts, reports, provider health, and watchdog for tour-price-watch."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import tour_watch  # noqa: E402


DEFAULT_LOCK = Path("/opt/openclaw-state/tour-price-watch/tour-alerts.lock")
DEFAULT_LOG = Path(os.environ.get("TOUR_WATCH_LOG_FILE") or "/opt/openclaw-logs/tour-price-watch.log")
PRICE_DROP_RUB = 5000
STALE_AFTER_HOURS = 8
HEALTH_WARNING_ERRORS = 2
HEALTH_DOWN_ERRORS = 3


def now_utc() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_iso() -> str:
    return now_utc().isoformat().replace("+00:00", "Z")


def parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def load_runtime_env() -> dict[str, str]:
    return tour_watch.load_env(tour_watch.SECRET_FILE)


def telegram_target() -> str | None:
    env = load_runtime_env()
    return env.get("TOUR_WATCH_TELEGRAM_TARGET") or env.get("AVIASALES_TELEGRAM_TARGET") or os.environ.get("TOUR_WATCH_TELEGRAM_TARGET")


def send_telegram(message: str, target: str | None, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True}
    if not target:
        return {"ok": False, "error": "Telegram target is not configured"}
    result = subprocess.run(
        ["openclaw", "message", "send", "--channel", "telegram", "--target", str(target), "--message", message, "--json"],
        text=True,
        capture_output=True,
        check=False,
    )
    return {"ok": result.returncode == 0, "returncode": result.returncode, "stderr": result.stderr.strip()[-300:]}


def append_log(event: dict[str, Any]) -> None:
    DEFAULT_LOG.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def alert_state(conn, key: str) -> dict[str, Any]:
    row = conn.execute("SELECT data_json FROM alert_state WHERE watch_id=?", (key,)).fetchone()
    if not row:
        return {}
    return json.loads(row["data_json"])


def save_alert_state(conn, key: str, data: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO alert_state(watch_id, data_json, updated_at)
        VALUES(?,?,?)
        ON CONFLICT(watch_id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at
        """,
        (key, json.dumps(data, ensure_ascii=False, sort_keys=True), now_iso()),
    )
    conn.commit()


def best_price(offer: dict[str, Any]) -> int | None:
    value = offer.get("actualized_price") or offer.get("quoted_price")
    return int(value) if value is not None else None


def status_level(watch: dict[str, Any], total: int | None) -> str | None:
    if total is None:
        return None
    urgent = watch.get("urgent_threshold_total")
    alert = watch.get("alert_threshold_total")
    if urgent is not None and total <= int(urgent):
        return "urgent"
    if alert is not None and total <= int(alert):
        return "alert"
    return None


def level_label(level: str) -> str:
    return "🔥 Срочно проверить" if level == "urgent" else "🟠 Хорошая цена"


def status_text(watch: dict[str, Any], price: int | None) -> str:
    level = status_level(watch, price)
    if level == "urgent":
        return "срочная цена"
    if level == "alert":
        return "хорошая цена"
    return "выше обычного порога"


def coverage_label(watch: dict[str, Any], offers: list[dict[str, Any]] | None = None) -> str:
    providers = sorted({offer.get("provider") for offer in offers or [] if offer.get("provider")})
    if providers == ["tez"] or (not providers and watch.get("primary_provider") == "tez"):
        return "TEZ TOUR"
    if providers:
        return ", ".join(providers)
    active = [watch.get("primary_provider"), *(watch.get("cross_check_providers") or [])]
    return ", ".join([item for item in active if item]) or "-"


def offer_short(offer: dict[str, Any] | None) -> list[str]:
    if not offer:
        return ["-"]
    return [
        f"{offer.get('hotel_name') or '-'}",
        f"{offer.get('operator') or '-'} / {offer.get('provider')}",
        f"{offer.get('departure_date')} -> {offer.get('return_date')} / {offer.get('nights')} ночей",
        f"{offer.get('meal') or '-'} / {offer.get('room') or '-'}",
        f"{best_price(offer)} {offer.get('currency') or 'RUB'}",
    ]


def format_price_alert(watch: dict[str, Any], offer: dict[str, Any], level: str, previous: int | None) -> str:
    current = best_price(offer)
    change = "-" if previous is None or current is None else f"{current - previous:+d} RUB"
    lines = [
        "🏖 GROMIK: туры",
        "",
        level_label(level),
        "",
        "Watch:",
        watch["name"],
        "",
        "Отель:",
        offer.get("hotel_name") or "-",
        "",
        "Оператор:",
        offer.get("operator") or "-",
        "",
        "Sources:",
        offer.get("provider") or "-",
        "",
        "Покрытие этого наблюдения сейчас:",
        coverage_label(watch, [offer]),
        "",
        "Даты:",
        f"{offer.get('departure_date')} -> {offer.get('return_date')}",
        "",
        "Ночей:",
        str(offer.get("nights") or "-"),
        "",
        "Состав:",
        f"{watch.get('adults')} adults, children ages: {watch.get('children_ages') or []}",
        "",
        "Питание:",
        offer.get("meal") or "-",
        "",
        "Номер:",
        offer.get("room") or "-",
        "",
        "Цена:",
        f"{current} {offer.get('currency') or 'RUB'}",
        "",
        "Цена актуализирована:",
        "да" if offer.get("actualized_price") else "нет",
        "",
        "Было:",
        str(previous) if previous is not None else "-",
        "",
        "Изменение:",
        change,
        "",
        "Перелёт:",
        offer.get("flight_out") or "-",
        "",
        "Ссылка:",
        offer.get("deeplink") or "-",
    ]
    return "\n".join(lines)


def format_report(watch: dict[str, Any], offers: list[dict[str, Any]], report_state: dict[str, Any], health: dict[str, Any]) -> str:
    top = offers[:3]
    current = best_price(top[0]) if top else None
    previous = report_state.get("last_report_price_total")
    delta = "-" if previous is None or current is None else f"{current - int(previous):+d} RUB"
    lines = [
        "🏖 GROMIK: плановый отчёт по турам",
        "",
        "Watch:",
        watch["name"],
        "",
        "Лучший вариант:",
        *offer_short(top[0] if top else None),
        "",
        "TOP-3:",
    ]
    for i, offer in enumerate(top, start=1):
        lines.append(f"{i}. {offer.get('hotel_name')} — {best_price(offer)} {offer.get('currency') or 'RUB'}")
    lines.extend(
        [
            "",
            "Изменение с прошлого отчёта:",
            delta,
            "",
            "Исторический минимум:",
            str(report_state.get("best_price_seen") or "-"),
            "",
            "Provider coverage:",
            coverage_label(watch, offers),
            "",
            "Health:",
            json.dumps(health, ensure_ascii=False),
            "",
            "Статус:",
            status_text(watch, current),
        ]
    )
    return "\n".join(lines)


def due_report(watch: dict[str, Any], state: dict[str, Any], force: bool) -> bool:
    if force:
        return True
    if not watch.get("report_enabled"):
        return False
    days = watch.get("report_days") or []
    time_value = watch.get("report_time")
    tz_name = watch.get("report_timezone")
    if not days or not time_value or not tz_name:
        return False
    try:
        tz = ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return False
    local = now_utc().astimezone(tz)
    if local.strftime("%A") not in days:
        return False
    hour, minute = map(int, time_value.split(":"))
    scheduled = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local < scheduled:
        return False
    last = parse_dt(state.get("last_report_at"))
    return not (last and last.astimezone(tz).date() == local.date())


def load_latest_offers(conn, watch_id: str, limit: int = 100) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT raw_json FROM offers
        WHERE watch_id=? AND observed_at=(SELECT MAX(observed_at) FROM offers WHERE watch_id=?)
        ORDER BY COALESCE(actualized_price, quoted_price, 999999999) ASC
        LIMIT ?
        """,
        (watch_id, watch_id, limit),
    ).fetchall()
    return [json.loads(row["raw_json"]) for row in rows]


def health_message(provider: str, status: str, row: dict[str, Any] | None, watch: dict[str, Any] | None = None, offers: list[dict[str, Any]] | None = None) -> str:
    if status == "warning":
        title = "⚠️ Мониторинг туров работает нестабильно"
    elif status == "down":
        title = "🔴 Мониторинг туров не работает"
    else:
        title = "✅ Мониторинг туров восстановлен"
    lines = [
        title,
        "",
        "Provider:",
        provider,
        "",
        "Watch:",
        (watch or {}).get("name") or "-",
        "",
        "Последняя успешная проверка:",
        (row or {}).get("last_success_at") or "-",
        "",
        "Последняя ошибка:",
        (row or {}).get("last_error_message") or "-",
    ]
    if offers:
        best = offers[0]
        lines.extend(["", "Текущий лучший вариант:", *offer_short(best)])
    return "\n".join(lines)


def process_provider_health_alerts(conn, watch: dict[str, Any], provider_names: list[str], offers: list[dict[str, Any]], target: str | None, dry_run: bool) -> list[dict[str, Any]]:
    events = []
    for provider in provider_names:
        if provider == "tourvisor":
            continue
        row = conn.execute("SELECT * FROM provider_health WHERE provider=?", (provider,)).fetchone()
        if not row:
            continue
        data = dict(row)
        state_key = "__provider_health__" + provider
        state = alert_state(conn, state_key)
        next_state = None
        if data["status"] == "ACTIVE":
            if state.get("level") in {"warning", "down"}:
                result = send_telegram(health_message(provider, "recovery", data, watch, offers), target, dry_run)
                if result.get("ok") and not dry_run:
                    state["level"] = "healthy"
                    state["last_recovery_at"] = now_iso()
                    save_alert_state(conn, state_key, state)
                events.append({"type": "provider_recovery", "provider": provider, "sent": result.get("ok")})
            continue
        errors = int(data.get("consecutive_errors") or 0)
        if errors >= HEALTH_DOWN_ERRORS:
            next_state = "down"
        elif errors >= HEALTH_WARNING_ERRORS:
            next_state = "warning"
        if next_state and state.get("level") != next_state:
            result = send_telegram(health_message(provider, next_state, data, watch, offers), target, dry_run)
            if result.get("ok") and not dry_run:
                state["level"] = next_state
                state["last_alert_at"] = now_iso()
                save_alert_state(conn, state_key, state)
            events.append({"type": "provider_health", "provider": provider, "level": next_state, "sent": result.get("ok")})
    return events


def run_check(conn, watch: dict[str, Any], exhaustive: bool, simulate_primary_failure: bool, simulate_provider_error: str | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any], list[str]]:
    registry = tour_watch.providers(conn)
    errors = []
    provider_health = {}
    primary = watch.get("primary_provider", "tourvisor")
    selected: list[str] = []
    if not simulate_primary_failure and primary in registry and registry[primary].healthcheck().active:
        selected.append(primary)
    else:
        if primary in registry:
            provider_health[primary] = "simulated_failure" if simulate_primary_failure else registry[primary].healthcheck().status
    if exhaustive:
        selected = [name for name, provider in registry.items() if provider.healthcheck().active]
    if not selected:
        selected = [name for name in watch.get("cross_check_providers", []) if name in registry and registry[name].healthcheck().active]
    if not selected:
        selected = [name for name, provider in registry.items() if name != primary and provider.healthcheck().active]
    offers: list[dict[str, Any]] = []
    for name in selected:
        try:
            if simulate_provider_error == name:
                if name == "tez":
                    raise tour_watch.TezTemporaryError(
                        "TEZ temporary HTTP 502: simulated Bad Gateway",
                        endpoint="/getResult",
                        params={"simulation": True},
                        status_code=502,
                        attempts=3,
                    )
                raise RuntimeError(f"simulated {name} provider error")
            rows = [offer.to_dict() for offer in registry[name].search(watch, exhaustive=exhaustive)]
            provider_health[name] = "ACTIVE"
            offers.extend(rows)
            tour_watch.update_provider_health(conn, name, "ACTIVE")
        except Exception as exc:
            provider_health[name] = "ERROR"
            tour_watch.update_provider_health(conn, name, "ERROR", str(exc)[:500])
            errors.append(tour_watch.error_payload(name, exc))
    offers = tour_watch.dedupe_offers(offers)
    tour_watch.store_offers(conn, watch["id"], offers)
    return sorted(offers, key=lambda x: best_price(x) or 10**12), errors, provider_health, selected


def process_watch(conn, watch: dict[str, Any], args: argparse.Namespace, target: str | None) -> dict[str, Any]:
    state = alert_state(conn, watch["id"])
    offers, errors, health, selected_providers = run_check(conn, watch, args.exhaustive, args.simulate_primary_failure, args.simulate_provider_error)
    event_log = []
    best = offers[0] if offers else None
    current = best_price(best) if best else None
    previous = state.get("last_alert_price_total")
    level = status_level(watch, current)
    should_alert = False
    if level and current is not None:
        if state.get("last_alert_sent_at") is None:
            should_alert = True
        elif level == "urgent" and state.get("last_alert_level") != "urgent":
            should_alert = True
        elif previous is not None and current <= int(previous) - max(PRICE_DROP_RUB, int(previous) * 2 // 100):
            should_alert = True
        elif state.get("best_price_seen") is None or current < int(state["best_price_seen"]):
            should_alert = True
    if should_alert and best:
        result = send_telegram(format_price_alert(watch, best, level or "alert", previous), target, args.dry_run)
        if result.get("ok") and not args.dry_run:
            state["last_alert_sent_at"] = now_iso()
            state["last_alert_price_total"] = current
            state["last_alert_level"] = level
            state["last_alert_offer_key"] = tour_watch.offer_key(best)
        event_log.append({"type": "price_alert", "sent": result.get("ok"), "level": level})
    if current is not None:
        state["best_price_seen"] = current if state.get("best_price_seen") is None else min(int(state["best_price_seen"]), current)
    event_log.extend(process_provider_health_alerts(conn, watch, selected_providers, offers, target, args.dry_run))
    report = alert_state(conn, "__report__" + watch["id"])
    if due_report(watch, report, args.force_report):
        result = send_telegram(format_report(watch, offers, report, health), target, args.dry_run)
        if result.get("ok") and not args.dry_run:
            report["last_report_at"] = now_iso()
            report["last_report_price_total"] = current
            report["last_report_offer_key"] = tour_watch.offer_key(best) if best else None
            if current is not None:
                report["best_price_seen"] = current if report.get("best_price_seen") is None else min(int(report["best_price_seen"]), current)
            save_alert_state(conn, "__report__" + watch["id"], report)
        event_log.append({"type": "scheduled_report", "sent": result.get("ok")})
    save_alert_state(conn, watch["id"], state)
    return {"watch_id": watch["id"], "offers": len(offers), "errors": errors, "events": event_log, "best_price": current, "health": health}


def active_watches(conn, watch_filter: str | None = None, include_explicit: bool = False) -> list[dict[str, Any]]:
    watches = []
    for watch in tour_watch.all_watches(conn):
        if watch_filter and watch["id"] != watch_filter:
            continue
        if watch.get("enabled", True) or (include_explicit and watch_filter):
            watches.append(watch)
    return watches


def run(args: argparse.Namespace) -> None:
    target = telegram_target()
    started = now_iso()
    DEFAULT_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with DEFAULT_LOCK.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        with tour_watch.connect() as conn:
            checks = []
            watches_to_check = active_watches(conn, args.watch, include_explicit=True)
            if not watches_to_check:
                conn.execute(
                    "INSERT INTO runs(run_type, started_at, completed_at, status, data_json) VALUES(?,?,?,?,?)",
                    ("scheduled_check", started, now_iso(), "idle", json.dumps({"checks": [], "idle": True, "reason": "idle: no active watches, skipped external provider calls"}, ensure_ascii=False)),
                )
                conn.commit()
                append_log({"at": now_iso(), "event": "tour_alerts_run", "idle": True, "reason": "idle: no active watches, skipped external provider calls"})
                tour_watch.emit({"ok": True, "idle": True, "reason": "idle: no active watches, skipped external provider calls", "checks": []})
                return
            for watch in watches_to_check:
                checks.append(process_watch(conn, watch, args, target))
            conn.execute(
                "INSERT INTO runs(run_type, started_at, completed_at, status, data_json) VALUES(?,?,?,?,?)",
                ("scheduled_check", started, now_iso(), "ok", json.dumps({"checks": checks}, ensure_ascii=False)),
            )
            conn.commit()
    append_log({"at": now_iso(), "event": "tour_alerts_run", "checks": checks})
    tour_watch.emit({"ok": True, "checks": checks})


def watchdog(args: argparse.Namespace) -> None:
    target = telegram_target()
    with tour_watch.connect() as conn:
        if not active_watches(conn):
            state = alert_state(conn, "__watchdog__")
            state["status"] = "idle"
            state["last_idle_at"] = now_iso()
            save_alert_state(conn, "__watchdog__", state)
            append_log({"at": now_iso(), "event": "tour_watchdog", "idle": True, "reason": "idle: no active watches, skipped watchdog stale alert"})
            tour_watch.emit({"ok": True, "idle": True, "stale": False, "event": None, "last_run": None})
            return
        row = conn.execute("SELECT completed_at FROM runs WHERE run_type='scheduled_check' ORDER BY completed_at DESC LIMIT 1").fetchone()
        last_run = row["completed_at"] if row else None
        stale = True if args.simulate_stale else not (parse_dt(last_run) and now_utc() - parse_dt(last_run) < timedelta(hours=STALE_AFTER_HOURS))
        state = alert_state(conn, "__watchdog__")
        event = None
        if stale and state.get("status") != "stale":
            result = send_telegram("🔴 Автоматический мониторинг туров перестал запускаться\n\nПоследний запуск:\n" + str(last_run or "-"), target, args.dry_run)
            if result.get("ok") and not args.dry_run:
                state["status"] = "stale"
                state["last_stale_alert_at"] = now_iso()
            event = {"type": "watchdog_stale", "sent": result.get("ok")}
        elif not stale and state.get("status") == "stale":
            result = send_telegram("✅ Автоматический мониторинг туров снова запускается\n\nПоследний запуск:\n" + str(last_run or "-"), target, args.dry_run)
            if result.get("ok") and not args.dry_run:
                state["status"] = "healthy"
                state["last_recovery_at"] = now_iso()
            event = {"type": "watchdog_recovery", "sent": result.get("ok")}
        else:
            state["status"] = "stale" if stale else "healthy"
        save_alert_state(conn, "__watchdog__", state)
    append_log({"at": now_iso(), "event": "tour_watchdog", "stale": stale, "action": event})
    tour_watch.emit({"ok": True, "stale": stale, "event": event, "last_run": last_run})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--watch")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--force-report", action="store_true")
    run_parser.add_argument("--exhaustive", action="store_true")
    run_parser.add_argument("--simulate-primary-failure", action="store_true")
    run_parser.add_argument("--simulate-provider-error")
    run_parser.set_defaults(func=run)
    watchdog_parser = sub.add_parser("watchdog")
    watchdog_parser.add_argument("--dry-run", action="store_true")
    watchdog_parser.add_argument("--simulate-stale", action="store_true")
    watchdog_parser.set_defaults(func=watchdog)
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
