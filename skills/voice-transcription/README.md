# voice-transcription

Status: beta.

`voice-transcription` is a reusable OpenClaw skill for local speech-to-text transcription of voice/audio messages.

It is useful when you want your agent to understand Telegram voice messages, turn spoken notes into tasks, summarize audio, or convert a spoken request into a prompt for another tool such as Codex.

## What It Uses

- `ffmpeg` / `ffprobe` for audio conversion and duration checks.
- `transcribe.cpp` as the local STT runtime.
- A local GigaAM v3 E2E-CTC GGUF model, for example a Q8_0 build.

The model file is not included in this repository. Download it separately and check its license before use.

## What You Can Send

- Telegram voice messages.
- Audio files supported by `ffmpeg`.
- Short and medium voice notes are the safest starting point.

Long recordings depend heavily on CPU, RAM, model, and chunk size. Do not assume that 2-hour audio will work well. Test gradually: 5 minutes, then 15 minutes, then 30 minutes.

## Accuracy Notes

Quality depends on noise, language, microphone, speaking style, and model. Always verify important names, dates, amounts, and legal/financial details manually.

## Example Env

```bash
STT_MODEL_PATH=/path/to/models/gigaam-v3-e2e-ctc-Q8_0.gguf
STT_TRANSCRIBE_CLI=/path/to/transcribe.cpp/main
STT_CACHE_DIR=/opt/openclaw-state/voice-transcription/cache
STT_LOG_DIR=/opt/openclaw-state/voice-transcription/logs
STT_CHUNK_SECONDS=600
```

If your model/runtime has a lower input limit, reduce `STT_CHUNK_SECONDS`.

## Install

1. Install `ffmpeg`.
2. Build or install `transcribe.cpp`.
3. Download a compatible GGUF speech model.
4. Copy this skill into your OpenClaw workspace.
5. Create a private env file from `.env.example`.
6. Configure OpenClaw media/audio to call `scripts/stt-wrapper.sh` with the incoming audio attachment path.

## Wrapper Behavior

`scripts/stt-wrapper.sh`:

- accepts one audio file path;
- converts it to 16 kHz mono WAV;
- chunks long audio;
- runs local STT sequentially;
- prints only the final transcript to stdout;
- writes technical logs to a log directory;
- caches transcript text by audio hash.

## Limitations

- This is local CPU/GPU work, not a free cloud service.
- Long audio can be slow.
- Chunking can lose a bit of context between chunks.
- This skill does not include diarization.
- This skill does not store audio intentionally, but your OpenClaw/media pipeline may keep inbound attachments depending on your setup.
