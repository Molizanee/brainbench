## Purpose

Gives the engineer an always-available tutor in the sidebar that explains the concepts behind any topic using the engineer's own ingested material, profile, and current plan as context, so questions get answered in the terms of what they are actually studying.

## ADDED Requirements

### Requirement: Tutor is available from every page with the current context
The system SHALL show a tutor panel in the sidebar on every authenticated page and SHALL bind it to the page's context: the topic when a topic page is open, the plan when the plan page is open, otherwise the profile only; the panel SHALL display which context it is using.

#### Scenario: Topic context attached
- **WHEN** the engineer opens the tutor on a topic page
- **THEN** the panel shows the topic title as its context and answers draw on that topic's chunks first

#### Scenario: Context follows navigation
- **WHEN** the engineer navigates from topic A to topic B with the tutor open
- **THEN** the panel switches to topic B's thread and context without losing topic A's thread

### Requirement: Answers are grounded in the engineer's material
The system SHALL answer using retrieved chunks from the engineer's sources, SHALL cite the sources it used with their location references, and SHALL say explicitly when the material does not cover the question before answering from general knowledge.

#### Scenario: Grounded answer with citations
- **WHEN** the engineer asks "why does Raft need a majority for leader election" on a topic whose chunks cover it
- **THEN** the answer references at least one chunk with its source title and location, and the citation links to that location

#### Scenario: Material gap stated
- **WHEN** the engineer asks about a concept absent from all ingested sources
- **THEN** the answer begins by stating the material does not cover it, then explains from general knowledge without fabricated citations

### Requirement: Explanations follow the learner profile
The system SHALL adapt explanations to the profile: language for the reply, explanation depth (intuition-first or rigor-first), and current skill levels, assuming knowledge the profile marks as proficient or expert and defining terms otherwise.

#### Scenario: Depth preference honored
- **WHEN** the profile depth is "intuition-first" and the engineer asks for an explanation of consistent hashing
- **THEN** the reply starts with an intuitive explanation before any formal definition

#### Scenario: Known prerequisites not re-explained
- **WHEN** the profile lists "hash functions" at level "expert"
- **THEN** the reply does not define hash functions and may reference them directly

### Requirement: Conversations are threaded and persisted
The system SHALL keep one thread per context (per topic, one for the plan, one general), persist every message, allow the engineer to start a new thread for the same context, and show the thread history when the panel opens.

#### Scenario: Thread restored
- **WHEN** the engineer returns to a topic page after closing the browser
- **THEN** the tutor panel shows the previous messages for that topic

#### Scenario: New thread
- **WHEN** the engineer starts a new thread on a topic with an existing thread
- **THEN** the panel shows an empty conversation and the previous thread remains accessible from a thread list

### Requirement: Replies stream and can be stopped
The system SHALL stream the reply to the panel as it is produced and SHALL let the engineer stop generation, keeping the partial reply marked as stopped.

#### Scenario: Streaming reply
- **WHEN** the tutor begins answering
- **THEN** text appears incrementally in the panel before the full reply is complete

#### Scenario: Stop generation
- **WHEN** the engineer presses stop during a reply
- **THEN** generation halts within 5 seconds and the partial text is saved with a "stopped" marker

### Requirement: Tutor links to the plan
The system SHALL, when an answer relates to other topics in the current plan, list those topics as links, and SHALL offer to open the topic's generated video or figure when one exists.

#### Scenario: Related topic linked
- **WHEN** the answer discusses a concept that is a separate topic in the plan
- **THEN** the panel shows a link to that topic

### Requirement: Tutor uses the routed provider and respects limits
The system SHALL route tutor calls through the task class "tutoring" in provider settings, SHALL reject sending a message when no provider is connected, and SHALL show the provider's limit state with the reset time when the provider reports its usage limit is reached.

#### Scenario: No provider
- **WHEN** no provider is connected and the engineer sends a message
- **THEN** the panel shows an error linking to the providers page and the message is not sent

#### Scenario: Provider limit reached mid-conversation
- **WHEN** the provider reports its usage limit during a reply
- **THEN** the panel shows the reset time and the message can be resent after it
