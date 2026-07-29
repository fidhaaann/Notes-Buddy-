# NotesBuddy Typed Pending Operations Implementation

**Status:** Implemented for CREATE_FOLDER

**Scope:** Immutable pending operations, slot requests, target-bound
confirmations, expiry, replay protection, deterministic typed cancellation,
and Telegram CREATE_FOLDER integration.

## Executive summary

CREATE_FOLDER is the first workflow migrated from mutable Telegram
`user_data` into the process-scoped typed dialogue service. A request now owns
one immutable operation with a stable ID, idempotency key, account/session
identity, parent-folder snapshot, parameters, status, and expiry.

An incomplete request creates an authoritative typed `SlotRequest`. Exact
cancellation is handled before the slot value and before Copilot/NLP. A
complete request crosses an `EXECUTING` state before the existing Drive
creation function is called, preventing duplicate processing in the running
process.

No Drive adapter, OAuth flow, database schema, dependency, deployment, search
design, or non-CREATE_FOLDER pending workflow was migrated.

## Files created and changed in this phase

### Production files created

None.

### Production files changed

| File | Change |
|---|---|
| `application/dialogue/models.py` | Adds immutable operation, parameter, target, slot, and confirmation models and session fields |
| `application/dialogue/errors.py` | Adds typed pending-work transition, expiry, mismatch, cancellation, and replay errors |
| `application/dialogue/service.py` | Adds guarded, optimistic operation lifecycle transitions |
| `application/dialogue/__init__.py` | Exports the new public dialogue types |
| `bot/dialogue.py` | Adds cancellation recognition, CREATE_FOLDER planning/execution, typed slot routing, expiry messages, and replay boundary |
| `bot/handlers.py` | Routes typed cancellation and slots before legacy slots/actions and Copilot/NLP |
| `bot/commands.py` | Routes `/mkdir` through typed CREATE_FOLDER and lets `/cancel` cancel typed work |
| `nlp/router.py` | Routes keyword and Copilot CREATE_FOLDER execution through the typed workflow |
| `security/limits.py` | Adds configurable operation, slot, and confirmation TTL defaults |
| `bot/ui.py` | Adds one reusable typed-operation Cancel keyboard helper |
| `bot/callbacks.py` | Handles the isolated `dialogue:cancel` callback namespace |

### Tests created

- `tests/test_pending_operation_models.py`
- `tests/test_pending_operation_service.py`
- `tests/test_cancellation_routing.py`
- `tests/test_create_folder_dialogue.py`

### Tests changed

- `tests/test_dialogue_characterization.py`
- `tests/test_cancellation_routing.py` — callback cancellation, duplicate, and
  cross-chat tests
- `tests/test_create_folder_dialogue.py` — typed prompt Cancel-button test

### Documentation created

- `docs/PENDING_OPERATIONS_IMPLEMENTATION.md`

## Initial migrated workflow

CREATE_FOLDER was selected because it exercises a missing string slot and has
one narrow provider boundary:

```text
drive.drive_service.create_folder_async(
    telegram_user_id,
    folder_name,
    parent_id=resolved_parent_id,
)
```

The Drive function, audit behavior, and Telegram success formatter are reused.
Ordinary folder creation remains low risk and does not require confirmation,
matching current product policy.

Entry paths migrated:

- natural-language keyword CREATE_FOLDER;
- Copilot CREATE_FOLDER with and without a complete folder-name entity;
- `/mkdir <name>`;
- `/mkdir` followed by a typed slot response;
- ordinary exact cancellation while the typed slot is active;
- `/cancel` for active typed work, while preserving its existing upload cleanup.
- `dialogue:cancel` from the typed CREATE_FOLDER slot prompt.

## Typed models

### `PendingOperation`

Each record contains:

- opaque operation ID;
- `OperationType`;
- principal, account, and session IDs;
- optional source result-set ID/version;
- immutable target snapshots;
- typed `CreateFolderParameters`;
- risk level and finite status;
- stable idempotency key;
- creation and expiry timestamps.

`OperationType` includes future enum values, but service workflow logic accepts
only `CREATE_FOLDER`.

### `CreateFolderParameters`

The parameters capture:

- optional folder name while drafting;
- immutable parent folder ID;
- optional parent name snapshot.

