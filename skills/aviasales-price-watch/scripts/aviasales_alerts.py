#!/usr/bin/env python3
"""Scheduled alerts, reports, health checks, and watchdog for Aviasales watches."""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import fcntl
import json
import os
from pathlib import Path
import subprocess
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import aviasales_watch


DEFAULT_ALERTS_STATE_FILE = Path("/opt/openclaw-state/aviasales-price-watch/alerts_state.json")
DEFAULT_LOCK_FILE = Path("/opt/openclaw-state/aviasales-price-watch/alerts.lock")
DEFAULT_LOG_FILE = Path(os.environ.get("AVIASALES_LOG_FILE") or "/opt/openclaw-logs/aviasales-price-watch.log")
PRICE_DROP_RUB = 2000
STALE_AFTER_HOURS = 6


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


def alerts_state_path() -> Path:
    configured = os.environ.get("AVIASALES_ALERTS_STATE_FILE")
    return Path(configured) if configured else DEFAULT_ALERTS_STATE_FILE


def lock_path() -> Path:
    configured = os.environ.get("AVIASALES_ALERTS_LOCK_FILE")
    return Path(configured) if configured else DEFAULT_LOCK_FILE


def load_state() -> dict[str, Any]:
    path = alerts_state_path()
    if not path.exists():
        return {"version": 1, "watches": {}, "monitor": {}, "watchdog": {}}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("version", 1)
    data.setdefault("watches", {})
    data.setdefault("monitor", {})
    data.setdefault("watchdog", {})
    return data


def save_state(state: dict[str, Any]) -> None:
    path = alerts_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(state, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    os.chmod(path, 0o600)


def append_log(event: dict[str, Any]) -> None:
    path = DEFAULT_LOG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True))
        fh.write("\n")


def load_runtime_env() -> dict[str, str]:
    return aviasales_watch.load_env(aviasales_watch.SECRET_FILE)


def telegram_target() -> str | None:
    env = load_runtime_env()
    return env.get("AVIASALES_TELEGRAM_TARGET") or os.environ.get("AVIASALES_TELEGRAM_TARGET")


def send_telegram(message: str, target: str, dry_run: bool) -> dict[str, Any]:
    if dry_run:
        return {"ok": True, "dry_run": True, "returncode": 0}
    command = [
        "openclaw",
        "message",
        "send",
        "--channel",
        "telegram",
        "--target",
        target,
        "--message",
        message,
        "--json",
    ]
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    return {"ok": result.returncode == 0, "returncode": result.returncode, "stderr": result.stderr.strip()[-500:]}


def compact(value: Any) -> str:
    if value is None or value == "":
        return "-"
    return str(value)


def alert_level(watch: dict[str, Any], total: int) -> str | None:
    alert = watch.get("alert_threshold_total")
    urgent = watch.get("urgent_threshold_total")
    if alert is None:
        return None
    if urgent is not None and total <= int(urgent):
        return "urgent"
    if total <= int(alert):
        return "alert"
    return None


def level_label(level: str) -> str:
    if level == "urgent":
        return "🔥 Срочно проверить"
    return "🟠 Хорошая цена"


def status_label(watch: dict[str, Any], total: int | None) -> str:
    if total is None:
        return "нет данных"
    level = alert_level(watch, total)
    if level == "urgent":
        return "срочная цена"
    if level == "alert":
        return "хорошая цена"
    return "выше обычного порога"


def combo_key(option: dict[str, Any]) -> str:
    return f"{option.get('depart_date')}|{option.get('return_date')}"


def choose_alert(
    watch: dict[str, Any],
    state: dict[str, Any],
    options: list[dict[str, Any]],
    previous_best_seen: int | None,
) -> dict[str, Any] | None:
    qualifying = []
    for option in options:
        total = int(option["calculated_total"])
        level = alert_level(watch, total)
        if level:
            qualifying.append((option, level))
    if not qualifying:
        return None

    alerted_combos = state.setdefault("alerted_combos", {})
    previous_level = state.get("last_alert_level")
    previous_alert_price = state.get("last_alert_price_total")
    previous_departure = state.get("last_alert_departure_date")
    previous_return = state.get("last_alert_return_date")

    for option, level in qualifying:
        total = int(option["calculated_total"])
        key = combo_key(option)
        seen_price = alerted_combos.get(key)
        reasons = []
        if state.get("last_alert_sent_at") is None:
            reasons.append("first_alert_zone")
        if level == "urgent" and previous_level != "urgent":
            reasons.append("first_urgent_zone")
        if seen_price is None:
            reasons.append("new_date_combination")
        if seen_price is not None and total <= int(seen_price) - PRICE_DROP_RUB:
            reasons.append("known_combination_price_drop")
        if previous_best_seen is None or total < int(previous_best_seen):
            reasons.append("new_historical_minimum")
        same_last_alert = (
            previous_alert_price == total
            and previous_departure == option.get("depart_date")
            and previous_return == option.get("return_date")
            and previous_level == level
        )
        if reasons and not (same_last_alert and reasons == ["new_historical_minimum"]):
            return {"option": option, "level": level, "reasons": reasons}
    return None


