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

- **Non-Goals:**

- Replacing long polling with webhooks.
- Full OCR accuracy improvement or machine translation.
- Supporting arbitrary group permissions or anonymous admin identity beyond what Telegram exposes.

## Decisions

### Update routing
Accept `channel_post` in `allowed_updates` and dispatch it separately from private `message` updates. Channel posts have no normal sender identity, so configuration commands in channels must be authorized by resolving the channel administrator through Telegram's administrator API and matching the command actor when Telegram provides one. If actor identity is unavailable, channel configuration uses a private-admin setup flow rather than trusting a channel post.

### Dictionary ownership and legacy migration
The legacy bot-wide vocabulary will be discarded rather than migrated. The Mini App's existing user words will be migrated into each user's personal dictionary, and existing channel links will be disabled until an owner links them to a dictionary. Keep the old bot vocabulary table empty and prevent old `/add` and `/list` code paths from recreating it.

### Channel persistence
Store channel enrollment separately from its target dictionary. A channel is enabled only after a channel administrator confirms the link and the acting user owns the target dictionary. The linked dictionary ID is the only vocabulary scope used for channel imports and channel-published reviews.

### Image import state machine
Represent an import as `pending`, `confirmed`, or `rejected`, keyed by an opaque confirmation token and the dictionary scope. Private-chat imports default to the sender's personal dictionary; channel imports use the channel's linked dictionary. OCR creates a preview only; confirmation verifies the actor is a dictionary member (and, for channel imports, a channel admin) and performs one transaction that marks the import confirmed and inserts rows with a uniqueness constraint. Expire pending imports after 15 minutes.

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
- **[Risk]** Migration deletes old bot-global words → **Mitigation:** run once under the explicit approved destructive migration, retain Mini App personal words, and verify preserved/deleted row counts.

## Migration Plan

1. Add additive SQLite tables, indexes, and nullable ownership fields; preserve existing private vocabulary.
2. Add routing and import tests before enabling channel configuration.
3. Rebuild the bot image with the OCR runtime and deploy after tests pass; the database migration deletes global bot words once and should be backed up before production rollout.
4. Enable a channel explicitly and test with a non-sensitive image.
5. Roll back by disabling channel enrollment and reverting the image; additive tables can remain unused without affecting existing private chats.
