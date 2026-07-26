# NotesBuddy Dialogue Foundation Implementation

**Status:** Implemented foundation; not yet wired into user-facing handlers

**Scope:** Typed dialogue models, repository seam, in-memory implementation,
state-transition service, compatibility conversions, and focused tests

## Executive summary

NotesBuddy now has a transport-neutral typed dialogue-state foundation under
`application/dialogue/`. It provides immutable session and result models,
explicit state/version invariants, typed errors, a bounded optimistic
repository, clock-driven state transitions, and a narrow conversion boundary
for current `bot.nav` values.

This phase does not change user-visible behavior. The new service is not used by
Telegram handlers, does not alter ordinary text-message precedence, and does
not replace `bot.nav`, `nlp.context`, Copilot slot state, or mutable
`context.user_data` pending actions.

## Files created

### Production foundation

| File | Responsibility |
|---|---|
| `application/__init__.py` | Marks the transport-neutral application package |
| `application/dialogue/__init__.py` | Public dialogue-foundation exports |
| `application/dialogue/models.py` | Immutable identities, enums, folder/result/session models, validation, and exact ordinal resolution |
| `application/dialogue/errors.py` | Typed dialogue, selection, expiry, stale-result, navigation, missing-session, and version-conflict errors |
| `application/dialogue/repository.py` | Provider-neutral `DialogueSessionRepository` protocol |
| `application/dialogue/memory_repository.py` | Bounded, lock-protected, LRU in-memory repository with optimistic version checks |
| `application/dialogue/service.py` | Immutable session, navigation, result-set, selection, preference, cancellation, and expiry transitions |
| `application/dialogue/compat.py` | One-way conversion of current `bot.nav` identities, paths, views, and indexed items |

No existing production file was modified.

### Tests

Created:

- `tests/test_dialogue_models.py`
- `tests/test_dialogue_repository.py`
- `tests/test_dialogue_service.py`
- `tests/test_dialogue_compat.py`

Changed:

- `tests/helpers.py` adds deterministic `FakeClock` and `SequenceIds` helpers.
- `tests/test_active_result_context.py` removes the obsolete skipped
  delayed-result-set target test; active coverage now exists at the new service
  boundary.

## Model decisions

### Immutability

All model dataclasses use `frozen=True` and `slots=True`. Collection fields use
immutable types:

- folder history and parent IDs are tuples;
- result items are an ordered tuple;
- capabilities are a `frozenset`.

Callers cannot mutate a session in place. `DialogueSessionService` uses
dataclass replacement to construct a new session and saves it with an expected
state version.

### Finite state

The new transport-neutral enums are:

- `DialogueState`: `READY`, `AWAITING_SLOT`, `AWAITING_CONFIRMATION`,
  `AWAITING_STEP_UP`, `AWAITING_UPLOAD`, `JOB_QUEUED`, `EXPIRED`, and
  `CANCELLED`;
- `ExperienceMode`: `GUIDED` and `EXPERT`;
- `FileSelectionBehavior`: `SHOW_DETAILS`, `DOWNLOAD`, and `ASK`;
- `ItemKind`: `FILE`, `FOLDER`, and `SHORTCUT`.

The session defaults to `READY`, `GUIDED`, and `SHOW_DETAILS`. These values do
not imply Telegram rendering, Drive execution, confirmation, or LLM behavior.

### Validation

Core identifiers and names must be non-empty, bounded strings without control
characters. Ordinals and versions must be positive integers. Timestamps must be
finite. The model also enforces:

- matching `DialogueSession.principal_id` and session identity;
- immutable collection inputs;
- unique result ordinals;
- unique Drive item IDs within one active result set;
- consistent shortcut kind/metadata;
- an expiry later than creation for active result sets;
- explicit session expiry state.

Duplicate item IDs are intentionally rejected within one active result set.
There is no current product case that requires one Drive item to occupy
multiple visible ordinals; supporting that later requires an explicit decision
about selection ambiguity.