def update_success_state(watch_state: dict[str, Any], payload: dict[str, Any]) -> None:
    options = payload.get("best") or []
    best = options[0] if options else None
    current_total = int(best["calculated_total"]) if best else None
    previous_best_seen = watch_state.get("best_price_total_seen")
    best_seen = current_total if previous_best_seen is None else min(int(previous_best_seen), current_total or int(previous_best_seen))
    watch_state["last_check_at"] = now_iso()
    watch_state["current_best_price_total"] = current_total
    watch_state["best_price_total_seen"] = best_seen
    watch_state["consecutive_api_errors"] = 0
    watch_state["last_successful_check_at"] = now_iso()
    watch_state["health_status"] = "healthy"
    history = watch_state.setdefault("history", [])
    history.append(
        {
            "checked_at": now_iso(),
            "current_best_price_total": current_total,
            "best_price_total_seen": best_seen,
            "options_found": payload.get("options_found"),
            "errors": len(payload.get("errors") or []),
        }
    )
    del history[:-50]


def update_error_state(watch_state: dict[str, Any], message: str) -> str:
    count = int(watch_state.get("consecutive_api_errors") or 0) + 1
    watch_state["last_check_at"] = now_iso()
    watch_state["consecutive_api_errors"] = count
    watch_state["last_error_at"] = now_iso()
    watch_state["last_error_message"] = message
    if count >= 3:
        watch_state["health_status"] = "down"
    elif count >= 2:
        watch_state["health_status"] = "degraded"
    else:
        watch_state["health_status"] = "healthy"
    return watch_state["health_status"]


def mark_price_alert_sent(watch_state: dict[str, Any], alert: dict[str, Any]) -> None:
    option = alert["option"]
    total = int(option["calculated_total"])
    watch_state["last_alert_price_total"] = total
    watch_state["last_alert_departure_date"] = option.get("depart_date")
    watch_state["last_alert_return_date"] = option.get("return_date")
    watch_state["last_alert_level"] = alert["level"]
    watch_state["last_alert_sent_at"] = now_iso()
    combos = watch_state.setdefault("alerted_combos", {})
    key = combo_key(option)
    old_price = combos.get(key)
    combos[key] = total if old_price is None else min(int(old_price), total)


def mark_report_sent(watch_state: dict[str, Any], option: dict[str, Any] | None) -> None:
    watch_state["last_report_at"] = now_iso()
    if option:
        watch_state["last_report_price_total"] = int(option["calculated_total"])
        watch_state["last_report_departure_date"] = option.get("depart_date")
        watch_state["last_report_return_date"] = option.get("return_date")


def should_send_report(watch: dict[str, Any], watch_state: dict[str, Any], force_report: bool, now: datetime) -> bool:
    if force_report:
        return True
    if not watch.get("report_enabled", False):
        return False
    days = watch.get("report_days") or []
    report_time = watch.get("report_time")
    timezone_name = watch.get("report_timezone")
    if not days or not report_time or not timezone_name:
        return False
    try:
        tz = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        return False
    local_now = now.astimezone(tz)
    if local_now.strftime("%A") not in days:
        return False
    hour, minute = map(int, report_time.split(":"))
    scheduled = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if local_now < scheduled:
        return False
    last_report = parse_dt(watch_state.get("last_report_at"))
    if last_report and last_report.astimezone(tz).date() == local_now.date():
        return False
    return True


