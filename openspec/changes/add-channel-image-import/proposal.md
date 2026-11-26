# Proposal

## Why

The current bot works in private chats, but it cannot be added to Telegram channels and cannot turn a photographed vocabulary list into saved words reliably. This limits group-based learning and makes importing existing materials too manual.

## What Changes

- Add support for receiving messages from Telegram channels where the bot is an administrator.
- Allow channel administrators to configure a channel for vocabulary publishing and review notifications.
- Process vocabulary images sent to the bot or an enabled channel.
- Extract Chinese terms and their translations from images, show a confirmation preview, and save only confirmed rows.
- Preserve the existing private-chat commands and Mini App behavior.
- Reject unsupported or low-confidence image input with an actionable error instead of silently saving incorrect words.

## Capabilities

### New Capabilities

- `telegram-channel-integration`: Receive and process channel posts, authorize bot administration, and manage per-channel settings and notifications.
- `image-vocabulary-import`: Extract vocabulary rows from images, validate and preview them, and persist confirmed words.

### Modified Capabilities

- None. The repository has no existing OpenSpec capability specifications.

## Impact

- Telegram update polling and message routing in `__main__.py`.
- OCR pipeline in `speech.py`/new image-import code, including dependency and runtime configuration.
- SQLite schema and `Store` methods for channels, import sessions, and per-user/per-channel vocabulary ownership.
- Docker image and Compose environment for OCR dependencies and persistent data.
- Bot command/help text and Telegram permissions required for channel administration.
- Tests for channel updates, authorization, OCR parsing, preview confirmation, and failure handling.
