# tour-price-watch

Status: beta.

`tour-price-watch` is a reusable OpenClaw skill for monitoring package-tour prices through a provider-based architecture.

This skill is not a universal "whole market" guarantee. It checks the providers you configure and clearly reports which providers actually returned data.

## Providers

- `travelata`: uses the official Travelata Partners API. Requires `TRAVELATA_LOGIN` and `TRAVELATA_PASSWORD`.
- `tez`: uses TEZ TOUR public JSON/XML search where reachable. Availability can depend on IP/network/API changes.
- `tourvisor`: included as optional/future provider. It requires separate Tourvisor Search API authorization and should be treated as `auth_required` until configured.

Travelata API credentials are not always visible by default in a normal account dashboard. You may need to request access through Travelpayouts/Travelata support or a partner channel.

## What It Can Do

- Create package-tour watches.
- Check prices manually.
- Store price history in SQLite.
- Send Telegram alerts in scheduled mode.
- Use normal and urgent thresholds.
- Send planned reports.
- Use provider health and watchdog checks.
- Stay idle without external API calls when no watches are enabled.

## Requirements

- OpenClaw agent/workspace.
- Python 3.
- Optional Travelata API credentials.
- Optional Telegram target for alerts.
- Optional cron for scheduled checks.

## Example Env

```bash
TRAVELATA_LOGIN=put_your_travelata_login_here
TRAVELATA_PASSWORD=put_your_travelata_password_here
TOURVISOR_TOKEN=optional_future_token_here
TOUR_WATCH_DB=/opt/openclaw-state/tour-price-watch/tour-price-watch.db
TOUR_WATCH_CACHE_DIR=/opt/openclaw-state/tour-price-watch/cache
TOUR_WATCH_TELEGRAM_TARGET=telegram_chat_id_or_channel_here
```

## Install

1. Copy this folder into your OpenClaw workspace:

   ```text
   <openclaw-workspace>/skills/tour-price-watch/
   ```

2. Create runtime state outside Git:

   ```bash
   sudo mkdir -p /opt/openclaw-state/tour-price-watch /opt/openclaw-secrets
   sudo chmod 700 /opt/openclaw-state/tour-price-watch /opt/openclaw-secrets
   ```

3. Create a private env file from `.env.example` and fill your own credentials.

4. Start with manual checks. Enable cron only after a manual check works.

## CLI Examples

```bash
python3 scripts/tour_watch.py init-db
python3 scripts/tour_watch.py providers
python3 scripts/tour_watch.py create --id demo-antalya --name "Demo Antalya" --departure-city Moscow --country Turkey --resort Antalya --departure-from 2027-05-10 --departure-to 2027-05-12 --nights-min 6 --nights-max 8 --adults 2 --currency RUB --primary-provider travelata --cross-check-providers tez --disabled
python3 scripts/tour_watch.py list --format markdown
python3 scripts/tour_watch.py check --watch demo-antalya --provider travelata --top 10
python3 scripts/tour_alerts.py run --dry-run
```

## Important Limitations

- Package-tour APIs can return different prices and coverage.
- A returned price is not a final guarantee of availability.
- Always verify final price and availability with the tour operator or agency before purchase.
- Travelata requires separate credentials.
- TEZ availability may depend on your server IP/network and API changes.
- Tourvisor requires separate Search API authorization and is not expected to work out of the box.
- Long or frequent monitoring can hit provider limits.
- Production monitoring should be enabled carefully and only after manual tests.

## Security

- Do not commit real `.env`.
- Do not publish Travelata login/password.
- Do not publish Tourvisor tokens.
- Do not publish Telegram chat IDs.
- Do not commit SQLite DB, provider cache, logs, or real watches.

## Files

- `SKILL.md`: OpenClaw agent instructions.
- `.env.example`: placeholders only.
- `config/watch.example.json`: disabled demo watch.
- `providers/`: provider adapters.
- `scripts/tour_watch.py`: CRUD and manual checks.
- `scripts/tour_alerts.py`: alerts, reports, health, watchdog.
