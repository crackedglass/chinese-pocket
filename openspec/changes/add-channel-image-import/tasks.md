# Tasks

## 1. Channel data model and authorization

- [ ] 1.1 Add channel enrollment/settings and channel-owned vocabulary tables with indexes; verify migrations preserve existing private word/chat rows and cover isolated channel reads.
- [ ] 1.2 Add Telegram update routing for channel posts and administrator verification; verify tests accept only enabled channels and reject non-admin configuration changes.
- [ ] 1.3 Add explicit channel enable/disable setup flow and command help; verify admin can enable/disable and disabled channels have no bot responses.
- [ ] 1.4 Add channel-scoped review publishing directly to the channel; verify scheduled review contains only the selected channel's vocabulary.

## 2. Image vocabulary import

- [ ] 2.1 Add bounded OCR extraction and one-row-per-line parser for `汉字 | pinyin | translation`; verify valid, malformed, empty, oversized, and OCR-failure fixtures.
- [ ] 2.2 Add pending import previews with confirm/cancel actions scoped to initiating user or channel; verify no candidate is persisted before confirmation.
- [ ] 2.3 Implement transactional, idempotent confirmation and expiry of pending imports; verify repeat confirmation creates no duplicates and unauthorized confirmation changes nothing.
- [ ] 2.4 Add Telegram image-import help text and usage examples; verify documented format matches parser behavior.

## 3. Packaging and integration verification

- [ ] 3.1 Add required OCR executable and Chinese language data to the bot image; verify image build and an OCR smoke test in the built container.
- [ ] 3.2 Add regression coverage for private `/start`, `/add`, `/list`, existing image import flow, plus channel posts; verify existing private-chat behavior remains unchanged.
- [ ] 3.3 Run the complete test suite and Compose validation; verify deployment health and end-to-end channel setup/import/review using a test channel.
