# NotesBuddy Deterministic Selection Routing Implementation

**Status:** Implemented and enabled in the Telegram ordinary-text path

**Scope:** Process-scoped dialogue service, Telegram identity mapping,
user-facing view mirroring, pure numeric/ordinal routing, folder/file dispatch,
and safe selection errors

## Executive summary

Exact numeric and ordinal replies now resolve through the typed
`application.dialogue` active result set before greeting handling, Copilot, or
keyword NLP. A safely resolved folder opens through the existing navigation and
browse path. A safely resolved file follows the typed session's explicit
`FileSelectionBehavior`.

The integration is intentionally dual-state:

- `bot.nav` remains the legacy navigation and execution source;
- the typed dialogue service is the authoritative pre-intelligence validator
  for mirrored views;
- existing commands, callbacks, pending dictionaries, Copilot slots, and
  `nlp.context` remain in place.

No Drive, OAuth, database, dependency, deployment, search, confirmation, or
slot-filling architecture was migrated.

## Files created and changed

### Production file created

| File | Responsibility |
|---|---|
| `bot/dialogue.py` | Process service access, Telegram scalar identity mapping, compatibility account key, centralized view publication, pure-selection recognition, typed resolution, error mapping, selection recording, and action dispatch |

### Production files changed

| File | Change |
|---|---|
| `main.py` | Initializes one dialogue service in the PTB application |
| `application/dialogue/service.py` | Adds atomic, idempotent compatibility path synchronization |
| `application/dialogue/compat.py` | Adds typed `ResultItem` to legacy `IndexedItem` conversion for existing execution paths |
| `bot/nav.py` | Adds an immutable read-only folder-stack snapshot |
| `bot/handlers.py` | Inserts deterministic selection after existing pending state and passive email capture, before Copilot/NLP |
| `bot/commands.py` | Publishes command browse/search/suggestion views |
| `bot/callbacks.py` | Publishes callback browse/recent views |
| `nlp/router.py` | Publishes NLP views and exposes narrow resolved folder/details/download reuse functions |
| `bot/ui.py` | Adds focused Download, Details, and Cancel actions for `ASK` |

### Tests created

- `tests/test_dialogue_integration.py`
- `tests/test_selection_routing.py`

### Tests changed

- `tests/helpers.py`
- `tests/test_active_result_context.py`
- `tests/test_dialogue_characterization.py`
- `tests/test_dialogue_service.py`
- `tests/test_search_context_characterization.py`

The test helper now supplies Telegram-shaped chat/thread and shared
application `bot_data` values. Obsolete skips for deterministic routing and
configurable file selection were replaced by active tests.

## Process-scoped service initialization

`main.build_bot()` calls:

```text
initialize_dialogue_service(app)
```

The helper stores the service under:

```text
Application.bot_data["dialogue_service"]
```

Repeated initialization returns the existing service. It does not create an
import-time singleton or a repository per update.

The service uses:

- `InMemoryDialogueSessionRepository`;
- a 24-hour conservative session TTL;
- the existing NLP context TTL for active results;
- the existing `bot.nav.MAX_USERS` capacity;
- `time.monotonic` in production;
- injected clocks in tests.

Missing injection is handled safely. View publication logs and preserves the
legacy listing; pure selection returns a safe retry message and does not reach
the LLM.

## Telegram session identity

`telegram_session_identity_from_update()` extracts only scalar values and calls
`application.dialogue.compat.telegram_identity()`:

- `principal_id`: Telegram user ID as a string;
- `client_type`: `"telegram"`;
- `conversation_id`: effective chat ID as a string;
- `thread_id`: message/topic thread ID when available.

Missing user or chat produces no identity and no core call. Telegram objects
never enter the core dialogue package.

The compatibility account key is:

```text
telegram-principal:<principal_id>
```

It means only “the current authenticated principal's compatibility account.”
It does not claim a Google account ID, email, or provider metadata. Publication
sets it only after an existing authenticated result path succeeds.

## Centralized view publication

All explicit user-facing view publishers call:

```text
publish_active_view_to_dialogue(update, context, authenticated=True)
```

The helper:

1. reads the current `bot.nav.ViewContext`;
2. extracts the client-session identity;
3. gets or creates the process-scoped typed session;
4. sets the compatibility account key when needed;
5. snapshots and synchronizes the current legacy folder path;
6. converts the active index map through `application.dialogue.compat`;
7. replaces the immutable typed active result set;
8. returns its opaque ID and version.

The conversion does not mutate `bot.nav`.

If mirroring fails, the helper clears the typed active result where possible so
a newer legacy view cannot leave an older typed target selectable. It logs the
rollout failure and returns without crashing the already-working legacy
listing.

## Result-producing paths mirrored

### Commands

- `/info` and directory browse
- `/cd` child listing through `/info`
- `/search` results
- `/search` closest-match suggestions

### Callbacks

- browse
- refresh
- Home/back child listing
- recent files

### NLP paths

- indexed search results
- search suggestions
- browse/current-folder results
- folder opening child results
- recent files
- favorites
- file suggestions
- folder suggestions

The internal `_ensure_folder_view()` and `_ensure_file_view()` helpers are
intentionally not direct publishers. They hydrate candidate state for an
ongoing action but do not themselves render a numbered list. When those
candidates are later shown as a selectable suggestion view, the dedicated
suggestion publisher mirrors that view.

Empty/no-result messages that do not create a new legacy active view also do
not create a typed result set. This preserves current search behavior for this
bounded phase.

## Ordinary-text precedence

The effective order is:

1. pending email input;
2. pending OTP input;
3. pending Copilot slot;
4. existing pending action or confirmation;
5. passive authenticated email capture;
6. deterministic pure active-result selection;
7. greeting/Copilot interpretation;
8. keyword NLP fallback;
9. existing safe fallback.

Pending slot and pending action order is unchanged. Cancellation precedence is
also unchanged and remains a later migration.

## Pure-selection recognition

The pre-intelligence recognizer accepts only a complete message consisting of:

- a numeric token such as `1`, `2`, or `3`;
- first through tenth;
- numeric/word ordinal forms supported by `ActiveResultSet`;
- simple forms such as `the first one`;
- `last`, `last one`, and equivalent exact forms.

It does not intercept text that merely contains a number. For example:

- `find module 1 notes`;
- `show semester 2 files`;
- `search unit 3`;
- `download 2`;
- `open 1`.

Those continue through existing interpretation or command behavior. Resolution
ultimately occurs only through `DialogueSessionService`.

## Selection recording

The handler resolves against the active result-set ID/version and then calls
`select_item()` before dispatching the action. This records:

- result-set ID;
- result-set version;
- ordinal;
- immutable Drive item ID;
- selection time.

The session version increments. If the subsequent folder/details/download
operation fails, the selection record remains and the action failure is
reported separately.

## Folder behavior

Typed `FOLDER` items, and `SHORTCUT` items whose typed target kind is
`FOLDER`, always use the folder-open path.

`nlp.router.open_resolved_folder()`:

- receives the immutable resolved ID rather than re-resolving an index;
- validates folder/shortcut and loop state;
- updates the existing `bot.nav` stack;
- invokes the existing `_handle_browse()` Drive/listing and formatting path;
- mirrors the displayed child view;
- never invokes download;
- pops the newly pushed folder and sends a safe message when child listing
  fails.

## File behavior

The action is selected only from
`DialogueSession.file_selection_behavior`:

| Behavior | Result |
|---|---|
| `SHOW_DETAILS` | Default. Calls the existing metadata/details path and renders existing file actions |
| `DOWNLOAD` | Calls the existing download path, retaining its step-up, anomaly, size, task-queue, and delivery behavior |
| `ASK` | Renders Download, Details, and Cancel callbacks without executing a Drive operation |

The LLM cannot read the message and choose or mutate this setting.

## Safe errors

Pure selections never fall through to Copilot/NLP after a typed selection
failure.