def format_price_alert(watch: dict[str, Any], alert: dict[str, Any], previous_best: int | None) -> str:
    option = alert["option"]
    total = int(option["calculated_total"])
    diff_line = "-"
    if previous_best is not None and total < previous_best:
        diff_line = f"-{previous_best - total} RUB"
    return "\n".join(
        [
            "✈️ GROMIK: билеты",
            "",
            f"[{level_label(alert['level'])}]",
            "",
            "Watch:",
            watch["name"],
            "",
            "Маршрут:",
            f"{watch['origin']} -> {watch['destination']}",
            "",
            "Даты:",
            f"туда: {option.get('depart_date')}",
            f"обратно: {option.get('return_date')}",
            f"длительность: {compact(option.get('trip_duration_days'))}",
            "",
            "Рейс:",
            f"{compact(option.get('airline'))} {compact(option.get('flight_number'))}",
            f"{compact(option.get('origin_airport'))} -> {compact(option.get('destination_airport'))}",
            f"{compact(option.get('departure_at'))} / {compact(option.get('return_at'))}",
            "",
            "Цена Aviasales:",
            f"{option['price_one_ticket']} {option['currency'].upper()} за 1 билет",
            "",
            "Расчётно:",
            f"{total} {option['currency'].upper()} за {option['passengers_for_calculation']} пассажиров",
            "",
            "Предыдущий минимум:",
            f"{previous_best} {option['currency'].upper()}" if previous_best is not None else "-",
            "",
            "Разница:",
            diff_line,
            "",
            "Важно:",
            "Это ориентир Aviasales Data API. Наличие мест и финальную цену нужно проверить при открытии предложения.",
            "",
            "Ссылка:",
            compact(option.get("link")),
        ]
    )


def format_report(watch: dict[str, Any], option: dict[str, Any] | None, watch_state: dict[str, Any]) -> str:
    previous = watch_state.get("last_report_price_total")
    current = int(option["calculated_total"]) if option else None
    if previous is None or current is None:
        change = "-"
    else:
        delta = current - int(previous)
        sign = "+" if delta > 0 else ""
        change = f"{sign}{delta} {option['currency'].upper()}"
    route_line = f"{watch['origin']} -> {watch['destination']}"
    if not option:
        best_lines = ["-"]
        currency = watch.get("currency", "rub").upper()
    else:
        currency = option["currency"].upper()
        best_lines = [
            f"{option.get('depart_date')} -> {option.get('return_date')}",
            f"{compact(option.get('airline'))} {compact(option.get('flight_number'))}",
            f"{option['price_one_ticket']} {currency} за 1 билет",
            f"{option['calculated_total']} {currency} за {option['passengers_for_calculation']} пассажиров",
        ]
    lines = [
        "✈️ GROMIK: плановый отчёт по билетам",
        "",
        "Watch:",
        watch["name"],
        "",
        "Маршрут:",
        route_line,
        "",
        "Лучший вариант сейчас:",
        *best_lines,
        "",
        "Предыдущий плановый отчёт:",
        f"{previous} {currency}" if previous is not None else "-",
        "",
        "Изменение:",
        change,
        "",
        "Текущий статус:",
        status_label(watch, current),
    ]
    if option and option.get("link"):
        lines.extend(["", "Ссылка:", option["link"]])
    lines.extend(
        [
            "",
            "Важно:",
            "Это ориентир Aviasales Data API. Наличие мест и финальную цену нужно проверить отдельно.",
        ]
    )
    return "\n".join(lines)


def format_health_alert(watch: dict[str, Any], status: str, watch_state: dict[str, Any]) -> str:
    title = "🔴 Мониторинг билетов не работает" if status == "down" else "⚠️ Мониторинг билетов работает нестабильно"
    return "\n".join(
        [
            title,
            "",
            "Watch:",
            watch["name"],
            "",
            "Маршрут:",
            f"{watch['origin']} -> {watch['destination']}",
            "",
            "Ошибок подряд:",
            str(watch_state.get("consecutive_api_errors")),
            "",
            "Последняя ошибка:",
            compact(watch_state.get("last_error_message")),
        ]
    )


def format_recovery_alert(watch: dict[str, Any], watch_state: dict[str, Any], option: dict[str, Any] | None) -> str:
    lines = [
        "✅ Мониторинг билетов восстановлен",
        "",
        "Watch:",
        watch["name"],
        "",
        "Последняя успешная проверка:",
        compact(watch_state.get("last_successful_check_at")),
    ]
    if option:
        lines.extend(
            [
                "",
                "Текущий лучший вариант:",
                f"{option.get('depart_date')} -> {option.get('return_date')}",
                f"{option['calculated_total']} {option['currency'].upper()} расчётно",
            ]
        )
    return "\n".join(lines)


def format_watchdog_stale(last_run_at: str | None) -> str:
    return "\n".join(
        [
            "🔴 Автоматический мониторинг билетов перестал запускаться",
            "",
            "Последний запуск основного монитора:",
            compact(last_run_at),
            "",
            f"Порог watchdog: {STALE_AFTER_HOURS} часов.",
        ]
    )


def format_watchdog_recovery(last_run_at: str | None) -> str:
    return "\n".join(
        [
            "✅ Автоматический мониторинг билетов снова запускается",
            "",
            "Последний запуск основного монитора:",
            compact(last_run_at),
        ]
    )


