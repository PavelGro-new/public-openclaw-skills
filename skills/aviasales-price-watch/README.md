# aviasales-price-watch

Status: stable. Tested in a personal OpenClaw/Gromik setup and packaged for reuse.

## What This Is

`aviasales-price-watch` is a reusable OpenClaw skill for monitoring cached flight prices through the Aviasales / Travelpayouts Data API.

It is designed for small personal automations: create a watch, check prices, receive Telegram alerts, and avoid unnecessary API calls when there is nothing active to monitor.

## What It Can Do

- Create flight price watches.
- Check prices manually or by schedule.
- Send Telegram alerts.
- Use normal and urgent price thresholds.
- Send scheduled reports.
- Avoid duplicate alerts with anti-spam state.
- Watch its own scheduler/health.
- Use idle-mode: if no watches are active, external APIs are not called.

## Requirements

- An OpenClaw agent/workspace.
- Python 3.
- A Travelpayouts / Aviasales API token.
- Telegram bot/target if you want alerts.
- `ffmpeg` is not required for this skill.

## API Token

You need a Travelpayouts token with access to the Aviasales Data API. Get it in your own Travelpayouts/Aviasales affiliate or account interface.

The exact account UI can change, so this package does not assume a fixed menu path.

## Install

1. Copy this folder into your OpenClaw workspace:

   ```text
   <openclaw-workspace>/skills/aviasales-price-watch/
   ```

2. Create state and secrets directories:

   ```bash
   sudo mkdir -p /opt/openclaw-state/aviasales-price-watch
   sudo mkdir -p /opt/openclaw-secrets
   sudo chmod 700 /opt/openclaw-state/aviasales-price-watch /opt/openclaw-secrets
   ```

3. Create a private env file from `.env.example`:

   ```bash
   sudo cp .env.example /opt/openclaw-secrets/aviasales-price-watch.env
   sudo chmod 600 /opt/openclaw-secrets/aviasales-price-watch.env
   ```

4. Edit the private env file and put your own token and Telegram target.

5. Copy `config/watches.example.json` to your state file:

   ```bash
   sudo cp config/watches.example.json /opt/openclaw-state/aviasales-price-watch/watches.json
   sudo chmod 600 /opt/openclaw-state/aviasales-price-watch/watches.json
   ```

## Example Env

```bash
TRAVELPAYOUTS_TOKEN=put_your_travelpayouts_token_here
AVIASALES_WATCHES_FILE=/opt/openclaw-state/aviasales-price-watch/watches.json
AVIASALES_ALERTS_STATE_FILE=/opt/openclaw-state/aviasales-price-watch/alerts_state.json
AVIASALES_TELEGRAM_TARGET=telegram_chat_id_or_channel_here
```

## Example Agent Commands

- "Следи за билетами Москва - Стамбул на май, скажи если будет дешевле 15000"
- "Покажи мои активные наблюдения за билетами"
- "Проверь цены сейчас"
- "Поставь срочный порог 12000"
- "Отключи это наблюдение"

## CLI Examples

```bash
python3 scripts/aviasales_watch.py list --format markdown
python3 scripts/aviasales_watch.py check --watch demo-mow-ist --format markdown
python3 scripts/aviasales_alerts.py run --dry-run
python3 scripts/aviasales_alerts.py watchdog --dry-run
```

## Cron Example

Cron is optional. Do not enable it until your `.env` and state file are ready.

See:

```text
../../examples/cron.example
```

## Limitations

- The Aviasales Data API can return cached price radar data, not guaranteed live seat availability.
- Always verify final price and ticket availability with the seller, airline, or booking page before purchase.
- API request frequency depends on your Travelpayouts limits.
- Coverage can differ by route, date, market, and airline.
- This package is educational and provided as-is. The author is not responsible for ticket purchases or price changes.

## Security

- Do not store API tokens in Git.
- Do not commit `.env`.
- Do not commit runtime state, watches, history, logs, or cache.
- Do not publish Telegram chat IDs.
- Publish `.env.example`, not your real env file.

## Files

- `SKILL.md`: OpenClaw skill instructions.
- `.env.example`: placeholders only.
- `config/watches.example.json`: disabled demo watch.
- `scripts/aviasales_watch.py`: CRUD and manual checks.
- `scripts/aviasales_alerts.py`: alerts, reports, health, watchdog.
- `scripts/run_aviasales_alerts.sh`: cron wrapper.
- `scripts/run_aviasales_watchdog.sh`: watchdog cron wrapper.
