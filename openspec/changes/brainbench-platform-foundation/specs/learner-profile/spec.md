## Purpose

Captures an engineer's current skills, target skills with mastery goals, time budget, and preferences so every agent decision in the platform is grounded in that engineer's actual starting point and objectives.

## ADDED Requirements

### Requirement: Engineer can create a learner profile
The system SHALL allow the engineer to create exactly one learner profile per instance containing a display name, a free-text background summary, and a weekly study time budget in hours.

#### Scenario: Profile created with required fields
- **WHEN** an engineer submits a profile with a display name, background summary, and a weekly time budget greater than zero
- **THEN** the system stores the profile and returns it with a unique identifier

#### Scenario: Second profile rejected
- **WHEN** a profile already exists and another one is submitted
- **THEN** the system rejects the request with a conflict error identifying the existing profile

#### Scenario: Invalid time budget rejected
- **WHEN** an engineer submits a profile with a weekly time budget of zero or negative hours
- **THEN** the system rejects the request with a validation error naming the field

### Requirement: Engineer can declare current skills with levels
The system SHALL allow an engineer to list current skills, each with a name and a self-assessed level from the set {novice, working, proficient, expert}.

#### Scenario: Current skill added
- **WHEN** an engineer adds a current skill "linear algebra" with level "working"
- **THEN** the profile lists that skill with that level

#### Scenario: Duplicate skill name rejected
- **WHEN** an engineer adds a current skill whose name already exists in the profile, ignoring case and surrounding whitespace
- **THEN** the system rejects the request and leaves the existing entry unchanged

### Requirement: Engineer can declare target skills with mastery goals
The system SHALL allow an engineer to list target skills, each with a name, a target level from {working, proficient, expert}, a priority from {high, medium, low}, and an optional free-text reason.

#### Scenario: Target skill added
- **WHEN** an engineer adds a target skill "distributed consensus" with target level "proficient" and priority "high"
- **THEN** the profile lists that target skill with those attributes

#### Scenario: Target below current level rejected
- **WHEN** an engineer adds a target skill whose name matches a current skill and whose target level is not higher than the current level
- **THEN** the system rejects the request explaining that the target must exceed the current level

### Requirement: Engineer can set learning preferences
The system SHALL allow an engineer to set preferences: preferred content formats from {video, figure, text}, preferred explanation depth from {intuition-first, rigor-first}, and a language for generated narration and captions.

#### Scenario: Preferences saved with defaults
- **WHEN** an engineer creates a profile without specifying preferences
- **THEN** the profile has all formats enabled, depth "intuition-first", and language "en"

#### Scenario: Preferences updated
- **WHEN** an engineer updates preferences to formats {video} and depth "rigor-first"
- **THEN** subsequent reads of the profile reflect the new values

### Requirement: Profile changes are versioned
The system SHALL record a monotonically increasing version number on the profile that increments on every change to skills, targets, time budget, or preferences, so downstream consumers can detect staleness.

#### Scenario: Version increments on update
- **WHEN** an engineer adds a target skill to a profile at version 3
- **THEN** the profile is at version 4

#### Scenario: Read returns current version
- **WHEN** a downstream consumer reads the profile
- **THEN** the response includes the current version number
