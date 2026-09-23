# OpenClaw Media/Audio Example

Configure OpenClaw so incoming voice/audio attachments are passed to the wrapper:

```text
Telegram voice
-> OpenClaw attachment path
-> skills/voice-transcription/scripts/stt-wrapper.sh {{AttachmentPath}}
-> transcript text
-> agent message context
```

Use your OpenClaw version's supported media/audio configuration format. The important part is that the wrapper receives exactly one local audio file path and prints only transcript text to stdout.

Example placeholder:

```yaml
media:
  audio:
    stt:
      provider: cli
      command: "/path/to/openclaw/workspace/skills/voice-transcription/scripts/stt-wrapper.sh {{AttachmentPath}}"
```

This is an example, not a guaranteed config schema for every OpenClaw version. Check your OpenClaw docs.
