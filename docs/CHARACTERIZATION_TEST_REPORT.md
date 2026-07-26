# NotesBuddy Dialogue Characterization Test Report

**Status:** Completed testing-only phase

**Scope:** Current navigation, active-result, selection, pending-state, cancellation, search-context, and expiry behavior

**Authoritative references:**

- `docs/CURRENT_ARCHITECTURE_AUDIT.md`
- `docs/PRODUCT_VISION.md`
- `docs/SYSTEM_ARCHITECTURE.md`

## Executive summary

This phase adds deterministic, network-free characterization tests around the
state seams that must be protected during the dialogue-system migration. The
tests distinguish three things:

1. current behavior that should be preserved;
2. current behavior that is unsafe or inconsistent but must remain visible
   until deliberately migrated;
3. target behavior that is documented as skipped tests rather than used to
   make the suite fail.

No production application code, dependency, configuration, database schema, or
existing test was changed.

## Validation result

Command:

```text
venv\Scripts\python.exe -m unittest discover -s tests -v
```

Result:

| Measure | Count |
|---|---:|
| Total tests | 92 |
| Passed | 88 |
| Skipped | 4 |
| Failures | 0 |
| Errors | 0 |

The 45 tests added in this phase comprise 41 passing current-behavior tests and
4 explicitly skipped target-behavior tests. All 47 pre-existing tests continue
to pass.

## Files added

| File | Purpose | Tests |
|---|---|---:|
| `tests/test_active_result_context.py` | Navigation isolation, active-view construction and replacement, selection helpers, item-kind metadata, and navigation/view expiry | 19 |
| `tests/test_dialogue_characterization.py` | Ordinary text routing, pending slots, pending confirmations/actions, overwrite behavior, and cancellation | 15 |
| `tests/test_search_context_characterization.py` | `bot.nav` versus `nlp.context`, command/NLP search behavior, query classification, reference resolution, and search expiry | 11 |
| `tests/helpers.py` | Minimal Telegram-shaped in-memory fakes with mocked replies | — |
| `docs/CHARACTERIZATION_TEST_REPORT.md` | This report | — |

## Current behaviors confirmed

### Navigation isolation

- `bot.nav` keeps independent folder stacks and active views for different
  Telegram user IDs.
- Push, pop, and home operations affect only the addressed user.
- One user's active index cannot resolve another user's item.
- `go_home()` resets the user's folder stack but currently leaves that user's
  active view intact.
- Navigation sessions and active views can expire independently by user.

### Active result views and selection

- `build_flat_index_map()` lists folders first and files second using flat
  string indices such as `"1"` and `"2"`.
- The current builder does not generate dotted hierarchical indices.
- Exact lookup resolves only exact visible keys; invalid and dotted keys return
  `None`.
- Folder, file, MIME-type, path, and shortcut metadata survive indexing.
- `resolve_smart()` handles numeric, ordinal, `last`, name-fragment, and
  file-type references where applicable.
- `copilot.dialogue.resolve_selection()` resolves pure numbers and ordinals but
  does not resolve action phrases such as `"download 2"` or `"open 1"`.
- The normalization helper extracts indices from both action phrases.
- The keyword interpreter carries the extracted index for `"download 2"` but
  currently drops it from the `OPEN_FOLDER` intent for `"open 1"`.
- The current default-action helper opens folders and downloads files.

### Pending slots and actions

- Copilot `_pending_slots` state is checked before mutable
  `pending_action`, Copilot interpretation, and keyword NLP.
- A pending folder-name slot captures `"Projects"`, clears the pending slot,
  and sends a reconstructed `MKDIR` intent to the executor.
- Slot state is isolated when Telegram supplies separate per-user
  `context.user_data` dictionaries.
- Confirmation responses execute the current pending dictionary and then clear
  it.
- A cancel/no/stop response clears a pending action when that action is already
  at its confirmation stage.
- Missing pending-action state returns the existing safe, unhandled form
  without executing or replying.

### Search and reference context

- `/search` populates the `bot.nav` active view.
- NLP search currently populates both `bot.nav` and the separate
  `nlp.context.SearchContext`.
- Replacing navigation results does not synchronize or invalidate an existing
  NLP search context.
- NLP search references resolve numeric, ordinal, last, name, and type
  references against `context.user_data`.
- The query classifier identifies explicit searches as fresh queries and
  numeric/ordinal/action references as follow-ups.
- A fresh-search signal wins when a phrase also contains an ordinal.
- Expired search context is rejected while unexpired context remains usable.
- Search expiry is isolated by the Telegram per-user data container.

## Known incorrect or migration-sensitive behaviors captured

These passing tests intentionally assert current behavior. They should change
only as part of an explicit migration:

