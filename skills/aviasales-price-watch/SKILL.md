---
name: aviasales-price-watch
description: Create, list, edit, pause, delete, and manually check reusable Aviasales/Travelpayouts cached flight price watches.
user-invocable: true
---

# Aviasales Price Watch

Reusable OpenClaw skill for monitoring cached flight prices through the Travelpayouts / Aviasales Data API.

Each watch is data, not code. Real runtime watches must be stored outside Git.

Suggested state files:

```text
/opt/openclaw-state/aviasales-price-watch/watches.json
/opt/openclaw-state/aviasales-price-watch/alerts_state.json
```

Suggested secrets file:

```text
/opt/openclaw-secrets/aviasales-price-watch.env
```

The CLI reads these environment variables when set:

```text
AVIASALES_ENV_FILE
AVIASALES_WATCHES_FILE
AVIASALES_ALERTS_STATE_FILE
AVIASALES_ALERTS_LOCK_FILE
AVIASALES_LOG_FILE
AVIASALES_TELEGRAM_TARGET
TRAVELPAYOUTS_TOKEN
```

## Active / Idle Mode

- Active mode: at least one watch has `enabled=true`; scheduled checks may call Travelpayouts / Aviasales.
- Idle mode: no watch has `enabled=true`; the dispatcher exits after a local state check and logs `idle: no active watches, skipped external provider calls`.
- Idle mode must not call Travelpayouts, send Telegram alerts, or run external provider health checks.
- The watchdog treats idle as normal and must not alert only because there are no active watches.

## Security Rules

- Keep real tokens only in `.env` or another private secrets file.
- The token variable name is `TRAVELPAYOUTS_TOKEN`.
- Never print, log, send, commit, or place the token in URLs.
- Use the `X-Access-Token` request header, not a `token=` query parameter.
- Treat Travelpayouts data as a cached price radar, not a guarantee that seats are available.
- Do not store real user watches in the skill directory or Git repository.
- Keep runtime state files outside Git with restrictive permissions.

## Commands

Set environment for local testing:

```bash
export AVIASALES_ENV_FILE=/opt/openclaw-secrets/aviasales-price-watch.env
export AVIASALES_WATCHES_FILE=/opt/openclaw-state/aviasales-price-watch/watches.json
export AVIASALES_ALERTS_STATE_FILE=/opt/openclaw-state/aviasales-price-watch/alerts_state.json
```

List watches:

```bash
python3 skills/aviasales-price-watch/scripts/aviasales_watch.py list --format markdown
```

Check one watch manually:

```bash
python3 skills/aviasales-price-watch/scripts/aviasales_watch.py check --watch demo-mow-ist --format markdown
```

Run alert check for all enabled watches:

```bash
python3 skills/aviasales-price-watch/scripts/aviasales_alerts.py run
```

Dry-run alert check without sending Telegram:

```bash
python3 skills/aviasales-price-watch/scripts/aviasales_alerts.py run --dry-run
```

Run watchdog check:

```bash
python3 skills/aviasales-price-watch/scripts/aviasales_alerts.py watchdog --dry-run
```

Create a new watch from parsed user intent:

```bash
python3 skills/aviasales-price-watch/scripts/aviasales_watch.py create \
  --name "Demo MOW to IST" \
  --origin MOW \
  --destination IST \
  --departure-from 2027-05-10 \
  --departure-to 2027-05-12 \
  --duration-min 5 \
  --duration-max 7 \
  --passengers 1 \
  --direct-only true \
  --currency rub \
  --alert-threshold-total 15000 \
  --urgent-threshold-total 12000
```

Pause, resume, delete, or update:

```bash
python3 skills/aviasales-price-watch/scripts/aviasales_watch.py pause --watch WATCH_ID
python3 skills/aviasales-price-watch/scripts/aviasales_watch.py resume --watch WATCH_ID
python3 skills/aviasales-price-watch/scripts/aviasales_watch.py delete --watch WATCH_ID
python3 skills/aviasales-price-watch/scripts/aviasales_watch.py update --watch WATCH_ID --alert-threshold-total 15000
```

## User-Facing Rules

- When the owner asks in natural language to create a watch, parse the route, dates, passenger count, direct-only preference, currency, and thresholds, then run `create`.
- If a city name is ambiguous or no IATA code is known, ask one short clarification before creating the watch.
- Do not edit Python code to add a watch.
- Show the best options sorted by one-ticket price.
- For each option, show only fields returned by the API.
- If a total price is calculated from one-ticket price and passenger count, mark it as a calculated estimate.
- Do not claim that all seats are actually available at the returned price.
- Use each watch's own thresholds:
  - `calculated xN <= urgent_threshold_total`: urgent alert
  - `calculated xN <= alert_threshold_total`: normal alert
  - otherwise: ordinary observation
- Planned reports are configured per watch with `report_enabled`, `report_days`, `report_time`, and `report_timezone`.
- Supported report days are `Monday`, `Tuesday`, `Wednesday`, `Thursday`, `Friday`, `Saturday`, `Sunday`.
- If the owner asks for weekly reports but does not name exact days and time, ask which days and what time are convenient.