## Identity-key choice

`ClientSessionIdentity` contains:

- stable NotesBuddy `principal_id`;
- `client_type`;
- client-local `conversation_id`;
- optional `thread_id`.

It is immutable and hashable, so it can safely key repository records. The
same principal in different chats, clients, or threads receives isolated
dialogue sessions while account ownership remains principal-scoped.

`compat.telegram_identity()` converts scalar Telegram user, chat, and optional
thread IDs into this identity. It accepts values rather than Telegram objects
and imports no Telegram classes.

The current private-chat mapping is:

```text
principal_id   = str(Telegram user ID)
client_type    = "telegram"
conversation_id = str(Telegram chat ID)
thread_id      = optional topic/thread ID
```

Handlers are not changed to use this key in this phase.

## Result-set and session versioning

### Session versions

- A new session starts at `state_version = 1`.
- Every successful state mutation creates a new session with exactly
  `state_version + 1`.
- Read-only selection resolution does not change the version.
- A no-op pop at Home returns the existing session without a new version.
- Repository updates validate the caller's `expected_version`.
- A stale expected version or skipped incoming version raises
  `VersionConflict`.
- A different session ID cannot overwrite an existing identity key.

### Result-set versions

`replace_active_results()`:

1. generates a new opaque ID using UUID4 by default;
2. creates an immutable ordered item tuple;
3. starts at result version 1 or increments the currently active result
   version;
4. sets explicit creation and expiry timestamps;
5. saves the containing session as one optimistic mutation.

Resolution can receive an expected result-set ID and version. If either differs
from the active result set, the service raises `StaleResultSet`. This prevents a
delayed callback or correlated response from targeting an item that reused the
same visible ordinal in a newer view.

`resolve_selection()` only identifies a `ResultItem`. It does not open folders,
download files, call Drive, or choose a default execution action.

## Selection behavior

`ActiveResultSet` resolves:

- positive integer ordinals;
- numeric strings;
- first through tenth, including numeric ordinal forms;
- simple forms such as `"the first one"`;
- `"last"` variants.

Resolution is exact and deterministic. There is no fuzzy filename matching in
this phase. Invalid or out-of-range values raise `InvalidSelection`; expired
sets raise `ExpiredContext`.

`select_item()` stores an immutable `SelectedItemReference` containing the
result-set ID/version, ordinal, item ID, and selection timestamp.

## Expiry behavior

Both sessions and active result sets have explicit timestamps.

- The repository accepts an injected clock and removes expired sessions on
  access, cleanup, and count.
- Session mutations refresh the session inactivity deadline.
- Result expiry is independent and checked at selection time.
- The service accepts injected session and result TTLs.
- `expire_session()` performs an explicit versioned transition to `EXPIRED`;
  the repository then treats the record as expired.
- Tests use `FakeClock`; no wall-clock sleeps are required.

## Repository behavior

`DialogueSessionRepository` is synchronous and provider-neutral so future
SQLite or PostgreSQL implementations can preserve the same contract.

The in-memory implementation:

- keys only by `ClientSessionIdentity`;
- uses a private `OrderedDict`;
- protects compound operations with `threading.RLock`, which is appropriate for
  the current single-process async runtime because methods contain no awaits;
- supports deterministic maximum capacity and least-recently-used eviction;
- cleans expired sessions;
- returns immutable session values;
- uses optimistic expected-version checks.

It does not replace the process-global dictionary in `bot.nav`.

## Navigation transitions

The service provides:

- push folder;
- pop folder;
- go Home;
- clear/replace active results.

Push records the previous current folder in immutable history and rejects a
folder ID already present in the active path, preventing direct navigation
loops. Pop at Home is a safe no-op. In the new model, go Home clears history and
active results as specified by the target architecture.