Telegram input is normalized and bounded through existing security validators
before constructing or updating the typed value. The parent ID is captured
when the operation begins and is never re-resolved at execution.

### `OperationTargetSnapshot`

CREATE_FOLDER stores its planned parent as a folder target snapshot. Future
confirmations therefore describe the same immutable parent even if navigation
or active results change.

### `SlotRequest`

The CREATE_FOLDER request uses:

```text
slot_name = folder_name
expected_type = string
prompt_key = create_folder.folder_name
```

It has its own opaque ID, operation binding, attempt count, and TTL. It lives
in `DialogueSession`, not `_pending_slots`.

### `ConfirmationRequest`

Confirmations contain immutable operation/session/principal binding, summary,
target snapshots, consequence, reversibility, timestamps, and a finite status.
The service rejects mismatched, expired, denied, confirmed, or consumed
requests as appropriate.

## Operation lifecycle

The implemented CREATE_FOLDER lifecycle is:

```text
DRAFT -> AWAITING_SLOT -> READY -> EXECUTING -> consumed tombstone
                        READY -> AWAITING_CONFIRMATION -> READY
Any pre-execution active state -> CANCELLED or EXPIRED
EXECUTING -> FAILED on provider failure
```

Every service mutation increments `DialogueSession.state_version` exactly
once. New transition methods accept `expected_version`; stale callers receive
`VersionConflict`.

Only one non-terminal pending operation may own a client session. A second
consequential request receives:

```text
You already have an unfinished action. Complete it or cancel it first.
```

## Cancellation precedence

The ordinary-text order is now:

1. existing email input;
2. existing OTP input;
3. typed pending expiry;
4. exact cancellation of typed pending work;
5. typed slot answer;
6. legacy Copilot slot compatibility;
7. legacy pending-action compatibility;
8. deterministic active-result selection;
9. greeting/Copilot/NLP.

The pure recognizer accepts only complete messages:

- `cancel`
- `stop`
- `never mind`
- `nevermind`
- `forget it`
- `no`
- `abort`

It does not intercept `find cancel culture notes`, `create a folder called
Cancelled Projects`, or `rename it to Stop Motion`.

This phase deliberately does not change cancellation precedence inside
unmigrated rename/move/delete dictionaries.

### Inline callback cancellation

`ui.typed_pending_cancel_keyboard()` is the single reusable inline keyboard
helper. Its sole button uses the exact payload:

```text
dialogue:cancel
```

`bot.callbacks.handle_callback()` handles that namespace before existing
callback namespaces. It asks the typed dialogue service to cancel only the
calling Telegram principal/chat/thread session. On a live operation, slot, or
confirmation it sends:

```text
Cancelled. No changes were made.
```

When no live typed work exists, including a duplicate callback after a prior
cancellation, it sends:

```text
There is no active action to cancel.
```

The callback has no Drive or Copilot/LLM path and never reads or mutates the
legacy rename/move/delete `pending_action` dictionary. CREATE_FOLDER currently
has no confirmation prompt because its existing policy does not require one;
the helper is available to a later typed confirmation prompt without copying
button construction.

## Slot handling and execution

For an incomplete request:

1. create an immutable DRAFT operation;
2. create a bound slot request;
3. reply `What should I call it?`;
4. process cancellation before accepting any answer;
5. sanitize and fill the folder-name slot;
6. transition the operation to READY;
7. transition to EXECUTING;
8. call the existing Drive function using the captured parent ID;
9. record completion and clear pending slot/confirmation state.

The active result set is preserved.

Provider failure transitions the operation to FAILED and sends a truthful
error. It never sends the success formatter.

## Confirmation binding and expiry

CREATE_FOLDER does not force a new confirmation. The implemented confirmation
service supports later higher-risk migrations:

- exactly one confirmation belongs to one operation;
- target snapshots are copied from the operation;
- operation and confirmation identities must match;
- confirmation has an independent TTL;
- changing typed parameters through slot fill clears any old confirmation;
- confirmation can be confirmed or denied once;
- only a confirmed request can accompany a confirmed operation across the
  execution boundary;
- expiry marks both pending confirmation and operation unusable.

The previously skipped expired-confirmation characterization is enabled and
passes against this typed service.

## Expiry

Conservative defaults are configurable with:

- `PENDING_OPERATION_TTL_SECONDS` — 900 seconds;
- `SLOT_REQUEST_TTL_SECONDS` — 600 seconds;
- `CONFIRMATION_TTL_SECONDS` — 300 seconds.

The service uses its injected clock. Tests advance `FakeClock` and perform no
sleep or external call. Expired Telegram work receives:

```text
That action has expired. Please start it again.
```

It never reaches Copilot or executes Drive.

## Replay prevention

Replay controls are:

- operation IDs and idempotency keys are stable for the operation;
- `consume_operation()` changes READY to EXECUTING before the provider call;
- another delivery of the same operation cannot cross EXECUTING again;
- completion stores a bounded consumed-operation ID tombstone;
- repeated completion or consumption raises `OperationAlreadyConsumed`;
- slot state is cleared before execution and completion clears all pending
  request state;
- client-session identity isolates users, chats, and Telegram topics.

The current Google Drive create call has no provider idempotency token. A
process crash after Google creates the folder but before local completion is
recorded can therefore leave a created folder without a completion tombstone.
Because typed state is currently in memory, restart does not retry it
automatically. This reduces duplicate execution during one process lifetime
but is not claimed as exactly-once provider delivery.

## Legacy compatibility

Still in use and intentionally not removed:

- Copilot `_pending_slots` for non-CREATE_FOLDER intents;
- mutable `pending_action` dictionaries for rename, move, delete, copy, bulk
  actions, and their confirmations;
- OTP and step-up flags;
- upload state;
- `nlp.context`;
- legacy `bot.nav` state.

New CREATE_FOLDER requests are not dual-written to `_pending_slots` or
`pending_action`. Typed pending work is checked before both legacy stores.
Legacy pending-action tests continue to pass.

Known unmigrated behavior includes the legacy rename-name path accepting
`cancel` as a proposed name. Fixing that safely belongs to the RENAME
migration, where its immutable file target and confirmation snapshot can be
introduced together.

## Tests

Coverage includes:

- frozen models and immutable collections;
- explicit IDs and stable idempotency key;
- valid and invalid transitions;
- predictable state-version increments and stale versions;
- slot fill, expiry, and clearing;
- cancellation and client-session isolation;
- second-operation overwrite prevention;
- confirmation binding, mismatch, expiry, and single use;
- operation consume/complete replay rejection;
- exact cancellation before slot and LLM;
- conservative non-cancellation phrases;
- unfinished consequential-action blocking;
- direct and missing-name CREATE_FOLDER;
- exact slot name and repeated slot answer;
- no Drive call on cancellation or expiry;
- exact `dialogue:cancel` payload and typed prompt keyboard;
- callback cancellation, no-active guidance, duplicate callback safety, and
  cross-chat isolation;
- no Drive, Copilot, or legacy pending-action effects from callback
  cancellation;
- truthful provider failure;
- `/mkdir` compatibility;
- non-migrated pending-action compatibility.

Final skipped-test status: zero skipped tests.

## Rollback procedure

No database or Drive restoration is required:

1. route `nlp.router._handle_mkdir()` back to its former direct/pending-action
   implementation;
2. route `bot.commands.cmd_mkdir()` back to its direct Drive call;
3. remove `handle_typed_pending_dialogue()` from ordinary-text precedence;
4. remove typed cancellation from `cmd_cancel` and `dialogue:cancel` from the
   callback dispatcher;
5. leave the selection-routing dialogue service and all legacy state in place.

Existing Drive and Telegram boundaries were not moved, so rollback does not
require provider changes.

## Exact next migration boundary

Migrate RENAME alone:

1. resolve the file once into an immutable target snapshot;
2. add typed rename parameters and its new-name slot;
3. bind confirmation to the operation and original Drive item ID;
4. apply deterministic cancellation before the rename slot;
5. retain current step-up and Drive rename functions;
6. add delayed-list, confirmation-expiry, replay, and provider-failure tests.

MOVE, DELETE, bulk operations, persistence, provider idempotency, callback
correlation, and legacy search consolidation should remain separate phases.

## Validation

Required command:

```text
venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result:

| Measure | Count |
|---|---:|
| Total tests | 182 |
| Passed | 182 |
| Skipped | 0 |
| Failures | 0 |
| Errors | 0 |

Compilation, import smoke tests, and `git diff --check` also passed.