def active_watches(store: dict[str, Any], watch_filter: str | None = None) -> list[dict[str, Any]]:
    watches = []
    for watch in store["watches"]:
        if watch_filter and watch.get("id") != watch_filter:
            continue
        if watch.get("enabled", True):
            watches.append(watch)
    return watches


def handle_health(
    watch: dict[str, Any],
    watch_state: dict[str, Any],
    success: bool,
    error_message: str | None,
    option: dict[str, Any] | None,
    target: str | None,
    dry_run: bool,
) -> dict[str, Any] | None:
    previous_status = watch_state.get("health_status", "healthy")
    if success:
        if previous_status in {"degraded", "down"}:
            result = send_telegram(format_recovery_alert(watch, watch_state, option), str(target), dry_run)
            watch_state["last_health_recovery_sent_at"] = now_iso()
            watch_state["last_health_alert_level"] = None
            return {"type": "health_recovery", "sent": result["ok"], "dry_run": dry_run}
        watch_state["last_health_alert_level"] = None
        return None

    status = update_error_state(watch_state, error_message or "API check failed")
    level = "down" if status == "down" else "degraded" if status == "degraded" else None
    if level and watch_state.get("last_health_alert_level") != level:
        result = send_telegram(format_health_alert(watch, status, watch_state), str(target), dry_run)
        if result["ok"]:
            watch_state["last_health_alert_level"] = level
            watch_state["last_health_alert_sent_at"] = now_iso()
        return {"type": f"health_{level}", "sent": result["ok"], "dry_run": dry_run}
    return None


def run_monitor(dry_run: bool, watch_filter: str | None, force_report: bool, simulate_api_error: bool) -> dict[str, Any]:
    target = telegram_target()
    if not target and not dry_run:
        return {"ok": False, "error": "AVIASALES_TELEGRAM_TARGET is not configured"}

    state = load_state()
    store = aviasales_watch.load_store()
    watches_to_check = active_watches(store, watch_filter)
    checks = []
    state["monitor"]["last_run_at"] = now_iso()
    lock_file = lock_path()
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("w", encoding="utf-8") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if not watches_to_check:
            state["monitor"]["last_completed_at"] = now_iso()
            state["monitor"]["last_idle_at"] = now_iso()
            state["monitor"]["last_status"] = "idle"
            save_state(state)
            record = {
                "at": now_iso(),
                "event": "aviasales_alerts_run",
                "idle": True,
                "reason": "idle: no active watches, skipped external provider calls",
                "watch_filter": watch_filter,
            }
            append_log(record)
            return {"ok": True, "dry_run": dry_run, "idle": True, "reason": record["reason"], "checks": [], "alerts_state_file": str(alerts_state_path())}
        for watch in watches_to_check:
            watch_state = state.setdefault("watches", {}).setdefault(watch["id"], {})
            previous_best_seen = watch_state.get("best_price_total_seen")
            previous_health_status = watch_state.get("health_status", "healthy")
            previous_report_price = watch_state.get("last_report_price_total")
            record = {"watch_id": watch["id"], "watch_name": watch["name"], "checked": True, "events": []}

            if simulate_api_error:
                health_event = handle_health(
                    watch,
                    watch_state,
                    False,
                    "simulated API error",
                    None,
                    target,
                    dry_run,
                )
                if health_event:
                    record["events"].append(health_event)
                record["api_success"] = False
                record["health_status"] = watch_state.get("health_status")
                checks.append(record)
                append_log(record | {"at": now_iso()})
                continue

            payload = aviasales_watch.check_watch(store, watch, top=1000)
            options = payload.get("best") or []
            best = options[0] if options else None
            api_success = bool(payload.get("ok")) and len(payload.get("errors") or []) == 0
            if api_success:
                update_success_state(watch_state, payload)
            else:
                message = "; ".join(str(error.get("error")) for error in payload.get("errors", [])[:3]) or "API check failed"
                health_event = handle_health(watch, watch_state, False, message, best, target, dry_run)
                if health_event:
                    record["events"].append(health_event)

            if api_success and previous_health_status in {"degraded", "down"}:
                result = send_telegram(format_recovery_alert(watch, watch_state, best), str(target), dry_run)
                if result["ok"]:
                    watch_state["last_health_recovery_sent_at"] = now_iso()
                    watch_state["last_health_alert_level"] = None
                record["events"].append({"type": "health_recovery", "sent": result["ok"], "dry_run": dry_run})

            price_alert = (
                choose_alert(
                    watch,
                    watch_state,
                    options,
                    int(previous_best_seen) if previous_best_seen is not None else None,
                )
                if api_success
                else None
            )
            if price_alert:
                send_result = send_telegram(
                    format_price_alert(watch, price_alert, int(previous_best_seen) if previous_best_seen is not None else None),
                    str(target),
                    dry_run,
                )
                if send_result["ok"] and not dry_run:
                    mark_price_alert_sent(watch_state, price_alert)
                record["events"].append({"type": "price_alert", "level": price_alert["level"], "sent": send_result["ok"], "dry_run": dry_run})

            if api_success and should_send_report(watch, watch_state, force_report, now_utc()):
                send_result = send_telegram(format_report(watch, best, watch_state), str(target), dry_run)
                if send_result["ok"] and not dry_run:
                    mark_report_sent(watch_state, best)
                record["events"].append(
                    {
                        "type": "scheduled_report",
                        "sent": send_result["ok"],
                        "dry_run": dry_run,
                        "previous_report_price_total": previous_report_price,
                        "current_report_price_total": int(best["calculated_total"]) if best else None,
                    }
                )

            record["api_success"] = api_success
            record["options_found"] = payload.get("options_found")
            record["current_best_price_total"] = watch_state.get("current_best_price_total")
            record["health_status"] = watch_state.get("health_status")
            checks.append(record)
            append_log(record | {"at": now_iso()})

        state["monitor"]["last_completed_at"] = now_iso()
        state["monitor"]["last_successful_run_at"] = now_iso()
        save_state(state)
    return {"ok": True, "dry_run": dry_run, "checks": checks, "alerts_state_file": str(alerts_state_path())}