This differs from current `bot.nav.go_home()`, which preserves the active view.
Because the new service is not wired into handlers, that user-visible legacy
behavior remains unchanged in this phase.

## Compatibility strategy

`application/dialogue/compat.py` is the only new core-adjacent file that imports
`bot.nav`.

It provides one-way, non-mutating conversion for:

- scalar Telegram IDs to `ClientSessionIdentity`;
- `IndexedItem` to `ResultItem`;
- a flat current index map or `ViewContext` to an ordered result tuple;
- current `(folder_id, name)` stacks to parent-linked `FolderLocation` tuples.

Conversions preserve visible ordinals, IDs, names, MIME types,
folder/file/shortcut kind, shortcut target metadata, and view source. Current
`IndexedItem` has no parent-ID or capability fields, so those values remain
empty rather than being invented.

The adapter does not dual-write state, clear `bot.nav`, or make the new
repository authoritative.

## Tests added

The 41 new focused tests cover:

- identity immutability, hashing, validation, and conversation isolation;
- default session values and principal consistency;
- result-item metadata and immutable collections;
- result-set version/ordinal/item invariants;
- numeric, ordinal, and last selection;
- typed invalid and expired selection errors;
- repository stability, isolation, optimistic saves, stale conflicts, expiry,
  deletion, capacity, and LRU behavior;
- session creation, account/preferences, predictable state versions;
- push, pop, Home, loop prevention, and independent sessions;
- active-result replacement, expiry, selection, and selected-item recording;
- explicit cancellation and expiry states;
- Telegram scalar, current path, view, item-kind, shortcut, and ordering
  compatibility conversions;
- proof that compatibility conversion does not mutate legacy navigation data.

All tests are local, deterministic, and make no external calls.

## Skipped migration test enabled

The prior skipped test for rejecting a delayed selection from a replaced result
set was removed from the legacy `bot.nav` characterization module and replaced
with the passing service test:

```text
test_delayed_selection_using_replaced_result_set_is_rejected
```

It creates one result set, replaces it, then proves that the old ID/version
raises `StaleResultSet`.

The plain numeric routing target test remains skipped. Ordinary
`handle_text_input` precedence was not changed.

## Known legacy state still in use

The running bot continues to use:

- `bot.nav._sessions` for navigation and active views;
- Telegram `context.user_data`;
- `nlp.context.SearchContext` and NLP state;
- Copilot `_pending_slots`;
- mutable `pending_action`, upload, OTP, and step-up dictionaries;
- current ordinary-message Copilot/NLP precedence.

No current handler imports `application.dialogue`, and no state is mirrored
automatically. This is intentional for a behavior-preserving foundation phase.

## Exact boundary for the next phase

The next phase should integrate deterministic selection state without
refactoring Drive operations or pending-action execution:

1. Construct one process-scoped repository and service in bootstrap.
2. Map Telegram user/chat/thread scalar IDs through `telegram_identity()`.
3. Mirror newly displayed `bot.nav` views into the typed service at the narrow
   view-publication boundary while retaining `bot.nav` as a rollback source.
4. Add a pure pre-intelligence resolver for exact numeric/ordinal selections,
   requiring correlated result-set ID/version when the client can provide it.
5. Route a resolved folder to the existing open-folder path and apply the
   explicit file-selection preference through existing operations.
6. Enable the still-skipped plain-number-before-NLP test.

That phase must continue to leave mutable pending actions, Drive behavior,
OAuth, database schema, and response formatting unchanged. Cancellation
precedence and typed pending operations should remain separate subsequent
migrations.

## Validation

Required command:

```text
venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result:

| Measure | Count |
|---|---:|
| Total tests | 132 |
| Passed | 129 |
| Skipped | 3 |
| Failures | 0 |
| Errors | 0 |

The delayed-result-set test is enabled and passing. The remaining skips cover
future configurable file selection, plain-number routing precedence, and typed
confirmation expiry.
