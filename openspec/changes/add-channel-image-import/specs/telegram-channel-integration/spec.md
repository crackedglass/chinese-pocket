# Spec Delta

## Purpose

Lets channel administrators use the Chinese vocabulary bot in Telegram channels to collect vocabulary from posts and publish configured learning reviews without exposing private-chat data.

## ADDED Requirements

### Requirement: Channel administrators link an enabled channel to a dictionary
The system SHALL allow an authenticated administrator of a Telegram channel to link that channel to a dictionary they own and SHALL reject linking actions from non-administrators or non-owners.

#### Scenario: Channel administrator enables linked dictionary
- **GIVEN** the bot is an administrator in the channel and the acting user owns the selected dictionary
- **WHEN** they enable the channel and select that dictionary
- **THEN** the channel is linked to that dictionary and the bot confirms the configuration

#### Scenario: Non-administrator or non-owner attempts linking
- **GIVEN** the user is not a channel administrator or does not own the selected dictionary
- **WHEN** they attempt to link the channel
- **THEN** the system rejects the operation and leaves channel settings unchanged

### Requirement: Channel posts use the linked dictionary
The system SHALL process vocabulary imports and reviews in an enabled channel using only the dictionary linked to that channel.

#### Scenario: Enabled channel post is handled
- **GIVEN** the channel is enabled and linked to a dictionary
- **WHEN** a supported channel post arrives
- **THEN** the bot processes it using the linked dictionary

#### Scenario: Non-enabled or unlinked channel post is ignored
- **GIVEN** the channel is not enabled or has no linked dictionary
- **WHEN** a channel post arrives
- **THEN** the bot does not save vocabulary or publish a review

### Requirement: Channel access can be revoked
The system SHALL disable a channel link when an authorized administrator disables it or when the bot loses administrator status.

#### Scenario: Channel disabled or bot removed
- **GIVEN** an enabled channel link exists
- **WHEN** an administrator disables it or the bot loses channel administrator status
- **THEN** later posts and scheduled reviews are ignored for that channel
