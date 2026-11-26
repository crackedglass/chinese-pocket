# Spec Delta

## Purpose

Lets users keep a personal Chinese vocabulary and create shared dictionaries where invited participants can contribute words while owners control membership and connected channels.

## ADDED Requirements

### Requirement: Every user has a personal dictionary
The system SHALL provide each authenticated user with a personal dictionary whose words are visible only to that user.

#### Scenario: First user access
- **WHEN** a user opens their vocabulary for the first time
- **THEN** the system provides an empty personal dictionary without exposing another user's words

#### Scenario: Personal word added
- **GIVEN** a user is viewing their personal dictionary
- **WHEN** they add a valid word
- **THEN** the word is stored in that personal dictionary and is not visible to other users

### Requirement: Owners can create shared dictionaries and invite participants
The system SHALL allow a dictionary owner to create a shared dictionary and issue an invitation code that another user can redeem to join.

#### Scenario: Owner creates invite
- **GIVEN** a user owns a shared dictionary
- **WHEN** they request an invitation
- **THEN** the system returns a redeemable invite code associated only with that dictionary

#### Scenario: User joins by invitation
- **GIVEN** a valid unused invite code exists
- **WHEN** an authenticated user redeems it
- **THEN** that user becomes a member of the associated dictionary and the invite cannot be reused

#### Scenario: Invalid invite
- **GIVEN** an invite code is invalid, expired, or already redeemed
- **WHEN** a user attempts to redeem it
- **THEN** the system rejects the request and membership remains unchanged

### Requirement: Dictionary members can contribute words while owners manage membership
The system SHALL allow dictionary members to add and review vocabulary and SHALL reserve membership management actions for the owner.

#### Scenario: Member adds word
- **GIVEN** a user is a member of a shared dictionary
- **WHEN** they add a valid word
- **THEN** it is stored in that dictionary and visible to its members

#### Scenario: Non-member accesses shared dictionary
- **GIVEN** a user is not a member of a shared dictionary
- **WHEN** they attempt to list, review, or modify its words
- **THEN** the system denies access without changing dictionary data

#### Scenario: Owner removes participant
- **GIVEN** a user owns a shared dictionary
- **WHEN** they remove a participant
- **THEN** that participant loses access while the dictionary and its words remain intact

### Requirement: Existing vocabulary is migrated without data loss
The system SHALL preserve authenticated Mini App words when creating dictionary records. The previous bot-global vocabulary is intentionally discarded during the dictionary migration.

#### Scenario: Existing Mini App words migrated and global bot words discarded
- **GIVEN** the database contains words from the previous bot and Mini App schemas
- **WHEN** the dictionary migration runs
- **THEN** each user's Mini App words remain accessible in their personal dictionary and previous bot-global words are removed from the active database
