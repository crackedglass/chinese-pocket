# Tasks

## 1. Dictionary data model and migration

- [x] 1.1 Add dictionaries, members, one-time invite codes, dictionary words, and channel-to-dictionary tables with indexes; verify existing Mini App rows are preserved and old bot-global words are discarded.
- [x] 1.2 Add authenticated dictionary APIs for listing, creating, selecting, and reading dictionaries; verify non-members cannot access shared words.
- [x] 1.3 Add owner-issued invite redemption and owner-only member removal; verify codes are single-use and non-owners cannot manage membership.

## 2. Telegram channel integration

- [x] 2.1 Route channel posts and bot membership updates; verify only enabled channels linked to a dictionary process content.
- [x] 2.2 Add authenticated channel link/unlink setup requiring channel administrator and dictionary-owner checks; verify unauthorized link attempts do not change state.
- [x] 2.3 Add channel-scoped import and review publishing from the linked dictionary; verify channel content does not mix words from another dictionary.

## 3. Image vocabulary import

- [x] 3.1 Add bounded OCR extraction and one-row-per-line parser for `汉字 | pinyin | translation`; verify valid, malformed, empty, oversized, and OCR-failure fixtures.
- [x] 3.2 Add pending previews and confirm/cancel flow defaulting private imports to the user's personal dictionary and channel imports to the linked dictionary; verify nothing is saved before confirmation.
- [x] 3.3 Implement transactional idempotent confirmation, membership/administrator authorization, and 15-minute expiry; verify repeat, unauthorized, and expired confirmations change no data.
- [x] 3.4 Add Telegram help text and image-import examples; verify documented commands and format match the implementation.

## 4. Packaging and integration verification

- [x] 4.1 Add Tesseract, Chinese/English/Russian language data, and Noto CJK fonts to the image; verify the built container OCRs a synthetic Chinese vocabulary fixture.
- [x] 4.2 Add regression tests for existing private commands, migrations, dictionary sharing/permissions, channel routing, and image imports; verify legacy behavior is retained.
- [ ] 4.3 Run full suite, Compose validation, and deployment health; verify secure pre-migration SQLite backup and migration state, then exercise test-channel invite, link, image import, and review flows.