def run_watchdog(dry_run: bool, simulate_stale: bool) -> dict[str, Any]:
    target = telegram_target()
    if not target and not dry_run:
        return {"ok": False, "error": "AVIASALES_TELEGRAM_TARGET is not configured"}
    state = load_state()
    store = aviasales_watch.load_store()
    wd = state.setdefault("watchdog", {})
    wd["last_watchdog_run_at"] = now_iso()
    active_count = len(active_watches(store))
    if active_count == 0 and not simulate_stale:
        wd["status"] = "idle"
        wd["last_idle_at"] = now_iso()
        save_state(state)
        append_log({"at": now_iso(), "watchdog": True, "idle": True, "reason": "idle: no active watches, skipped watchdog stale alert"})
        return {"ok": True, "idle": True, "stale": False, "event": None, "active_watches": 0, "alerts_state_file": str(alerts_state_path())}
    monitor = state.setdefault("monitor", {})
    last_run_at = monitor.get("last_run_at")
    last_run = parse_dt(last_run_at)
    stale = True if simulate_stale else (last_run is None or now_utc() - last_run > timedelta(hours=STALE_AFTER_HOURS))
    event = None
    if stale and wd.get("status") != "stale":
        result = send_telegram(format_watchdog_stale(last_run_at), str(target), dry_run)
        if result["ok"]:
            wd["status"] = "stale"
            wd["last_stale_alert_sent_at"] = now_iso()
        event = {"type": "watchdog_stale", "sent": result["ok"], "dry_run": dry_run}
    elif not stale and wd.get("status") == "stale":
        result = send_telegram(format_watchdog_recovery(last_run_at), str(target), dry_run)
        if result["ok"]:
            wd["status"] = "healthy"
            wd["last_recovery_alert_sent_at"] = now_iso()
        event = {"type": "watchdog_recovery", "sent": result["ok"], "dry_run": dry_run}
    else:
        wd["status"] = "stale" if stale else "healthy"
    save_state(state)
    append_log({"at": now_iso(), "watchdog": True, "stale": stale, "event": event})
    return {"ok": True, "stale": stale, "event": event, "alerts_state_file": str(alerts_state_path())}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    run_parser = sub.add_parser("run")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--watch")
    run_parser.add_argument("--force-report", action="store_true")
    run_parser.add_argument("--simulate-api-error", action="store_true")
    watchdog_parser = sub.add_parser("watchdog")
    watchdog_parser.add_argument("--dry-run", action="store_true")
    watchdog_parser.add_argument("--simulate-stale", action="store_true")
    args = parser.parse_args()

    if args.command == "run":
        result = run_monitor(
            dry_run=args.dry_run,
            watch_filter=args.watch,
            force_report=args.force_report,
            simulate_api_error=args.simulate_api_error,
        )
    elif args.command == "watchdog":
        result = run_watchdog(dry_run=args.dry_run, simulate_stale=args.simulate_stale)
    else:
        result = {"ok": False, "error": f"Unsupported command: {args.command}"}
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
