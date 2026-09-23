# Telegram Messages

## Вариант A. Один пост

Коллеги, привет!

Выложил первый публичный пакет reusable skills из моего персонального OpenClaw-агента Громика:

https://github.com/PavelGro-new/public-openclaw-skills

Идея простая: это не SaaS и не готовый продукт, а учебно-практический набор skills, которые можно забрать, изучить, адаптировать и запустить у себя через Codex, Claude Code или другого coding agent.

Сейчас внутри 3 skills:

1. `voice-transcription` - beta. Локальная транскрибация голосовых и аудио через ffmpeg + transcribe.cpp + локальную STT-модель. Подходит для голосового управления агентом. Длинные аудио зависят от железа и выбранной модели: 2 часа не обещаю, лучше тестировать постепенно: 5 минут, 15 минут, 30 минут.

2. `aviasales-price-watch` - stable. Мониторинг цен авиабилетов через Aviasales / Travelpayouts Data API. Нужен свой Travelpayouts token. Важно: это ценовой радар/кэш Aviasales, а не гарантия финальной цены и наличия мест.

3. `tour-price-watch` - beta. Мониторинг пакетных туров через provider-архитектуру. TEZ TOUR подключается проще, Travelata требует отдельный API-доступ через поддержку/партнёрский канал, Tourvisor не работает "из коробки" без корректного доступа. Финальные цены и наличие туров всегда нужно проверять у источника.

Безопасность: не коммитьте `.env`, токены, chat_id, state, logs, реальные watches и любые пользовательские данные. В репозитории лежат только примеры и placeholders.

Перед запуском лучше начать с README и quick-start:
https://github.com/PavelGro-new/public-openclaw-skills

## Вариант B. Три коротких сообщения

### Сообщение 1

Коллеги, привет!

Выложил публичный пакет reusable skills из моего OpenClaw-агента Громика:

https://github.com/PavelGro-new/public-openclaw-skills

Это учебно-практический набор, который можно адаптировать под своих агентов через Codex, Claude Code или другой coding agent. Это не polished SaaS, а рабочие заготовки с README, примерами конфигов и security notes.

### Сообщение 2

Внутри сейчас 3 skills:

`voice-transcription` - beta. Локальная транскрибация голосовых/аудио для управления агентом голосом. Длинные записи зависят от железа и модели: 2 часа не обещаю, тестировать лучше 5/15/30 минут.

`aviasales-price-watch` - stable. Мониторинг авиабилетов через Aviasales / Travelpayouts Data API. Нужен свой Travelpayouts token. Это ценовой радар, а не подтверждение финальной цены и наличия мест.

`tour-price-watch` - beta. Мониторинг пакетных туров через providers. TEZ проще, Travelata требует отдельный API-доступ через support/партнёрский канал, Tourvisor без правильного доступа не заработает из коробки.

### Сообщение 3

Важное по безопасности:

Не коммитьте `.env`, токены, chat_id, state, logs, реальные watches, аудио, transcripts и любые персональные данные.

В репозитории оставлены только placeholders и примеры. Перед запуском обязательно прочитайте README и quick-start:

https://github.com/PavelGro-new/public-openclaw-skills
