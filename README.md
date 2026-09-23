# Public OpenClaw Skills from Gromik

Reusable OpenClaw skills extracted from a personal AI agent setup.

This package is meant for students learning OpenClaw, AI agents, automation, and vibe coding. The skills are intentionally small, practical, and documented with setup examples.

## Skills

- `voice-transcription` - beta. Local speech-to-text for voice/audio attachments.
- `aviasales-price-watch` - stable. Flight price watches through Aviasales / Travelpayouts Data API.
- `tour-price-watch` - beta. Package-tour monitoring through provider adapters such as Travelata and TEZ TOUR.

## What Is Inside

- Skill code.
- README for each skill.
- `.env.example` files with placeholders only.
- Safe config examples.
- Cron examples.
- Security checklist.

## What Is Not Included

- API tokens.
- Telegram chat IDs.
- Personal data.
- Real watches.
- Runtime state.
- Logs.
- Cache.
- GGUF models.
- Audio files.
- Transcripts.
- Production secrets.

## Quick Start

1. Choose one skill.
2. Read that skill's `README.md`.
3. Copy the skill folder into your OpenClaw workspace.
4. Copy `.env.example` to a private `.env` or private secrets file.
5. Fill in your own tokens and paths.
6. Run a manual test.
7. Enable cron only after the manual test works.

## Status

This is an educational open package. Use it carefully. Production responsibility is on the user: check API limits, protect secrets, and verify prices/transcripts before acting on them.
