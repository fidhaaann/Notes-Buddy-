"""Unit tests for immutable dialogue state transitions."""

import unittest

from application.dialogue.errors import (
    ExpiredContext,
    InvalidSelection,
    NavigationLoop,
    StaleResultSet,
)
from application.dialogue.memory_repository import (
    InMemoryDialogueSessionRepository,
)
from application.dialogue.models import (
    ClientSessionIdentity,
    DialogueState,
    ExperienceMode,
    FileSelectionBehavior,
    FolderLocation,
    ItemKind,
    ResultItem,
)
from application.dialogue.service import DialogueSessionService
from tests.helpers import FakeClock, SequenceIds


def _key(
    conversation_id: str = "chat-1",
    *,
    principal_id: str = "user-1",
) -> ClientSessionIdentity:
    return ClientSessionIdentity(
        principal_id=principal_id,
        client_type="telegram",
        conversation_id=conversation_id,
    )


def _item(ordinal: int, item_id: str, kind: ItemKind = ItemKind.FILE) -> ResultItem:
    return ResultItem(
        ordinal=ordinal,
        item_id=item_id,
        account_id="account-1",
        name_snapshot=f"Item {ordinal}",
        item_kind=kind,
    )


class DialogueSessionServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.repository = InMemoryDialogueSessionRepository(
            max_sessions=10,
            session_ttl_seconds=100.0,
            clock=self.clock,
            id_factory=SequenceIds("session"),
        )
        self.service = DialogueSessionService(
            self.repository,
            clock=self.clock,
            session_ttl_seconds=100.0,
            result_ttl_seconds=10.0,
            id_factory=SequenceIds("result"),
        )

    def test_create_session_uses_safe_defaults(self) -> None:
        session = self.service.get_or_create_session(_key())

        self.assertEqual(session.session_id, "session-1")
        self.assertEqual(session.current_folder, FolderLocation.home())
        self.assertEqual(session.state, DialogueState.READY)
        self.assertEqual(session.state_version, 1)

    def test_push_and_pop_folder_create_new_versions(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)

        pushed = self.service.push_folder(
            key,
            FolderLocation("folder-1", "Projects", parent_id="root"),
        )
        popped = self.service.pop_folder(key)

        self.assertEqual(pushed.current_folder.item_id, "folder-1")
        self.assertEqual(pushed.folder_history, (FolderLocation.home(),))
        self.assertEqual(pushed.state_version, 2)
        self.assertEqual(popped.current_folder, FolderLocation.home())
        self.assertEqual(popped.folder_history, ())
        self.assertEqual(popped.state_version, 3)

    def test_pop_at_home_is_a_safe_no_op(self) -> None:
        key = _key()
        created = self.service.get_or_create_session(key)

        popped = self.service.pop_folder(key)

        self.assertIs(popped, created)
        self.assertEqual(popped.state_version, 1)

    def test_navigation_rejects_folder_already_in_active_path(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)
        self.service.push_folder(key, FolderLocation("folder-1", "Projects"))

        with self.assertRaises(NavigationLoop):
            self.service.push_folder(key, FolderLocation("root", "Home"))
        with self.assertRaises(NavigationLoop):
            self.service.push_folder(key, FolderLocation("folder-1", "Projects"))

    def test_go_home_clears_history_and_active_results(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)
        self.service.push_folder(key, FolderLocation("folder-1", "Projects"))
        self.service.replace_active_results(
            key,
            source="folder",
            items=(_item(1, "file-1"),),
        )

        home = self.service.go_home(key)

        self.assertEqual(home.current_folder, FolderLocation.home())
        self.assertEqual(home.folder_history, ())
        self.assertIsNone(home.active_result_set)

    def test_result_replacement_creates_new_id_and_increments_result_version(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)
        first = self.service.replace_active_results(
            key,
            source="search",
            items=(_item(1, "file-1"),),
        )
        second = self.service.replace_active_results(
            key,
            source="search",
            items=(_item(1, "file-2"),),
        )

        self.assertEqual(first.active_result_set.result_set_id, "result-1")
        self.assertEqual(first.active_result_set.version, 1)
        self.assertEqual(second.active_result_set.result_set_id, "result-2")
        self.assertEqual(second.active_result_set.version, 2)
        self.assertEqual(second.state_version, 3)

    def test_delayed_selection_using_replaced_result_set_is_rejected(self) -> None:
        """Enabled migration test replacing the old skipped characterization."""
        key = _key()
        self.service.get_or_create_session(key)
        first = self.service.replace_active_results(
            key,
            source="search",
            items=(_item(1, "file-old"),),
        ).active_result_set
        self.service.replace_active_results(
            key,
            source="search",
            items=(_item(1, "file-new"),),
        )

        with self.assertRaises(StaleResultSet):
            self.service.resolve_selection(
                key,
                "1",
                result_set_id=first.result_set_id,
                result_set_version=first.version,
            )

    def test_selection_updates_last_selected_reference(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)
        with_results = self.service.replace_active_results(
            key,
            source="search",
            items=(_item(1, "file-1"), _item(2, "file-2")),
        )
        active = with_results.active_result_set

        selected = self.service.select_item(
            key,
            "second",
            result_set_id=active.result_set_id,
            result_set_version=active.version,
        )

        self.assertEqual(selected.last_selected_item.item_id, "file-2")
        self.assertEqual(selected.last_selected_item.ordinal, 2)
        self.assertEqual(selected.last_selected_item.result_set_id, "result-1")
        self.assertEqual(selected.state_version, 3)

    def test_resolve_selection_identifies_item_without_mutating_session(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)
        session = self.service.replace_active_results(
            key,
            source="folder",
            items=(_item(1, "folder-1", ItemKind.FOLDER),),
        )

        item = self.service.resolve_selection(key, "first")

        self.assertEqual(item.item_id, "folder-1")
        self.assertEqual(item.item_kind, ItemKind.FOLDER)
        self.assertEqual(self.service.get_session(key).state_version, session.state_version)

    def test_expired_active_results_reject_selection(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)
        self.service.replace_active_results(
            key,
            source="search",
            items=(_item(1, "file-1"),),
        )
        self.clock.advance(10.0)

        with self.assertRaises(ExpiredContext):
            self.service.resolve_selection(key, 1)

    def test_missing_active_results_raise_invalid_selection(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)

        with self.assertRaises(InvalidSelection):
            self.service.resolve_selection(key, 1)

    def test_preferences_increment_versions_without_changing_safety_state(self) -> None:
        key = _key()
        created = self.service.get_or_create_session(key)
        expert = self.service.set_experience_mode(key, ExperienceMode.EXPERT)
        ask = self.service.set_file_selection_behavior(
            key,
            FileSelectionBehavior.ASK,
        )

        self.assertEqual(created.state_version, 1)
        self.assertEqual(expert.state_version, 2)
        self.assertEqual(ask.state_version, 3)
        self.assertEqual(ask.experience_mode, ExperienceMode.EXPERT)
        self.assertEqual(ask.file_selection_behavior, FileSelectionBehavior.ASK)
        self.assertEqual(ask.state, DialogueState.READY)

    def test_account_and_clear_results_increment_versions_predictably(self) -> None:
        key = _key()
        self.service.get_or_create_session(key)
        account = self.service.set_account(key, "account-1")
        results = self.service.replace_active_results(
            key,
            source="search",
            items=(_item(1, "file-1"),),
        )
        cleared = self.service.clear_active_results(key)

        self.assertEqual(account.state_version, 2)
        self.assertEqual(results.state_version, 3)
        self.assertEqual(cleared.state_version, 4)
        self.assertIsNone(cleared.active_result_set)

    def test_sessions_transition_independently(self) -> None:
        first_key = _key("chat-1")
        second_key = _key("chat-2")
        self.service.get_or_create_session(first_key)
        second = self.service.get_or_create_session(second_key)

        first = self.service.push_folder(
            first_key,
            FolderLocation("folder-1", "Projects"),
        )

        self.assertEqual(first.current_folder.item_id, "folder-1")
        self.assertEqual(self.service.get_session(second_key), second)
        self.assertEqual(second.current_folder, FolderLocation.home())
        self.assertEqual(second.state_version, 1)

    def test_cancel_and_expire_are_explicit_state_transitions(self) -> None:
        cancel_key = _key("cancel")
        expire_key = _key("expire")
        self.service.get_or_create_session(cancel_key)
        self.service.get_or_create_session(expire_key)

        cancelled = self.service.cancel_session_state(cancel_key)
        expired = self.service.expire_session(expire_key)

        self.assertEqual(cancelled.state, DialogueState.CANCELLED)
        self.assertEqual(cancelled.state_version, 2)
        self.assertEqual(expired.state, DialogueState.EXPIRED)
        self.assertEqual(expired.state_version, 2)
        self.assertIsNone(self.repository.get(expire_key))


if __name__ == "__main__":
    unittest.main()
