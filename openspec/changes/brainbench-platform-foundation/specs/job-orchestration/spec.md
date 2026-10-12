## Purpose

Runs every long-lived agent and render step as a durable background job with observable state so the engineer can see what the platform is doing, why something failed, and stop it.

## ADDED Requirements

### Requirement: Long-running work executes as durable jobs
The system SHALL execute ingestion, curation, and visual generation as jobs that survive process restarts and have a status from {queued, running, ready, failed, cancelled, skipped}.

#### Scenario: Job survives worker restart
- **WHEN** a worker process terminates while a job is "running"
- **THEN** the job returns to "queued" within 2 minutes and is picked up again by a worker

#### Scenario: Job status readable
- **WHEN** an engineer requests a job by identifier
- **THEN** the response includes its type, status, creation time, start time, finish time, attempt count, and progress message

### Requirement: Failed jobs retry with backoff
The system SHALL retry a failed job up to 3 attempts with exponential backoff starting at 30 seconds, except for failures classified as permanent (validation error, budget exhausted, cancelled), which SHALL not retry.

#### Scenario: Transient failure retried
- **WHEN** a job fails because a network timeout interrupted a provider call
- **THEN** the job is re-queued with a later run-after time and attempt count incremented

#### Scenario: Permanent failure not retried
- **WHEN** a job fails because the engineer's budget is exhausted
- **THEN** the job is "failed" immediately with reason "budget-exhausted" and no further attempts

### Requirement: Provider limit deferrals do not consume attempts
The system SHALL treat a provider usage-limit response as a deferral, not a failure: the job returns to "queued" with run-after no earlier than the reported reset time and no later than 60 seconds after it (or between 5 and 6 minutes from now when no reset time is reported), its attempt count is unchanged, its progress message names the wait, and work completed before the limit is kept where the job type supports checkpoints.

#### Scenario: Deferred job keeps its attempts
- **WHEN** a running job on attempt 1 receives a provider usage-limit response with a reset time
- **THEN** the job is "queued" with run-after between the reset time and 60 seconds after it, attempt count still 1, and message "waiting for provider limit reset"

#### Scenario: Repeated deferrals do not fail the job
- **WHEN** the same job is deferred by provider limits three times
- **THEN** it is still "queued" and eligible to run after the latest reset time

### Requirement: Engineer can cancel a job
The system SHALL allow an engineer to cancel any of their queued or running jobs; running sandboxes are terminated and partial outputs discarded.

#### Scenario: Cancel queued job
- **WHEN** an engineer cancels a job with status "queued"
- **THEN** the job status becomes "cancelled" and it is never started

#### Scenario: Cancel running render
- **WHEN** an engineer cancels a running render job
- **THEN** the sandbox is stopped within 30 seconds, no asset is attached, and the job status is "cancelled"

### Requirement: Jobs are concurrency-limited
The system SHALL run at most a configurable number of concurrent jobs per instance (default 2) and per job type (default 1 render), and SHALL prefer jobs in creation order within the same priority.

#### Scenario: Instance limit enforced
- **WHEN** 2 jobs are running and a third is queued
- **THEN** the third remains "queued" until one of the two finishes

#### Scenario: Render limit enforced
- **WHEN** a render job is running and a second render job becomes due
- **THEN** the second render stays "queued" while a non-render job can still start

### Requirement: Job events are streamed to the UI
The system SHALL emit an event on every job status change and progress update, and the web UI SHALL reflect changes without a manual refresh.

#### Scenario: Live status update
- **WHEN** a render job transitions from "running" to "ready"
- **THEN** the topic view showing that job updates to display the new asset without a page reload
