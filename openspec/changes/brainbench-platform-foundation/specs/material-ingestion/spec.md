## Purpose

Lets an engineer feed raw study material into the platform from several source types and turns it into searchable, embedded text chunks that curation agents can reason over.

## ADDED Requirements

### Requirement: Engineer can submit a source
The system SHALL accept a source of type URL, PDF upload, Git repository URL, or plain-text note, associate it with the engineer's profile, and return a source identifier with status "queued".

#### Scenario: URL source accepted
- **WHEN** an engineer submits a source of type URL with a well-formed http or https address
- **THEN** the system stores the source with status "queued" and enqueues an ingestion job

#### Scenario: PDF upload accepted
- **WHEN** an engineer uploads a PDF file no larger than 50 MB
- **THEN** the system stores the file and the source with status "queued"

#### Scenario: Oversized PDF rejected
- **WHEN** an engineer uploads a PDF larger than 50 MB
- **THEN** the system rejects the upload with an error stating the limit

#### Scenario: Malformed URL rejected
- **WHEN** an engineer submits a URL source whose address is not a valid http or https URL
- **THEN** the system rejects the request with a validation error

### Requirement: Sources are extracted into text chunks
The system SHALL extract text from each source, split it into chunks of at most 2000 characters at sentence boundaries, and store each chunk with its ordinal position and a reference to the originating location (page number, URL fragment, or file path). A single sentence longer than 2000 characters SHALL be split at the last whitespace before the limit, or at the limit when there is no whitespace, and each piece SHALL be marked as a continuation of the same sentence.

#### Scenario: PDF extracted with page references
- **WHEN** ingestion of a PDF source completes
- **THEN** every chunk records the page number it was extracted from

#### Scenario: Oversized sentence split
- **WHEN** extraction yields a single sentence of 4500 characters containing whitespace
- **THEN** it is stored as consecutive chunks each at most 2000 characters, each boundary at the last whitespace before the limit when one exists in that span and at the hard limit otherwise, whose concatenation reproduces the sentence exactly, with every chunk flagged as a continuation of that sentence and ordered by position

#### Scenario: Git repository extracted by file
- **WHEN** ingestion of a Git repository source completes
- **THEN** chunks exist for README, documentation, and source files under 1 MB, each recording its file path, and binary files are skipped

#### Scenario: Extraction failure reported
- **WHEN** text extraction fails for a source
- **THEN** the source status becomes "failed" with a human-readable reason and no partial chunks are retained

### Requirement: Chunks are embedded for semantic retrieval
The system SHALL compute a vector embedding for every chunk using a model that runs on the host without any external API, and support a semantic query that returns the top-k most similar chunks across all sources, with k between 1 and 50.

#### Scenario: Semantic query returns ranked chunks
- **WHEN** an engineer queries "Raft leader election" with k=5 after ingesting a distributed systems textbook
- **THEN** the system returns at most 5 chunks ordered by similarity, each including its source identifier and location reference

### Requirement: Engineer can inspect ingestion status
The system SHALL expose for every source its status from {queued, extracting, embedding, ready, failed}, chunk count, and the failure reason when failed.

#### Scenario: Status progresses to ready
- **WHEN** extraction and embedding complete for a source
- **THEN** the source status is "ready" and the chunk count equals the number of stored chunks

#### Scenario: Status listing
- **WHEN** an engineer lists their sources
- **THEN** each source shows its type, title, status, chunk count, and submission time

### Requirement: Engineer can delete a source
The system SHALL allow an engineer to delete a source, removing its chunks and embeddings, and marking any topics derived solely from it as orphaned.

#### Scenario: Delete removes chunks
- **WHEN** an engineer deletes a source with status "ready"
- **THEN** the source and all its chunks are no longer returned by listings or semantic queries

#### Scenario: Delete during ingestion
- **WHEN** an engineer deletes a source whose ingestion job is still running
- **THEN** the job is cancelled and the source is removed
