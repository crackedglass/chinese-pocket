# Spec Delta

## Purpose

Lets learners turn photographed Chinese vocabulary lists into validated words through a reviewable import flow, reducing manual entry while preventing uncertain OCR results from being stored silently.

## ADDED Requirements

### Requirement: Image vocabulary import is explicit
The system SHALL recognize an image as a vocabulary import only when it is sent through the supported import flow, and SHALL download it within configured size limits.

#### Scenario: Supported image received
- **GIVEN** a user or enabled channel sends a supported image
- **WHEN** the bot receives the image
- **THEN** it downloads the image within the size limit and starts extraction

#### Scenario: Image exceeds limit
- **GIVEN** an image exceeds the configured maximum size
- **WHEN** the bot receives it
- **THEN** it rejects the image with a retryable size error and stores no words

### Requirement: Extracted rows are validated and previewed
The system SHALL extract candidate rows containing Chinese text, pinyin, and translation, validate each row against the supported word format, and present candidates for confirmation before persistence.

#### Scenario: Valid candidates found
- **GIVEN** extraction produces valid vocabulary rows
- **WHEN** extraction completes
- **THEN** the bot presents the rows and requires explicit confirmation before saving

#### Scenario: Invalid or empty extraction
- **GIVEN** extraction produces no valid rows or malformed rows only
- **WHEN** extraction completes
- **THEN** the bot reports that no importable vocabulary was found and stores no rows

### Requirement: Import confirmation is scoped and idempotent
The system SHALL save only confirmed candidates to the selected dictionary and SHALL not create duplicate entries when the same import is confirmed more than once.

#### Scenario: Private user confirms import
- **GIVEN** a pending preview belongs to the current user and their personal dictionary
- **WHEN** the user confirms it before expiry
- **THEN** valid candidates are saved in that personal dictionary and a count is returned

#### Scenario: Channel administrator confirms import
- **GIVEN** a pending preview belongs to the dictionary linked to an enabled channel
- **WHEN** a verified channel administrator confirms it
- **THEN** valid candidates are saved in the linked dictionary and a count is returned

#### Scenario: Unauthorized confirmation
- **GIVEN** a pending preview belongs to a dictionary the actor cannot access
- **WHEN** the actor attempts confirmation
- **THEN** the confirmation is rejected and no vocabulary is changed

#### Scenario: Duplicate confirmation
- **GIVEN** a preview has already been confirmed
- **WHEN** the same confirmation is received again
- **THEN** no duplicate rows are created
