## Purpose

Generates explanatory Manim videos and interactive isometric figures (built with the Anatomy agent skill) for each topic in a study plan so complex engineering and computer science concepts can be learned visually, with the generation done by agents in isolated workspaces.

## ADDED Requirements

### Requirement: Engineer can request visuals for a topic
The system SHALL allow an engineer to request generation of visuals for any topic in their current plan, choosing one or more formats from {video, figure}, and SHALL enqueue one generation job per requested format.

#### Scenario: Video requested
- **WHEN** an engineer requests format "video" for a topic
- **THEN** a generation job is created for that topic and format with status "queued"

#### Scenario: Request for whole plan
- **WHEN** an engineer requests visuals for all topics in the plan with formats from their preferences
- **THEN** one job per topic per enabled format is created, ordered by plan position

#### Scenario: Duplicate request deduplicated
- **WHEN** an engineer requests a format for a topic that already has a queued or running job for that format
- **THEN** no new job is created and the existing job identifier is returned

### Requirement: Agent produces a storyboard before rendering
The system SHALL produce for each generation job a storyboard: an ordered list of scenes, each with a learning objective, narration text in the profile's language, and a description of the visual, grounded in the topic's referenced chunks.

#### Scenario: Storyboard grounded in chunks
- **WHEN** a storyboard is generated
- **THEN** every scene cites at least one chunk referenced by the topic

#### Scenario: Storyboard respects depth preference
- **WHEN** the profile depth preference is "intuition-first"
- **THEN** the first scene states the intuition before any formal definition appears

### Requirement: Agent generates an executable Manim scene for video format
The system SHALL generate a Manim Community scene script from the storyboard, execute it in an isolated sandbox with no network access and a CPU time limit, and produce an MP4 with the narration as a caption track.

#### Scenario: Successful render
- **WHEN** the generated script renders without error
- **THEN** an MP4 asset and a caption file are stored and attached to the topic, and the job status is "ready"

#### Scenario: Script error triggers repair loop
- **WHEN** the script fails to execute
- **THEN** the agent receives the error output and retries with a corrected script, up to 3 attempts, after which the job is "failed" with the last error attached

#### Scenario: Sandbox limits enforced
- **WHEN** rendering exceeds the configured CPU time limit (default 15 minutes)
- **THEN** the sandbox is terminated and the job is "failed" with reason "timeout"

### Requirement: Agent builds an interactive figure for figure format
The system SHALL, for the figure format, derive a figure brief from the job's storyboard (the mechanism or object that embodies the concept, its true numbers, what moves, the readout) and run a figure-building agent session that follows the Anatomy skill instructions, with file and Node execution tools confined to an isolated per-job workspace without network access, to produce a standalone HTML figure with embedded styles and scripts, a PNG preview, and the build source, within a wall-clock limit (default 30 minutes) and a bounded number of agent turns. The storyboard, the brief, and the session transcript SHALL be stored with the asset as its provenance.

#### Scenario: Successful figure build
- **WHEN** the agent session finishes and the workspace contains a standalone HTML figure and a PNG preview
- **THEN** both files and the build source are stored and attached to the topic, and the job status is "ready"

#### Scenario: Figure provenance stored
- **WHEN** a figure asset is ready
- **THEN** the asset view shows the storyboard with its chunk citations, the brief derived from it, and the build source

#### Scenario: Figure not applicable
- **WHEN** the storyboard agent judges the topic has no mechanism, object, or structure to build
- **THEN** the job is "skipped" with the rationale, and no agent session is started

#### Scenario: Agent session produced no figure
- **WHEN** the agent session ends, times out, or exhausts its turn budget without a standalone HTML figure in the workspace
- **THEN** the job is "failed" with the session transcript tail attached, and no partial asset is stored

#### Scenario: Tools confined to the workspace
- **WHEN** the agent attempts to read or write a path outside its job workspace, or to run a command other than Node on workspace files
- **THEN** the tool call is rejected with an error the agent can see, and the job continues

### Requirement: Generated assets are attached to topics and viewable
The system SHALL list for each topic its assets with format, creation time, storyboard, and the script or build source that produced them, and SHALL serve video and figure assets for in-browser viewing.

#### Scenario: Asset listing
- **WHEN** an engineer views a topic with a ready video
- **THEN** the view shows the video, its captions, and a link to the storyboard and script

#### Scenario: Figure viewer
- **WHEN** an engineer views a topic with a ready figure asset
- **THEN** the figure is embedded in an isolated frame that cannot access the application's session or origin, responds to pointer and keyboard interaction, and offers a link to open it full-screen

### Requirement: Engineer can regenerate with feedback
The system SHALL allow an engineer to request regeneration of an asset with free-text feedback; the new job receives the previous storyboard, script, and feedback, and the previous asset is retained as history.

#### Scenario: Regeneration keeps history
- **WHEN** an engineer regenerates a video with feedback "too fast, show the proof step by step"
- **THEN** a new job is created carrying the feedback, and after completion both the old and new assets are listed with the new one marked current

### Requirement: Generation cost is bounded
The system SHALL enforce a configurable monthly budget of render minutes and a daily cap on generation jobs, reject new generation requests when either is exhausted, show the remaining amounts, and defer (not fail) jobs while the selected provider reports its subscription usage limit is reached.

#### Scenario: Render budget exhausted
- **WHEN** the month's render minutes are exhausted
- **THEN** new generation requests are rejected with the reset date, and queued render jobs are not started

#### Scenario: Provider limit reached mid-plan
- **WHEN** the provider reports its usage limit is reached while generation jobs are queued
- **THEN** the queued jobs remain queued with a "waiting for provider limit reset" message and resume automatically after the reported reset time
