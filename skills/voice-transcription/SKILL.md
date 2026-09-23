---
name: voice-transcription
description: Beta local STT skill for transcribing voice/audio attachments through a reusable wrapper.
user-invocable: true
---

# Voice Transcription

Use this skill when the user sends or references a voice/audio attachment and wants it transcribed, summarized, turned into tasks, or interpreted as an instruction.

Status: beta.

## How To Use

1. Receive the audio attachment path from OpenClaw media/audio.
2. Call `scripts/stt-wrapper.sh ATTACHMENT_PATH`.
3. Treat stdout as the transcript.
4. Technical logs must go to stderr or the configured log directory, not into the transcript.

## Agent Behavior

- If the user asks for transcription, return the transcript.
- If the user asks for a summary, first transcribe, then summarize.
- If the user asks to create tasks, first transcribe, then extract tasks.
- If the user speaks a command, act on the meaning, not just the raw transcript.
- If transcription fails, explain that STT failed and ask for a shorter/clearer audio or text fallback.
- Do not invent missing words.
- Mark uncertain words if they matter.

## Privacy

- Do not save personal audio/transcripts unless the user explicitly asks.
- Do not commit audio, transcripts, logs, or cache.
- Do not include model files in Git.

## Limits

- Long audio depends on machine resources.
- Start with short and medium voice messages.
- Test long recordings gradually.
