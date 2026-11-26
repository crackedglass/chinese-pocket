# Proposal

## Why

The current bot works in private chats, but it cannot be added to Telegram channels and cannot turn a photographed vocabulary list into saved words reliably. This limits group-based learning and makes importing existing materials too manual.

## What Changes

- Add a personal vocabulary dictionary for every Telegram user and support creating shared dictionaries.
- Allow owners to invite participants with a one-time code; members can add and review words while only owners manage membership.
- Link an enabled Telegram channel to a selected dictionary so channel imports and reviews use that dictionary.
- Process vocabulary images sent privately or to an enabled channel.
- Extract rows in `汉字 | pinyin | translation` format, show a confirmation preview, and save only confirmed rows to the selected dictionary.
- Preserve existing private-chat commands and existing vocabulary during migration.
- Reject unsupported or low-confidence image input with an actionable error instead of silently saving incorrect words.

## Capabilities

### New Capabilities

- `shared-vocabulary`: Personal and shared dictionaries, owner-managed memberships, invite codes, and dictionary-scoped words. Old bot-global vocabulary is discarded; Mini App personal words are migrated.
- `telegram-channel-integration`: Receive and process channel posts, authorize channel administration, and connect an enabled channel to a dictionary.
- `image-vocabulary-import`: Extract vocabulary rows from images, validate and preview them, and persist confirmed words in a dictionary.

### Modified Capabilities

- None. The repository has no existing OpenSpec capability specifications.

## Impact

- Telegram update polling and message routing in `__main__.py`.
- OCR pipeline in `speech.py`/new image-import code, including dependency and runtime configuration.
- SQLite schema and `Store` methods for channels, import sessions, and per-user/per-channel vocabulary ownership.
- Docker image and Compose environment for OCR dependencies and persistent data.
- Bot command/help text and Telegram permissions required for channel administration.
- Tests for channel updates, authorization, OCR parsing, preview confirmation, and failure handling.
