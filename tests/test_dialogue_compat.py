"""Tests for the narrow bot.nav-to-dialogue compatibility adapter."""

import copy
import unittest

from application.dialogue.compat import (
    active_view_to_result_items,
    folder_stack_to_locations,
    index_map_to_result_items,
    telegram_identity,
)
from application.dialogue.models import ItemKind
from bot import nav


def _legacy_item(
    item_id: str,
    name: str,
    index: str,
    *,
    is_folder: bool = False,
    is_shortcut: bool = False,
    shortcut_target_id: str | None = None,
    shortcut_target_mime_type: str | None = None,
) -> nav.IndexedItem:
    return nav.IndexedItem(
        id=item_id,
        name=name,
        mime_type=(
            nav.FOLDER_MIME if is_folder else "application/pdf"
        ),
        is_folder=is_folder,
        parent_index="",
        full_index=index,
        is_shortcut=is_shortcut,
        shortcut_target_id=shortcut_target_id,
        shortcut_target_mime_type=shortcut_target_mime_type,
        path="Home > Projects",
    )


class DialogueCompatibilityTests(unittest.TestCase):
    def test_telegram_scalars_convert_to_transport_neutral_identity(self) -> None:
        identity = telegram_identity(101, -202, thread_id=7)

        self.assertEqual(identity.principal_id, "101")
        self.assertEqual(identity.client_type, "telegram")
        self.assertEqual(identity.conversation_id, "-202")
        self.assertEqual(identity.thread_id, "7")

    def test_flat_visible_order_and_ordinals_are_preserved(self) -> None:
        index_map = {
            "1": _legacy_item("folder-1", "Projects", "1", is_folder=True),
            "2": _legacy_item("file-1", "Notes.pdf", "2"),
        }

        converted = index_map_to_result_items(
            index_map,
            account_id="account-1",
            source="folder",
        )

        self.assertEqual([item.ordinal for item in converted], [1, 2])
        self.assertEqual([item.item_id for item in converted], ["folder-1", "file-1"])
        self.assertEqual([item.name_snapshot for item in converted], ["Projects", "Notes.pdf"])

    def test_folder_file_and_shortcut_kinds_map_without_execution_semantics(self) -> None:
        index_map = {
            "1": _legacy_item("folder-1", "Projects", "1", is_folder=True),
            "2": _legacy_item("file-1", "Notes.pdf", "2"),
            "3": _legacy_item(
                "shortcut-1",
                "Shared",
                "3",
                is_folder=True,
                is_shortcut=True,
                shortcut_target_id="target-folder",
                shortcut_target_mime_type=nav.FOLDER_MIME,
            ),
        }

        folder, file_item, shortcut = index_map_to_result_items(
            index_map,
            account_id="account-1",
        )

        self.assertEqual(folder.item_kind, ItemKind.FOLDER)
        self.assertEqual(file_item.item_kind, ItemKind.FILE)
        self.assertEqual(shortcut.item_kind, ItemKind.SHORTCUT)
        self.assertTrue(shortcut.is_shortcut)
        self.assertEqual(shortcut.shortcut_target_id, "target-folder")
        self.assertEqual(shortcut.shortcut_target_kind, ItemKind.FOLDER)

    def test_active_view_source_is_copied(self) -> None:
        view = nav.ViewContext(
            view_type="search",
            index_map={"1": _legacy_item("file-1", "Notes.pdf", "1")},
            metadata={"keyword": "notes"},
            created_at=100.0,
        )

        converted = active_view_to_result_items(view, account_id="account-1")

        self.assertEqual(converted[0].source, "search")

    def test_current_folder_stack_converts_to_parent_linked_locations(self) -> None:
        converted = folder_stack_to_locations(
            [
                ("root", "Home"),
                ("folder-1", "Semester 5"),
                ("folder-2", "DBMS"),
            ]
        )

        self.assertEqual([location.item_id for location in converted], ["root", "folder-1", "folder-2"])
        self.assertIsNone(converted[0].parent_id)
        self.assertEqual(converted[1].parent_id, "root")
        self.assertEqual(converted[2].parent_id, "folder-1")

    def test_conversion_does_not_mutate_legacy_navigation_values(self) -> None:
        index_map = {
            "1": _legacy_item("folder-1", "Projects", "1", is_folder=True),
            "2": _legacy_item("file-1", "Notes.pdf", "2"),
        }
        before = copy.deepcopy(index_map)

        index_map_to_result_items(index_map, account_id="account-1")

        self.assertEqual(index_map, before)


if __name__ == "__main__":
    unittest.main()
