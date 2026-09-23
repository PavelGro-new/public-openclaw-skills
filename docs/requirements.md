# Requirements

## voice-transcription

- Linux, macOS, or WSL-style environment.
- `bash`.
- `ffmpeg` and `ffprobe`.
- `transcribe.cpp`.
- Local GGUF speech model such as GigaAM v3 E2E-CTC.
- CPU/RAM depends on model and audio length.

Long audio should be tested gradually: 5 minutes, 15 minutes, 30 minutes. Do not assume multi-hour audio will work without tuning.

## aviasales-price-watch

- Python 3.
- Travelpayouts / Aviasales Data API token.
- Telegram target is optional but needed for alerts.
- Cron is optional.

## tour-price-watch

- Python 3.
- Travelata credentials are optional overall, but required for the Travelata provider.
- TEZ provider may work without credentials, but availability can depend on IP/network/API changes.
- Tourvisor is optional/future and requires separate authorization.
- Telegram target is optional but needed for alerts.
- Cron is optional.
