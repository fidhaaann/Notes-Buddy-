"""Unit tests for immutable dialogue foundation models."""

import unittest
from dataclasses import FrozenInstanceError

from application.dialogue.errors import (
    ExpiredContext,
    InvalidDialogueValue,
    InvalidSelection,
)
from application.dialogue.models import (
    ActiveResultSet,
    ClientSessionIdentity,
    DialogueSession,
    DialogueState,
    ExperienceMode,
    FileSelectionBehavior,
    FolderLocation,
    ItemKind,
    ResultItem,
)


def _identity(conversation: str = "chat-1") -> ClientSessionIdentity:
    return ClientSessionIdentity("user-1", "telegram", conversation)


def _item(
    ordinal: int,
    item_id: str,
    *,
    kind: ItemKind = ItemKind.FILE,
    shortcut_target_kind: ItemKind | None = None,
) -> ResultItem:
    is_shortcut = kind is ItemKind.SHORTCUT
    return ResultItem(
        ordinal=ordinal,
        item_id=item_id,
        account_id="account-1",
        name_snapshot=f"Item {ordinal}",
        item_kind=kind,
        mime_type="application/pdf",
        parent_ids=("parent-1",),
        is_shortcut=is_shortcut,
        shortcut_target_id="target-1" if is_shortcut else None,
        shortcut_target_kind=shortcut_target_kind,
        modified_at=100.0,
        size_bytes=42,
        source="legacy",
        capabilities=frozenset({"can_download"}),
    )


def _result_set(*items: ResultItem) -> ActiveResultSet:
    return ActiveResultSet(
        result_set_id="result-set-1",
        version=1,
        source="search",
        items=tuple(items),
        created_at=100.0,
        expires_at=200.0,
    )


class ClientSessionIdentityTests(unittest.TestCase):
    def test_identity_is_immutable_hashable_and_conversation_scoped(self) -> None:
        first = _identity("chat-1")
        same = _identity("chat-1")
        other_chat = _identity("chat-2")

        self.assertEqual(first, same)
        self.assertEqual(len({first, same, other_chat}), 2)
        with self.assertRaises(FrozenInstanceError):
            first.conversation_id = "changed"

    def test_identity_rejects_empty_control_and_oversized_identifiers(self) -> None:
        invalid_values = ("", "   ", "bad\nvalue", "x" * 257)
        for value in invalid_values:
            with self.subTest(value=repr(value)):
                with self.assertRaises(InvalidDialogueValue):
                    ClientSessionIdentity(value, "telegram", "chat-1")


class DialogueSessionModelTests(unittest.TestCase):
    def test_default_session_values_are_transport_neutral_and_safe(self) -> None:
        identity = _identity()
        session = DialogueSession(
            session_id="session-1",
            session_key=identity,
            principal_id="user-1",
            created_at=100.0,
            updated_at=100.0,
            expires_at=200.0,
        )

        self.assertEqual(session.state, DialogueState.READY)
        self.assertEqual(session.current_folder, FolderLocation.home())
        self.assertEqual(session.folder_history, ())
        self.assertIsNone(session.active_result_set)
        self.assertIsNone(session.last_selected_item)
        self.assertEqual(session.experience_mode, ExperienceMode.GUIDED)
        self.assertEqual(
            session.file_selection_behavior,
            FileSelectionBehavior.SHOW_DETAILS,
        )
        self.assertEqual(session.state_version, 1)

    def test_session_rejects_principal_mismatch_and_invalid_version(self) -> None:
        with self.assertRaises(InvalidDialogueValue):
            DialogueSession(
                session_id="session-1",
                session_key=_identity(),
                principal_id="another-user",
                created_at=100.0,
                updated_at=100.0,
                expires_at=200.0,
            )
        with self.assertRaises(InvalidDialogueValue):
            DialogueSession(
                session_id="session-1",
                session_key=_identity(),
                principal_id="user-1",
                created_at=100.0,
                updated_at=100.0,
                expires_at=200.0,
                state_version=0,
            )


class ResultItemModelTests(unittest.TestCase):
    def test_invalid_ordinal_and_mutable_collection_values_are_rejected(self) -> None:
        with self.assertRaises(InvalidDialogueValue):
            _item(0, "file-1")
        with self.assertRaises(InvalidDialogueValue):
            ResultItem(
                ordinal=1,
                item_id="file-1",
                account_id="account-1",
                name_snapshot="File",
                item_kind=ItemKind.FILE,
                parent_ids=["parent-1"],
            )
        with self.assertRaises(InvalidDialogueValue):
            ResultItem(
                ordinal=1,
                item_id="file-1",
                account_id="account-1",
                name_snapshot="File",
                item_kind=ItemKind.FILE,
                capabilities={"can_download"},
            )

    def test_file_folder_and_shortcut_metadata_is_preserved(self) -> None:
        file_item = _item(1, "file-1")
        folder = _item(2, "folder-1", kind=ItemKind.FOLDER)
        shortcut = _item(
            3,
            "shortcut-1",
            kind=ItemKind.SHORTCUT,
            shortcut_target_kind=ItemKind.FOLDER,
        )

        self.assertEqual(file_item.item_kind, ItemKind.FILE)
        self.assertEqual(folder.item_kind, ItemKind.FOLDER)
        self.assertTrue(shortcut.is_shortcut)
        self.assertEqual(shortcut.shortcut_target_id, "target-1")
        self.assertEqual(shortcut.shortcut_target_kind, ItemKind.FOLDER)
        self.assertEqual(shortcut.parent_ids, ("parent-1",))
        self.assertEqual(shortcut.capabilities, frozenset({"can_download"}))


class ActiveResultSetModelTests(unittest.TestCase):
    def test_invalid_version_and_duplicate_ordinals_are_rejected(self) -> None:
        with self.assertRaises(InvalidDialogueValue):
            ActiveResultSet(
                result_set_id="set-1",
                version=0,
                source="search",
                items=(),
                created_at=100.0,
                expires_at=200.0,
            )
        with self.assertRaises(InvalidDialogueValue):
            _result_set(_item(1, "file-1"), _item(1, "file-2"))

    def test_duplicate_item_ids_are_rejected_within_one_result_set(self) -> None:
        with self.assertRaises(InvalidDialogueValue):
            _result_set(_item(1, "file-1"), _item(2, "file-1"))

    def test_numeric_ordinal_and_last_selection_resolve(self) -> None:
        first = _item(1, "file-1")
        second = _item(2, "file-2")
        results = _result_set(first, second)

        self.assertIs(results.resolve(1, 150.0), first)
        self.assertIs(results.resolve("second", 150.0), second)
        self.assertIs(results.resolve("the first one", 150.0), first)
        self.assertIs(results.resolve("last", 150.0), second)
        self.assertEqual(results.item_count, 2)

    def test_invalid_selection_raises_typed_error(self) -> None:
        results = _result_set(_item(1, "file-1"))

        for selection in (0, "0", "eleventh", "2"):
            with self.subTest(selection=selection):
                with self.assertRaises(InvalidSelection):
                    results.resolve(selection, 150.0)

    def test_expired_result_set_rejects_selection(self) -> None:
        results = _result_set(_item(1, "file-1"))

        self.assertFalse(results.is_expired(199.999))
        self.assertTrue(results.is_expired(200.0))
        with self.assertRaises(ExpiredContext):
            results.resolve("first", 200.0)


if __name__ == "__main__":
    unittest.main()
