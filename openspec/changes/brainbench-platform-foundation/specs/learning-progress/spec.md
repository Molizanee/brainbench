## Purpose

Records what the engineer actually studied and how well they feel they mastered it, so the study plan adapts to real progress instead of its initial estimate.

## ADDED Requirements

### Requirement: Engineer can log a study session
The system SHALL allow an engineer to log a study session against a topic with a duration in minutes, an optional free-text note, and a mastery rating from {struggled, understood, confident, could-teach}.

#### Scenario: Session logged
- **WHEN** an engineer logs 45 minutes on a topic with rating "understood"
- **THEN** the session is stored and the topic's total studied minutes increases by 45

#### Scenario: Zero duration rejected
- **WHEN** an engineer logs a session with a duration of 0 minutes
- **THEN** the system rejects the request with a validation error

### Requirement: Topic status reflects sessions
The system SHALL derive each topic's status from {not-started, in-progress, mastered}: "mastered" when the latest rating is "confident" or "could-teach", "in-progress" when any session exists otherwise, "not-started" when none exists.

#### Scenario: Status becomes mastered
- **WHEN** the latest session on a topic has rating "could-teach"
- **THEN** the topic status is "mastered"

#### Scenario: Regression after mastery
- **WHEN** a topic is "mastered" and a new session is logged with rating "struggled"
- **THEN** the topic status returns to "in-progress"

### Requirement: Progress feeds back into curation
The system SHALL make session history available to curation runs, which SHALL deprioritize mastered topics, surface topics rated "struggled" earlier with a note recommending a different format, and update the profile's current skill level when all topics serving a target skill are mastered.

#### Scenario: Mastered topics moved down
- **WHEN** a curation re-run occurs after a topic is mastered
- **THEN** that topic is placed after all unmastered topics unless it is a prerequisite of an unmastered one

#### Scenario: Target skill promoted
- **WHEN** every topic serving target skill "distributed consensus" at level "proficient" is mastered
- **THEN** the profile's current skills gain "distributed consensus" at level "proficient" and the target is marked achieved

### Requirement: Engineer can view progress summary
The system SHALL show per target skill the count of topics by status, total studied minutes, and an estimate of remaining minutes based on the plan's estimates for unmastered topics.

#### Scenario: Summary per target
- **WHEN** an engineer opens the progress view
- **THEN** each target skill shows mastered, in-progress, and not-started counts, minutes studied, and minutes remaining

#### Scenario: Weekly pace against budget
- **WHEN** the engineer studied 180 minutes in the current week with a 300-minute budget
- **THEN** the summary shows 60% of the weekly budget used
