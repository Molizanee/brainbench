## Purpose

Lets the engineer sign in to their own ChatGPT (Codex) or Anthropic subscription from the web UI and use it as BrainBench's inference provider: BrainBench runs the provider's OAuth flow itself, keeps the resulting credentials in its own encrypted vault, refreshes them, tracks usage windows, and calls the provider APIs directly, with no API keys and no vendor CLI.

## ADDED Requirements

### Requirement: Providers are declared in a registry
The system SHALL ship a provider registry where each provider declares an id, display name, wire API, base URL, login kind from {oauth-code, device-code}, token refresh policy, callback port when applicable, whether identity is organization-scoped, and the models it offers with context window and output limits; the registry SHALL be the single source for the providers page, login dispatch, and model selection.

#### Scenario: Registry drives the providers page
- **WHEN** the engineer opens the providers page
- **THEN** every registry provider is listed with its login kind and the models it offers, even before any account is connected

#### Scenario: Provider can be disabled
- **WHEN** the engineer disables a provider in settings
- **THEN** its models disappear from every selector and its stored accounts are kept but not used, until it is re-enabled

### Requirement: Engineer signs in with the provider's own OAuth flow from the web UI
The system SHALL start the provider's OAuth flow when the engineer clicks "Connect": for oauth-code providers it SHALL generate a PKCE challenge and state, show the authorization URL, always offer a field to paste the redirect URL or code, and additionally listen on the provider's loopback callback port when the deployment publishes that port on the machine the engineer's browser runs on; whichever completes first (callback or paste) finishes the login. For device-code providers it SHALL show the verification URL and user code and poll until approval or expiry.

#### Scenario: Authorization-code sign-in with loopback callback
- **WHEN** the deployment publishes the callback port on the engineer's machine and the browser completes consent there
- **THEN** the callback is received on the loopback port, the code is exchanged for tokens, and the account appears as "connected" without further input

#### Scenario: Authorization-code sign-in from a remote browser
- **WHEN** the engineer's browser runs on a different machine than the server, so the provider's redirect to localhost fails in the browser
- **THEN** the UI already shows the paste field, the engineer pastes the failed redirect URL, the code and state are extracted and verified, the code is exchanged, and the account appears as "connected"

#### Scenario: Paste accepted while callback listener is active
- **WHEN** the loopback listener is active and the engineer pastes the redirect URL instead
- **THEN** the login completes from the paste, the listener is closed, and a late callback for the same state is ignored

#### Scenario: Device-code sign-in
- **WHEN** the engineer connects a device-code provider
- **THEN** the UI shows the verification URL and user code, polls at the provider's interval, and marks the account "connected" on approval or shows "expired" when the code expires

#### Scenario: State mismatch rejected
- **WHEN** a callback or pasted redirect carries a state that does not match the pending login
- **THEN** the login is rejected and no credential is stored

### Requirement: Credentials are stored in an encrypted vault owned by BrainBench
The system SHALL store each account's access token, refresh token, expiry, identity (email, account or organization id, plan label when known), and provider in a vault encrypted at rest with a key file created on first run with owner-only permissions; refresh tokens SHALL never be returned by any API or shown in the UI, and access tokens SHALL never be sent to the browser.

#### Scenario: Secrets not exposed
- **WHEN** the providers page or any API response describes a connected account
- **THEN** it includes the identity and expiry only, never a token

#### Scenario: Vault unreadable without key
- **WHEN** the vault key file is missing or does not match the fingerprint recorded when credentials were first stored
- **THEN** stored accounts are reported as "locked", inference is rejected with a message naming the key path, no new key is generated, and the providers page shows the recovery options: restore the key file from backup, or explicitly reset the vault, which deletes every stored credential and generates a new key

#### Scenario: Explicit vault reset
- **WHEN** the engineer confirms a vault reset on a locked vault
- **THEN** all stored credentials are deleted, a new key and fingerprint are generated, and every provider shows as disconnected

### Requirement: Tokens are refreshed automatically
The system SHALL refresh an OAuth access token before expiry with a single in-flight refresh per account, SHALL retry transient refresh failures on the next sweep, and SHALL disable an account with a recorded cause on a definitive failure (revoked or invalid grant) without deleting it.

#### Scenario: Refresh before expiry
- **WHEN** an account's access token is within five minutes of expiry
- **THEN** the background refresher obtains a new token and subsequent requests use it

#### Scenario: Revoked grant disables account
- **WHEN** the provider answers a refresh with an invalid-grant error
- **THEN** the account status becomes "needs-reauthentication" with the cause recorded, and the providers page offers "Connect" again

### Requirement: Multiple accounts per provider are supported and ranked
The system SHALL allow more than one connected account per provider, treating each organization or workspace as its own account, and SHALL pick the account for a request by skipping blocked or disabled accounts, then preferring the account with the most remaining usage in its current window, then the engineer's priority order.

#### Scenario: Blocked account skipped
- **WHEN** account A is rate-limit blocked until a known reset time and account B is not
- **THEN** new requests use account B until A's block expires

#### Scenario: All accounts blocked
- **WHEN** every account of the routed provider is blocked
- **THEN** jobs are deferred until the earliest reset time and the tutor reports the reset time

### Requirement: Usage windows and rate limits are tracked per account
The system SHALL fetch each account's usage windows (for example five-hour and weekly) from the provider when available, cache them for five minutes, record a block with the reset time when the provider answers a rate-limit error, and show windows and blocks on the providers page.

#### Scenario: Usage visible
- **WHEN** the engineer opens the providers page for a connected account with usage reporting
- **THEN** each window shows used percentage and reset time

#### Scenario: Rate limit recorded
- **WHEN** a request receives a rate-limit response with a reset hint
- **THEN** the account is blocked for that scope until the reset time and the providers page shows the block

### Requirement: Inference calls the provider API directly
The system SHALL perform every model call by sending the provider's wire format to its API with the selected account's access token, supporting native tool calling, streaming, and structured output as the wire format allows; on an authentication error it SHALL refresh once and retry before failing.

#### Scenario: Streaming completion
- **WHEN** the tutor sends a message
- **THEN** tokens are streamed from the provider API to the panel as they arrive

#### Scenario: Auth error recovery
- **WHEN** a request fails with an authentication error on a token the vault considers valid
- **THEN** the token is refreshed once and the request is retried; a second failure disables the account as "needs-reauthentication"

### Requirement: Engineer can sign out an account
The system SHALL allow the engineer to remove a connected account, deleting its tokens from the vault and revoking them at the provider when the provider supports revocation.

#### Scenario: Sign out
- **WHEN** the engineer removes an account
- **THEN** its tokens are deleted, it no longer appears, and in-flight requests using it finish or fail without starting new ones

### Requirement: Engineer chooses provider and model per task class
The system SHALL let the engineer select, for each task class {curation, storyboarding, script-generation, script-repair, figure-building, tutoring}, an enabled provider and a model from the registry, with defaults applied when unset, and SHALL reject starting a job or tutor message whose task class has no connected, enabled account.

#### Scenario: Default routing
- **WHEN** only Anthropic is connected and no routing is set
- **THEN** every task class routes to Anthropic with its default model

#### Scenario: No provider connected
- **WHEN** the engineer requests curation and no account is connected
- **THEN** the request is rejected with a message linking to the providers page

### Requirement: Usage is reported per provider and account
The system SHALL record per call the provider, account, model, and token counts, and show a daily summary per provider and account.

#### Scenario: Usage summary
- **WHEN** the engineer opens the providers page after running jobs
- **THEN** each account shows today's call count and input/output tokens
