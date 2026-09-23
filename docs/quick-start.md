# Quick Start

1. Pick a skill:
   - `voice-transcription` for voice/audio transcription.
   - `aviasales-price-watch` for flight price monitoring.
   - `tour-price-watch` for package-tour monitoring.

2. Copy the skill folder into your OpenClaw workspace:

   ```text
   <openclaw-workspace>/skills/<skill-name>/
   ```

3. Read the skill's `README.md`.

4. Copy `.env.example` to a private env/secrets file outside Git.

5. Fill in only your own tokens, paths, and Telegram target.

6. Run a manual test before enabling cron.

7. Keep runtime state outside Git.

8. Before sharing your own fork, run a secret scan and check `git status`.

Never commit `.env`, tokens, chat IDs, real watches, audio files, transcripts, logs, cache, or DB files.
