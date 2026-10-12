## Why

Agents make generating study material cheap, but choosing what is worth studying, stripping the noise, and turning dense engineering topics into something visual is still manual and does not scale with the volume of new content. BrainBench exists so an engineer declares where they are and what they want to master, and agents do the heavy work: curate sources, prioritize topics against the engineer's goals, produce Manim videos and interactive isometric figures for each topic, and answer questions in the context of the material being studied, using the engineer's existing ChatGPT or Claude subscription instead of API keys.

## What Changes

- Introduce BrainBench as a self-hosted, open source, single-tenant application: one engineer per instance, web UI as the primary interface, API and background workers behind it. Installation is `docker compose up` on a host with Docker; the stack builds its own images, bootstraps its data directory, applies migrations, and announces the browser URL, with first-run setup completed in the browser.
- Add provider connection modeled on omp (oh-my-pi): the engineer signs in with their ChatGPT (Codex) or Anthropic account through the provider's own OAuth flow run by BrainBench from the web UI; tokens live in a BrainBench-owned encrypted vault, are refreshed automatically, and inference calls the provider APIs directly with native tool calling and streaming. Multiple accounts per provider, usage windows, and rate-limit-aware account selection are supported. No API keys and no vendor CLI.
- Add a learner profile: current skills with self-assessed levels, target skills with mastery goals, time budget, and learning preferences.
- Add material ingestion: the engineer submits URLs, PDFs, Git repositories, and plain notes; the system extracts text, chunks it, embeds it with a local embedding model, and stores it for retrieval.
- Add agent-driven curriculum curation: agents extract topics from ingested materials, score each topic for relevance against the profile, discard noise, and produce an ordered study plan with prerequisite edges.
- Add visual content generation: for each planned topic, agents write a Manim scene script rendered in an isolated container, and, when the topic has a mechanism or structure worth building, a figure-building agent follows the Anatomy skill (`wheresryan22/anatomy`) with file and Node tools confined to a sandboxed workspace to build an interactive isometric figure (standalone HTML with SVG, optional WebGL and 3D mode); MP4, captions, HTML figures, and previews are attached to the topic.
- Add a tutor agent in the sidebar on every page that explains concepts using the current topic's material, the plan, and the profile as context, with cited sources, streaming replies, and persisted threads.
- Add learning progress tracking: the engineer marks topics studied and rates mastery; the study plan re-prioritizes from that feedback.
- Add a job system so every agent and render step runs asynchronously with visible status, retries, and failure reasons.

## Capabilities

### New Capabilities
- `provider-connection`: A declarative provider registry; OAuth sign-in (authorization-code with PKCE, device-code) from the web UI; an encrypted credential vault; automatic refresh; multiple accounts per provider with usage windows, rate-limit blocks, and ranked selection; direct API inference; provider and model routing per task class.
- `learner-profile`: Creating and updating the engineer's current skills, target skills with mastery goals, time budget, and preferences; the profile is the input to every agent decision.
- `material-ingestion`: Submitting sources (URL, PDF, Git repo, note), extracting and chunking their text, embedding chunks locally, and exposing ingestion status per source.
- `curriculum-curation`: Agent pipeline that derives topics from ingested materials, scores relevance against the learner profile, filters noise, and emits a prioritized study plan with prerequisites and rationale.
- `visual-content-generation`: Agent generation of Manim videos and Anatomy interactive figures per topic, isolated rendering and skill sessions, asset storage, and attaching generated assets to topics with regeneration on request.
- `tutor-chat`: Sidebar tutor agent bound to the current page context, grounded in the engineer's chunks with citations, profile-aware explanations, streamed replies, persisted threads, and links into the plan.
- `learning-progress`: Recording study sessions and mastery ratings per topic and feeding them back into curation so the plan re-prioritizes.
- `job-orchestration`: Durable background jobs for ingestion, curation, and rendering with status, retries, cancellation, and failure reasons visible in the web UI.
- `self-host-deployment`: One-command bootstrap from a fresh clone (`docker compose up`), announced access URL, in-browser first-run wizard, worker self-discovery of sandbox mount paths, idempotent re-runs, and pull-and-up upgrades.

### Modified Capabilities

(none, greenfield project)

## Impact

- New repository layout: `apps/api` (Python 3.15 via `uv`, FastAPI, API and worker entrypoints, PEP 810 lazy imports for heavy dependencies), `apps/web` (React 19 + MUI, TypeScript, Bun as package manager, bundler, server, and test runner), `packages/providers` (omp-style provider layer: registry, vault, OAuth engines, refresher, usage, selection, wire transports, Strands model adapters), `packages/agents` (Strands Agents SDK: agent factories, tools, output schemas, sandboxed figure tools), `infra/` (Docker Compose, app image, Manim and Anatomy sandbox images, Postgres with pgvector). Conventions in `BACKEND_GOOD_PRACTICES.md` and `FRONTEND_GOOD_PRACTICES.md` at the repo root.
- External dependencies on the host: Docker only. Everything else is built or pulled by compose: Postgres 16 with pgvector, the app image with a local embedding model (ONNX), a Manim sandbox image, and an Anatomy sandbox image (Node 20 and Chromium). The Anatomy skill is vendored by the `init` service at a pinned commit under `./data/skills/anatomy`.
- No API keys anywhere in the system. OAuth tokens live only in the instance's encrypted vault (`BRAINBENCH_DATA/vault.key`) and are never exposed to the browser or any API response.
- Policy exposure: Anthropic's terms do not permit third-party apps to offer Claude.ai login or route requests through subscription credentials, and OpenAI documents ChatGPT sign-in for Codex surfaces only. This is the same posture omp and OpenCode take; because BrainBench is self-hosted, the exposed account is the engineer's own. The design states this in the UI and README, lets each provider be disabled, and isolates the provider layer so it can be swapped.
- New persisted data: provider settings, learner profile, sources and chunks with embeddings, topics, study plans, generated assets, study sessions, jobs.
- Runtime risk: subscription rate limits apply per provider account; rendering is CPU-heavy. Concurrency caps, render-minute budgets, and provider rate-limit backoff are part of the design.