1. **Unversioned result replacement.** A second active view replaces the first,
   and a delayed `"1"` can silently resolve to an unrelated item now occupying
   index 1.
2. **Plain numeric routing gap.** With an active folder result list, ordinary
   text `"1"` reaches the Copilot path without a preceding active-selection
   resolution step.
3. **Split search state.** Command search updates `bot.nav` but does not create
   or replace `nlp.context.SearchContext`; an old NLP context can therefore
   coexist with new command-search navigation results.
4. **Open-action index gap.** An index can be extracted from `"open 1"`, but the
   current keyword interpreter does not attach it to the `OPEN_FOLDER` intent.
5. **Mutable pending overwrite.** A later sensitive action replaces the single
   `context.user_data["pending_action"]` dictionary and discards the earlier
   operation.
6. **Cancellation after slot branches.** When `pending_action` is awaiting a
   rename name, `"cancel"` is captured as the new name before the cancellation
   branch is checked.
7. **Copilot slot cancellation gap.** When `_pending_slots` is awaiting a folder
   name, `"cancel"` is captured as the folder name and sent for execution.
8. **Partial slash cancellation.** `/cancel` clears upload state but leaves
   `pending_action`, `_pending_slots`, `awaiting_otp`, and
   `pending_stepup_action`.
9. **No confirmation expiry.** Mutable pending-action dictionaries contain no
   timestamp/version and have no expiry validation.
10. **Expired raw search state retained.** Expired NLP search context is rejected
    by accessors but remains stored in `context.user_data`.
11. **Home/view mismatch.** Returning home resets the folder stack without
    clearing or replacing the previous active view.

## Skipped target-behavior tests

The suite contains four precise skips:

| Target behavior | Skip reason |
|---|---|
| Reject a delayed index from a replaced result set | Current `bot.nav` has no result-set ID or version |
| Select file behavior from an explicit user preference | Current helper hard-codes download |
| Resolve a pure numeric/ordinal selection before Copilot/NLP | Current ordinary-text route has no pre-intelligence selection stage |
| Reject an expired confirmation | Current pending dictionaries have no timestamp or expiry |

These skips are executable migration markers. They should be enabled as the
corresponding target capability is introduced; they should not be deleted or
silently rewritten.

## Isolation and external-service controls

The new tests:

- use `unittest` and `IsolatedAsyncioTestCase`;
- use in-memory dictionaries and reset `bot.nav` module state;
- patch wall and monotonic clocks for expiry checks;
- mock handler replies and application calls;
- mock indexed-search results;
- do not call Telegram, Google Drive, Google OAuth, Gemini, SMTP, or any
  external network;
- do not require secrets, credentials, or a database migration.

## Coupling that made behavior difficult to test

### `bot.handlers.handle_text_input`

This single path imports or coordinates authentication, database state,
step-up verification, slot filling, mutable pending actions, Copilot,
keyword NLP, formatting, and monitoring. Testing one precedence decision
requires mocking several unrelated collaborators.

### `nlp.router`

Interpretation, target resolution, Drive calls, pending-state mutation,
confirmation prompting, search, navigation, formatting, and execution share
one module. Pending-action execution cannot be exercised deeply without Drive,
security, and notification collaborators, so these tests stop at the
execution boundary.

### Split state ownership

Relevant state is divided among:

- process-global `bot.nav._sessions`;
- Telegram `context.user_data`;
- `nlp.context` dictionaries;
- Copilot slot dictionaries;
- upload and step-up keys.

There is no single snapshot or transition API to assert against.

### Command search

`cmd_search` combines authentication, rate limiting, parsing, indexed search,
view construction, formatting, and Telegram rendering. The test uses mocks at
the authentication, rate-limit, and indexed-search boundaries while retaining
the real state update.

## Recommended exact boundary for the next implementation phase

The next phase should introduce only the authoritative dialogue-state seam,
not refactor Drive operations or rewrite handlers.

1. Add transport-neutral typed `DialogueSession`, `ActiveResultSet`,
   `ResultItem`, and state-transition contracts under an application dialogue
   package.
2. Add a `DialogueSessionRepository` protocol and an in-memory compatibility
   implementation keyed by the documented future `ClientSessionIdentity`.
3. Adapt the existing `bot.nav` API and relevant `context.user_data` reads
   through this seam while preserving the passing characterization tests.
4. Add result-set ID/version validation, then enable the skipped delayed-index
   test.
5. In the following bounded step, place a pure deterministic selection and
   cancellation resolver immediately before Copilot/NLP in
   `handle_text_input`, then enable the skipped numeric-routing test.

Drive adapters, policy consolidation, persistence migrations, and handler
formatting should remain outside that next boundary. This keeps rollback to the
existing state adapters possible and makes behavior changes explicit in the
test suite.
