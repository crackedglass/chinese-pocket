# Spec Delta

## Purpose

Lets channel administrators use the Chinese vocabulary bot in Telegram channels to collect vocabulary from posts and publish configured learning reviews without exposing private-chat data.

## ADDED Requirements

### Requirement: Bot processes authorized channel posts
The system SHALL process supported channel posts only when the bot is an administrator of the channel and the channel is enabled for the bot.

#### Scenario: Enabled channel post is handled
- **GIVEN** the bot is an administrator and the channel is enabled
- **WHEN** a supported channel post arrives
- **THEN** the bot processes it using that channel's configuration

#### Scenario: Non-enabled channel post is ignored
- **GIVEN** the channel is not enabled for the bot
- **WHEN** a channel post arrives
- **THEN** the bot does not save vocabulary or publish a reply

### Requirement: Channel administrators control channel enrollment
The system SHALL allow a channel administrator to enable or disable the bot for that channel and SHALL reject configuration changes from non-administrators.

#### Scenario: Administrator enables channel
- **GIVEN** the sender is a channel administrator
- **WHEN** they enable the bot in the channel
- **THEN** the channel is recorded as enabled and the bot confirms the configuration

#### Scenario: Non-administrator attempts configuration
- **GIVEN** the sender is not a channel administrator
- **WHEN** they attempt to change channel settings
- **THEN** the bot rejects the request and leaves channel settings unchanged

### Requirement: Channel data is isolated
The system SHALL associate channel vocabulary and settings with the originating channel and SHALL not mix them with another channel's or private user's vocabulary.

#### Scenario: Channel vocabulary lookup
- **GIVEN** two enabled channels have different vocabulary
- **WHEN** a review is requested in one channel
- **THEN** only that channel's vocabulary is used

#### Scenario: Channel disabled
- **GIVEN** an enabled channel is disabled
- **WHEN** later channel posts arrive
- **THEN** those posts are ignored and no scheduled channel review is sent
