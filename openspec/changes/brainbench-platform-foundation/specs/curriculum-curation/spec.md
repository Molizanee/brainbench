## Purpose

Uses agents to turn ingested material into a prioritized, prerequisite-ordered study plan that matches the engineer's targets and discards content that does not advance them.

## ADDED Requirements

### Requirement: Engineer can request a curation run
The system SHALL allow an engineer to start a curation run over all of their sources with status "ready"; the run produces a new study plan version and never mutates a previous one.

#### Scenario: Run started
- **WHEN** an engineer with at least one ready source and at least one target skill requests curation
- **THEN** the system creates a curation job and returns its identifier

#### Scenario: Run rejected without targets
- **WHEN** an engineer with no target skills requests curation
- **THEN** the system rejects the request explaining that at least one target skill is required

#### Scenario: Run rejected without ready sources
- **WHEN** an engineer with no sources in status "ready" requests curation
- **THEN** the system rejects the request explaining that ready material is required

### Requirement: Curation extracts topics from material
The system SHALL derive a set of topics from the engineer's chunks where each topic has a title, a one-paragraph summary, and references to at least one supporting chunk.

#### Scenario: Topics cite evidence
- **WHEN** a curation run completes
- **THEN** every emitted topic references one or more chunks from which it was derived

#### Scenario: Near-duplicate topics merged
- **WHEN** two sources cover the same concept under different titles
- **THEN** the run emits a single topic that references chunks from both sources

### Requirement: Topics keep a stable identity across runs
The system SHALL reconcile each extracted candidate against existing topics by normalized title or by embedding similarity at or above the merge threshold, reuse the matched topic's identifier, and record a new topic revision only when its title or summary changed; unmatched candidates create new topics. Study sessions, generated assets, and tutor threads SHALL bind to the topic identifier and therefore survive re-curation.

#### Scenario: Re-curation preserves progress
- **WHEN** the engineer logs a session on topic T, adds a source, and runs curation again
- **THEN** the new plan contains T with the same identifier and its session history, assets, and tutor thread intact

#### Scenario: Changed summary creates a revision
- **WHEN** re-curation produces a different summary for an existing topic
- **THEN** a new revision is recorded, the new plan references the new revision, and earlier plans still show the earlier text

#### Scenario: Merged topic forwards its history
- **WHEN** the engineer merges topic B into topic A
- **THEN** B's sessions, assets, and tutor threads are attached to A, B is marked as merged into A, and links to B resolve to A

### Requirement: Topics are scored against the learner profile
The system SHALL assign every topic a relevance score in [0, 1] and a written rationale that states which target skill it serves and why, using the profile's current skills, target skills, priorities, and preferences as inputs.

#### Scenario: Topic linked to a target skill
- **WHEN** a topic is scored
- **THEN** the result names at least one target skill from the profile or marks the topic as unrelated

#### Scenario: Already-mastered content scored low
- **WHEN** a topic covers material the profile lists as a current skill at level "expert"
- **THEN** its relevance score is below 0.2 and the rationale states the skill is already held

### Requirement: Noise is filtered out of the plan
The system SHALL exclude from the study plan every topic with a relevance score below a per-run threshold (default 0.4) and SHALL keep excluded topics visible in an "excluded" list with their rationale so the engineer can override.

#### Scenario: Low-relevance topic excluded
- **WHEN** a topic scores 0.3 and the threshold is 0.4
- **THEN** the topic appears in the excluded list and not in the plan

#### Scenario: Engineer overrides exclusion
- **WHEN** an engineer includes an excluded topic
- **THEN** the topic is added to the plan at the position consistent with its prerequisites and marked "manually included"

### Requirement: Study plan is ordered by prerequisites and priority
The system SHALL produce a study plan where each topic lists its prerequisite topics, the order is a valid topological order of those prerequisites, ties are broken by target skill priority then relevance score, and every topic has an estimated study time in minutes.

#### Scenario: Prerequisite ordering holds
- **WHEN** topic B lists topic A as a prerequisite
- **THEN** A appears before B in the plan order

#### Scenario: Cycle detected
- **WHEN** the agent proposes prerequisite edges that form a cycle
- **THEN** the run breaks the cycle by dropping the lowest-confidence edge and records this in the run log

#### Scenario: Plan fits the time budget horizon
- **WHEN** a plan is produced for a profile with a weekly budget of 5 hours and no topic estimated above 300 minutes
- **THEN** the plan groups topics into weekly blocks whose total estimated minutes do not exceed 300 per block

#### Scenario: Oversized topic spans weeks
- **WHEN** a topic is estimated at 450 minutes and the weekly budget is 300 minutes
- **THEN** the topic is placed alone in a block that spans 2 consecutive weeks, is flagged "exceeds weekly budget", and no other topic shares that block

### Requirement: Engineer can review and edit the plan
The system SHALL allow an engineer to reorder topics, remove topics, merge two topics, and edit a topic's title or summary; edits create a new plan version with the previous one retained.

#### Scenario: Reorder respects prerequisites
- **WHEN** an engineer moves a topic before one of its prerequisites
- **THEN** the system rejects the move and names the violated prerequisite

#### Scenario: Edit creates new version
- **WHEN** an engineer removes a topic from plan version 2
- **THEN** plan version 3 exists without that topic and version 2 remains readable

#### Scenario: Title edit preserves old plan content
- **WHEN** an engineer edits a topic's title on plan version 3
- **THEN** a new topic revision carries the new title, version 4 references it, and versions 1 to 3 still display the previous title

### Requirement: Curation runs are reproducible and auditable
The system SHALL record for every run the profile version, the set of source identifiers, the threshold, the model identifiers used, and a log of agent steps, and SHALL expose them with the plan.

#### Scenario: Run metadata available
- **WHEN** an engineer views a study plan
- **THEN** the view includes the profile version and source set the plan was built from

#### Scenario: Stale plan flagged
- **WHEN** the profile version or the set of ready sources differs from the one recorded on the latest plan
- **THEN** the plan is flagged "stale" and a re-run is offered
