"""Characterization tests for the current bot.nav session and active view."""

import unittest
from unittest.mock import patch

from bot import nav
from copilot import dialogue


def _item(
    item_id: str,
    name: str,
    *,
    index: str,
    mime_type: str = "application/pdf",
    is_folder: bool = False,
    is_shortcut: bool = False,
    shortcut_target_id: str | None = None,
) -> nav.IndexedItem:
    return nav.IndexedItem(
        id=item_id,
        name=name,
        mime_type=mime_type,
        is_folder=is_folder,
        parent_index="",
        full_index=index,
        is_shortcut=is_shortcut,
        shortcut_target_id=shortcut_target_id,
        path="Home",
    )


class NavigationIsolationCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        nav._sessions.clear()

    def tearDown(self) -> None:
        nav._sessions.clear()

    def test_folder_stacks_are_isolated_by_telegram_user_id(self) -> None:
        nav.push_folder(101, "folder-a", "User A")
        nav.push_folder(202, "folder-b", "User B")

        self.assertEqual(nav.current_folder_id(101), "folder-a")
        self.assertEqual(nav.current_folder_id(202), "folder-b")
        self.assertEqual(nav.breadcrumb(101), "Home > User A")
        self.assertEqual(nav.breadcrumb(202), "Home > User B")

    def test_active_view_indices_cannot_cross_user_boundaries(self) -> None:
        item_a = _item("file-a", "A.pdf", index="1")
        nav.set_active_view(101, "search", {"1": item_a})

        self.assertIs(nav.resolve_index(101, "1"), item_a)
        self.assertIsNone(nav.resolve_index(202, "1"))

    def test_push_and_pop_change_only_the_addressed_user(self) -> None:
        nav.push_folder(101, "a-1", "A1")
        nav.push_folder(101, "a-2", "A2")
        nav.push_folder(202, "b-1", "B1")

        self.assertTrue(nav.pop_folder(101))
        self.assertEqual(nav.current_folder_id(101), "a-1")
        self.assertEqual(nav.current_folder_id(202), "b-1")

    def test_go_home_resets_only_that_users_folder_stack(self) -> None:
        nav.push_folder(101, "a-1", "A1")
        nav.push_folder(202, "b-1", "B1")

        nav.go_home(101)

        self.assertEqual(nav.breadcrumb(101), "Home")
        self.assertEqual(nav.breadcrumb(202), "Home > B1")

    def test_go_home_currently_preserves_the_users_active_view(self) -> None:
        item = _item("file-a", "A.pdf", index="1")
        nav.push_folder(101, "a-1", "A1")
        nav.set_active_view(101, "folder", {"1": item})

        nav.go_home(101)

        self.assertIs(nav.resolve_index(101, "1"), item)


class ActiveViewCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        nav._sessions.clear()

    def tearDown(self) -> None:
        nav._sessions.clear()

    def test_flat_view_builds_folder_then_file_indices_and_preserves_metadata(self) -> None:
        folders = [
            {
                "id": "folder-1",
                "name": "Projects",
                "mimeType": nav.FOLDER_MIME,
            }
        ]
        files = [
            {
                "id": "file-1",
                "name": "DBMS.pdf",
                "mimeType": "application/pdf",
            }
        ]

        index_map = nav.build_flat_index_map(101, folders, files)
        nav.set_active_view(101, "folder", index_map, {"folder_id": "root"})

        self.assertEqual(set(index_map), {"1", "2"})
        self.assertTrue(index_map["1"].is_folder)
        self.assertEqual(index_map["1"].mime_type, nav.FOLDER_MIME)
        self.assertFalse(index_map["2"].is_folder)
        self.assertEqual(index_map["2"].mime_type, "application/pdf")
        self.assertEqual(nav.get_active_view_metadata(101, "folder_id"), "root")

    def test_flat_view_preserves_shortcut_metadata(self) -> None:
        folders = [
            {
                "id": "shortcut-1",
                "name": "Shared Project",
                "mimeType": "application/vnd.google-apps.shortcut",
                "isShortcut": True,
                "shortcutTargetId": "folder-target",
                "shortcutTargetMimeType": nav.FOLDER_MIME,
            }
        ]

        shortcut = nav.build_flat_index_map(101, folders, {})["1"]

        self.assertTrue(shortcut.is_folder)
        self.assertTrue(shortcut.is_shortcut)
        self.assertEqual(shortcut.shortcut_target_id, "folder-target")
        self.assertEqual(shortcut.shortcut_target_mime_type, nav.FOLDER_MIME)

    def test_flat_view_builder_does_not_generate_dotted_indices(self) -> None:
        index_map = nav.build_flat_index_map(
            101,
            [{"id": "folder-1", "name": "Folder"}],
            [{"id": "file-1", "name": "File"}],
        )

        self.assertEqual(list(index_map), ["1", "2"])
        self.assertFalse(any("." in index for index in index_map))

    def test_exact_valid_index_resolves_and_invalid_index_returns_none(self) -> None:
        item = _item("file-1", "DBMS.pdf", index="1")
        nav.set_active_view(101, "search", {"1": item})

        self.assertIs(nav.resolve_index(101, "1"), item)
        self.assertIsNone(nav.resolve_index(101, "01"))
        self.assertIsNone(nav.resolve_index(101, "2"))
        self.assertIsNone(nav.resolve_index(101, "1.1"))

    def test_second_view_replaces_the_first_mapping(self) -> None:
        first = _item("file-old", "Old.pdf", index="1")
        second = _item("file-new", "New.pdf", index="1")
        nav.set_active_view(101, "search", {"1": first}, {"keyword": "old"})

        nav.set_active_view(101, "search", {"1": second}, {"keyword": "new"})

        self.assertIs(nav.resolve_index(101, "1"), second)
        self.assertNotIn("file-old", [item.id for item in nav.get_index_map(101).values()])
        self.assertEqual(nav.get_active_view_metadata(101, "keyword"), "new")

    def test_current_unversioned_view_reuses_an_old_visible_index_for_new_item(self) -> None:
        """Known migration hazard: delayed "1" can silently target a new view."""
        nav.set_active_view(
            101,
            "search",
            {"1": _item("file-old", "Old.pdf", index="1")},
        )
        nav.set_active_view(
            101,
            "search",
            {"1": _item("file-unrelated", "Unrelated.pdf", index="1")},
        )

        self.assertEqual(nav.resolve_index(101, "1").id, "file-unrelated")

    @unittest.skip(
        "Target behavior requires result-set IDs/versions; current bot.nav stores only one "
        "unversioned active mapping."
    )
    def test_target_delayed_index_from_replaced_view_is_rejected(self) -> None:
        self.fail("Enable after versioned ActiveResultSet is implemented")

    def test_ordinal_helpers_resolve_first_second_and_last(self) -> None:
        first = _item("file-1", "First.pdf", index="1")
        second = _item("file-2", "Second.pdf", index="2")
        nav.set_active_view(101, "search", {"1": first, "2": second})

        self.assertIs(nav.resolve_smart(101, "first"), first)
        self.assertIs(nav.resolve_smart(101, "second"), second)
        self.assertIs(nav.resolve_smart(101, "last"), second)

    def test_pure_selection_helper_resolves_numbers_and_ordinals_only(self) -> None:
        first = _item("folder-1", "Projects", index="1", is_folder=True)
        second = _item("file-2", "Notes.pdf", index="2")
        nav.set_active_view(101, "folder", {"1": first, "2": second})

        self.assertIs(dialogue.resolve_selection(101, "1"), first)
        self.assertIs(dialogue.resolve_selection(101, "second"), second)
        self.assertIsNone(dialogue.resolve_selection(101, "download 2"))
        self.assertIsNone(dialogue.resolve_selection(101, "open 1"))

    def test_current_default_action_opens_folders_and_downloads_files(self) -> None:
        folder = _item("folder-1", "Projects", index="1", is_folder=True)
        file_item = _item("file-2", "Notes.pdf", index="2")

        self.assertEqual(dialogue.get_default_action(folder), "open")
        self.assertEqual(dialogue.get_default_action(file_item), "download")

    @unittest.skip(
        "Target file-selection behavior is configurable; current helper hard-codes download."
    )
    def test_target_file_default_is_selected_from_user_preference(self) -> None:
        self.fail("Enable after file-selection preference is introduced")


class NavigationExpiryCharacterizationTests(unittest.TestCase):
    def setUp(self) -> None:
        nav._sessions.clear()

    def tearDown(self) -> None:
        nav._sessions.clear()

    def test_unexpired_view_remains_available(self) -> None:
        item = _item("file-1", "Notes.pdf", index="1")
        nav.set_active_view(101, "search", {"1": item})
        nav.get_active_view(101).created_at = 100.0

        with patch("bot.nav.time.monotonic", return_value=100.0 + nav.VIEW_TTL_SECONDS):
            self.assertFalse(nav.is_view_expired(101))
            self.assertIs(nav.resolve_index(101, "1"), item)

    def test_expired_view_is_cleared_for_only_that_user(self) -> None:
        nav.set_active_view(101, "search", {"1": _item("old", "Old.pdf", index="1")})
        nav.set_active_view(202, "search", {"1": _item("fresh", "Fresh.pdf", index="1")})
        nav.get_active_view(101).created_at = 100.0
        nav.get_active_view(202).created_at = 100.0 + nav.VIEW_TTL_SECONDS

        with patch(
            "bot.nav.time.monotonic",
            return_value=100.0 + nav.VIEW_TTL_SECONDS + 1.0,
        ):
            self.assertTrue(nav.clear_expired_view(101))
            self.assertFalse(nav.clear_expired_view(202))

        self.assertIsNone(nav.get_active_view(101))
        self.assertEqual(nav.resolve_index(202, "1").id, "fresh")

    def test_expired_navigation_session_is_removed_without_removing_fresh_user(self) -> None:
        nav.current_folder_id(101)
        nav.current_folder_id(202)
        nav._sessions[101].last_access = 100.0
        nav._sessions[202].last_access = 100.0 + nav._SESSION_TTL

        with patch(
            "bot.nav.time.monotonic",
            return_value=100.0 + nav._SESSION_TTL + 1.0,
        ):
            self.assertEqual(nav.cleanup_expired_sessions(), 1)

        self.assertNotIn(101, nav._sessions)
        self.assertIn(202, nav._sessions)


if __name__ == "__main__":
    unittest.main()
