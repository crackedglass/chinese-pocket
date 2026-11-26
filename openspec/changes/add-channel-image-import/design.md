# Design

## Context

See `proposal.md` for motivation. The current bot polls Telegram with `getUpdates`, handles private `message` updates, downloads Telegram photos, and runs OCR through the system `tesseract` command. Vocabulary is stored in a shared SQLite file, but the existing `words` table is global and has no channel or import-session ownership. The Mini App has a separate user-scoped table.

## Goals / Non-Goals

**Goals:**

- Route channel posts without breaking private-chat commands.
- Verify channel enrollment and administrator authority before changing channel state or saving channel vocabulary.
- Reuse bounded Telegram photo downloads and the existing word parser where possible.
- Add a confirmation boundary between OCR output and persistence.
- Make imports retryable and duplicate-safe.

**Non-Goals:**

- Replacing long polling with webhooks.
- Full OCR accuracy improvement or machine translation.
- Merging the Mini App's `user_words` table with the bot's vocabulary in this change.
- Supporting arbitrary group permissions or anonymous admin identity beyond what Telegram exposes.

## Decisions

### Update routing
Accept `channel_post` in `allowed_updates` and dispatch it separately from private `message` updates. Channel posts have no normal sender identity, so configuration commands in channels must be authorized by resolving the channel administrator through Telegram's administrator API and matching the command actor when Telegram provides one. If actor identity is unavailable, channel configuration uses a private-admin setup flow rather than trusting a channel post.

### Channel persistence
Add channel-scoped records keyed by Telegram `chat_id`, with enabled state and review settings. Add a vocabulary ownership key to new channel rows rather than reusing the global private-chat table. Keep existing private-chat behavior unchanged and migrate no existing rows destructively.

### Image import state machine
Represent an import as `pending`, `confirmed`, or `rejected`, keyed by an opaque confirmation token and its owner scope. OCR creates a preview only; confirmation performs one transaction that marks the import confirmed and inserts rows with a uniqueness constraint. Expire pending imports after a short configurable period.

### OCR boundary
Keep image download limits in the Telegram client. Normalize OCR output into candidate lines, then run the existing strict `parse_words` validation. The bot reports invalid lines in the preview rather than silently coercing uncertain text. A later OCR provider can replace the extraction implementation without changing the confirmation contract.

### Channel output
Publish confirmed imports and scheduled reviews directly in the enabled channel. Do not reply to every channel image by default; process images only for an explicit import command/caption to avoid channel noise and accidental imports from ordinary images.

### Image format
The first version supports one vocabulary item per line in the format `汉字 | pinyin | translation`. Table recognition is deferred.

## Risks / Trade-offs

- **[Risk]** Telegram channel posts may not expose the administrator who initiated an action → **Mitigation:** require a private setup command or a signed pending setup initiated by an administrator.
- **[Risk]** OCR can produce plausible but incorrect pinyin or translation → **Mitigation:** mandatory preview and confirmation; no automatic persistence.
- **[Risk]** SQLite write contention between web and bot services → **Mitigation:** short transactions, WAL mode, and indexes; keep import work outside the write transaction.
- **[Risk]** Existing Docker image does not include OCR binaries or language data → **Mitigation:** make OCR dependencies explicit in the image and add a startup/health check before enabling image imports.
- **[Risk]** Channel data can leak through global review queries → **Mitigation:** require an explicit owner scope on every channel query and add cross-channel isolation tests.

## Migration Plan

1. Add additive SQLite tables, indexes, and nullable ownership fields; preserve existing private vocabulary.
2. Add routing and import tests before enabling channel configuration.
3. Build the bot image with the required OCR runtime and deploy as a rolling Compose update.
4. Enable a channel explicitly and test with a non-sensitive image.
5. Roll back by disabling channel enrollment and reverting the image; additive tables can remain unused without affecting existing private chats.