| Condition | User treatment |
|---|---|
| No session or active results | Ask the user to browse or search first |
| Invalid/out-of-range ordinal | Ask for an item from the displayed list |
| Expired results | Ask the user to refresh or search again |
| Stale result ID/version | Ask the user to use the latest list |
| Optimistic version race | Ask the user to choose again |
| Missing service | Safe temporary-list error |
| Unexpected internal error | Sanitized retry/refresh message and server-side log |

The resolver does not guess through `nlp.context` or fall back to `bot.nav`
after a typed failure.

## Callback correlation

Existing callback payloads were not redesigned. Their 64-byte operational
format and existing behavior remain unchanged.

The typed publication helper returns result-set ID/version for a future short
correlation-token design. A full callback/token map and outgoing-message reply
correlation require a separate ADR and migration because current callbacks
carry direct Drive IDs and current plain replies do not carry the originating
message's result-set token.

Consequently, an uncorrelated plain reply always selects from the latest active
typed list for its client-session identity. Explicit stale ID/version inputs
are rejected today; detecting every delayed non-reply Telegram message requires
future message correlation.

## Temporary dual-state limitations

- `bot.nav` and the typed service are both updated for displayed views.
- Existing slash commands and callbacks continue to resolve from `bot.nav`.
- Pure numeric/ordinal ordinary text resolves only from typed state.
- `nlp.context` remains a separate search/history mechanism.
- Telegram `context.user_data` still owns pending slots, confirmations,
  uploads, OTP, and step-up state.
- The compatibility account key is principal-derived, not a future
  multi-account Drive identity.
- Process restart removes typed in-memory sessions; users must browse/search
  again.
- Plain Telegram messages have no result-set correlation unless a later phase
  stores outgoing-message bindings.

## Tests

The new and updated tests verify:

- one service instance is reused;
- different chats isolate typed sessions;
- missing service and mirror failures are safe;
- view ordering, kinds, account, and folder path mirror correctly;
- replacement creates a new ID and increments result version;
- mirroring does not mutate legacy navigation;
- numeric, ordinal, and last selection precede Copilot;
- fresh queries and unrelated text are not intercepted;
- folder selection opens and never downloads;
- child folder views are mirrored;
- folder failure restores the legacy path;
- default details, explicit download, and `ASK` choices;
- selection recording;
- invalid, expired, stale, missing, and cross-chat errors;
- pending slot/action characterization remains passing;
- command and NLP search keep their documented `nlp.context` differences.

## Migration tests enabled

The skipped ordinary-text test was replaced by the passing real-handler test:

```text
test_plain_numeric_reply_resolves_before_copilot
```

The earlier configurable-file-selection skip was also removed because
`SHOW_DETAILS`, `DOWNLOAD`, and `ASK` now have passing integration coverage.

The only remaining skip is typed confirmation expiry, which is outside this
phase.

## Rollback procedure

Rollback requires no data restoration:

1. remove the `handle_active_result_selection()` call from
   `bot.handlers.handle_text_input`;
2. remove `publish_active_view_to_dialogue()` calls from result publishers;
3. remove `initialize_dialogue_service()` from `main.build_bot()`;
4. leave `bot.nav`, commands, callbacks, NLP helpers, and Drive operations in
   place.

Because legacy navigation remains active and no schema changed, commands and
callbacks immediately continue with their pre-integration state path.

## Exact next-phase boundary

The next phase should introduce typed pending operations and confirmation
snapshots without changing Drive adapters:

1. model immutable target-bound pending operation and confirmation records;
2. migrate one existing mutable `pending_action` flow behind a compatibility
   adapter;
3. make cancellation an explicit deterministic transition before slot values;
4. add expiry, replay prevention, and optimistic version tests;
5. preserve the current Telegram rendering and execution functions.

Callback result correlation, persistent dialogue storage, search
consolidation, and multi-account Drive identity should remain separate later
decisions.

## Validation

Command:

```text
venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result:

| Measure | Count |
|---|---:|
| Total tests | 152 |
| Passed | 151 |
| Skipped | 1 |
| Failures | 0 |
| Errors | 0 |

The plain-number-before-NLP test is enabled and passing.
