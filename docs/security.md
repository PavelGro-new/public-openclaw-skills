# Security

Before publishing or sharing this package, verify that it contains no private runtime data.

## Never Commit

- `.env` or other real secrets files.
- API tokens.
- Travelata login/password.
- Telegram bot tokens.
- Telegram chat IDs.
- Runtime watches.
- Real travel routes.
- State databases.
- Alert state.
- Logs.
- Cache files.
- Raw API dumps.
- Audio files.
- Transcript cache.
- GGUF model files.
- Personal notes or private documents.

## How To Store API Tokens

Keep real tokens in a private file outside Git, for example:

```text
/opt/openclaw-secrets/<skill-name>.env
```

Use restrictive permissions:

```bash
chmod 700 /opt/openclaw-secrets
chmod 600 /opt/openclaw-secrets/<skill-name>.env
```

## Why `.env.example` Is Safe

`.env.example` contains only variable names and placeholders. It helps users understand what to configure without exposing real secrets.

## Why Runtime State Is Not Safe

Runtime state can contain routes, dates, price history, Telegram targets, transcripts, operational metadata, and personal context. Keep it outside the repository.

## GitHub Checklist

Before publishing:

- Run a secret scan.
- Confirm there is no `.env`.
- Confirm examples are disabled by default.
- Confirm DB files, logs, cache, audio, transcripts, and models are absent.
- Review README limitations and security notes.
