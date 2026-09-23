---
name: tour-price-watch
description: Beta package-tour price watch using provider-based adapters for Travelata, TEZ TOUR, and optional Tourvisor.
user-invocable: true
---

# Tour Price Watch

Use this skill when the user wants to create, check, update, pause, or report on package-tour price watches.

Status: beta.

## Provider Rules

- `travelata` uses official Travelata Partners API credentials.
- `tez` uses TEZ TOUR public JSON/XML search where reachable.
- `tourvisor` is optional/future and should be treated as `auth_required` until the user provides valid Search API access.
- Never claim that a result covers the whole market unless the configured providers actually support that claim.
- Always say which providers were used: `Travelata`, `TEZ TOUR`, `Tourvisor`, or a combination.
- If a provider fails, continue with available providers and explain the limitation.

## Runtime Files

Runtime data must live outside Git:

```text
/opt/openclaw-state/tour-price-watch/tour-price-watch.db
/opt/openclaw-state/tour-price-watch/cache/
```

Secrets must live outside Git:

```text
/opt/openclaw-secrets/tour-price-watch.env
```

Environment variables:

```text
TRAVELATA_LOGIN
TRAVELATA_PASSWORD
TOURVISOR_TOKEN
TOUR_WATCH_ENV_FILE
TOUR_WATCH_DB
TOUR_WATCH_CACHE_DIR
TOUR_WATCH_TELEGRAM_TARGET
```

## Natural Language Behavior

- If the user says "следи за турами", parse destination, dates, nights, adults/children, hotels, thresholds, and report schedule.
- If adults/children/child ages are unclear, ask one short clarification before enabling production monitoring.
- If the user says "проверь туры сейчас", run a manual check for the matching watch.
- If the user says "проверь через Travelata", run `check --provider travelata`.
- If the user says "сравни Travelata и TEZ", run separate provider checks if both are reachable.
- If the user asks for price discovery without thresholds, show current range and ask which normal and urgent thresholds to use.
- Do not create production watches with guessed occupancy.
- Keep demo or uncertain watches disabled by default.

## Idle Mode

- Active mode: at least one watch has `enabled=true`; scheduled checks may call configured providers.
- Idle mode: no watch has `enabled=true`; scheduled dispatcher must exit after local SQLite state check.
- Idle mode must not call TEZ, Travelata, Tourvisor, Telegram, or external provider health checks.
- Watchdog must treat idle as normal.

## Safety

- Do not print provider credentials.
- Do not store personal travel data in examples.
- Do not say "no tours exist" when a provider is temporarily unavailable.
- For TEZ HTTP/network failures, say that TEZ is temporarily unavailable.
- For Travelata auth failures, say credentials are missing or invalid.
- Remind the user that tour prices are not final availability guarantees.
