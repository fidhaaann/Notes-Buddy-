"""Integration tests for Telegram identity, bootstrap, and view mirroring."""

import copy
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from application.dialogue.models import ItemKind
from bot import nav
from bot.dialogue import (
    DIALOGUE_SERVICE_KEY,
    initialize_dialogue_service,
    publish_active_view_to_dialogue,
    telegram_session_identity_from_update,
)
from tests.helpers import FakeClock, make_update_context


def _legacy_item(
    item_id: str,
    name: str,
    index: str,
    *,
    is_folder: bool = False,
) -> nav.IndexedItem:
    return nav.IndexedItem(
        id=item_id,
        name=name,
        mime_type=nav.FOLDER_MIME if is_folder else "application/pdf",
        is_folder=is_folder,
        parent_index="",
        full_index=index,
        path="Home",
    )


class DialogueBootstrapTests(unittest.TestCase):
    def test_one_service_is_reused_by_application_updates(self) -> None:
        application = SimpleNamespace(bot_data={})
        clock = FakeClock()

        first = initialize_dialogue_service(application, clock=clock)
        second = initialize_dialogue_service(application, clock=clock)

        self.assertIs(first, second)
        self.assertIs(application.bot_data[DIALOGUE_SERVICE_KEY], first)

    def test_missing_service_publication_fails_without_changing_legacy_view(self) -> None:
        uid = 101
        nav._sessions.clear()
        item = _legacy_item("file-1", "Notes.pdf", "1")
        nav.set_active_view(uid, "search", {"1": item})
        update, context = make_update_context(uid, "ignored")

        with self.assertLogs("bot.dialogue", level="WARNING"):
            published = publish_active_view_to_dialogue(
                update,
                context,
                authenticated=True,
            )

        self.assertIsNone(published)
        self.assertIs(nav.resolve_index(uid, "1"), item)


class ActiveViewPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        nav._sessions.clear()
        self.clock = FakeClock()
        self.bot_data: dict = {}
        self.update, self.context = make_update_context(
            101,
            "ignored",
            chat_id=501,
            bot_data=self.bot_data,
        )
        self.service = initialize_dialogue_service(
            self.context.application,
            clock=self.clock,
            result_ttl_seconds=30.0,
        )

    def tearDown(self) -> None:
        nav._sessions.clear()

    def test_browse_view_mirrors_order_kinds_account_and_folder_path(self) -> None:
        nav.push_folder(101, "semester-5", "Semester 5")
        index_map = {
            "1": _legacy_item("folder-1", "Projects", "1", is_folder=True),
            "2": _legacy_item("file-1", "Notes.pdf", "2"),
        }
        nav.set_active_view(
            101,
            "folder",
            index_map,
            metadata={"folder_id": "semester-5"},
        )

        result_ref = publish_active_view_to_dialogue(
            self.update,
            self.context,
            authenticated=True,
        )

        identity = telegram_session_identity_from_update(self.update)
        session = self.service.get_session(identity)
        active = session.active_result_set
        self.assertEqual(result_ref, (active.result_set_id, active.version))
        self.assertEqual([item.ordinal for item in active.items], [1, 2])
        self.assertEqual(
            [item.item_kind for item in active.items],
            [ItemKind.FOLDER, ItemKind.FILE],
        )
        self.assertEqual(session.current_folder.item_id, "semester-5")
        self.assertEqual(
            [folder.item_id for folder in session.folder_history],
            ["root"],
        )
        self.assertEqual(session.account_id, "telegram-principal:101")

    def test_second_publication_replaces_result_set_and_increments_version(self) -> None:
        nav.set_active_view(
            101,
            "search",
            {"1": _legacy_item("file-1", "First.pdf", "1")},
        )
        first_ref = publish_active_view_to_dialogue(
            self.update,
            self.context,
            authenticated=True,
        )
        nav.set_active_view(
            101,
            "search",
            {"1": _legacy_item("file-2", "Second.pdf", "1")},
        )

        second_ref = publish_active_view_to_dialogue(
            self.update,
            self.context,
            authenticated=True,
        )

        self.assertNotEqual(first_ref[0], second_ref[0])
        self.assertEqual(first_ref[1], 1)
        self.assertEqual(second_ref[1], 2)
        identity = telegram_session_identity_from_update(self.update)
        self.assertEqual(
            self.service.resolve_selection(identity, 1).item_id,
            "file-2",
        )

    def test_publication_does_not_mutate_legacy_navigation(self) -> None:
        index_map = {
            "1": _legacy_item("folder-1", "Projects", "1", is_folder=True),
            "2": _legacy_item("file-1", "Notes.pdf", "2"),
        }
        nav.push_folder(101, "semester-5", "Semester 5")
        nav.set_active_view(101, "folder", index_map)
        before_map = copy.deepcopy(nav.get_index_map(101))
        before_stack = nav.get_folder_stack(101)

        publish_active_view_to_dialogue(
            self.update,
            self.context,
            authenticated=True,
        )

        self.assertEqual(nav.get_index_map(101), before_map)
        self.assertEqual(nav.get_folder_stack(101), before_stack)

    def test_mirror_failure_does_not_crash_or_replace_legacy_view(self) -> None:
        item = _legacy_item("file-1", "Notes.pdf", "1")
        nav.set_active_view(101, "search", {"1": item})

        with (
            patch.object(
                self.service,
                "replace_active_results",
                side_effect=RuntimeError("synthetic mirror failure"),
            ),
            self.assertLogs("bot.dialogue", level="WARNING"),
        ):
            result = publish_active_view_to_dialogue(
                self.update,
                self.context,
                authenticated=True,
            )

        self.assertIsNone(result)
        self.assertIs(nav.resolve_index(101, "1"), item)
        identity = telegram_session_identity_from_update(self.update)
        self.assertIsNone(self.service.get_session(identity).active_result_set)

    def test_same_principal_in_two_chats_gets_isolated_typed_sessions(self) -> None:
        nav.set_active_view(
            101,
            "search",
            {"1": _legacy_item("chat-a-file", "A.pdf", "1")},
        )
        publish_active_view_to_dialogue(
            self.update,
            self.context,
            authenticated=True,
        )
        update_b, context_b = make_update_context(
            101,
            "ignored",
            chat_id=502,
            bot_data=self.bot_data,
        )
        nav.set_active_view(
            101,
            "search",
            {"1": _legacy_item("chat-b-file", "B.pdf", "1")},
        )
        publish_active_view_to_dialogue(
            update_b,
            context_b,
            authenticated=True,
        )

        identity_a = telegram_session_identity_from_update(self.update)
        identity_b = telegram_session_identity_from_update(update_b)
        self.assertNotEqual(identity_a, identity_b)
        self.assertEqual(
            self.service.resolve_selection(identity_a, 1).item_id,
            "chat-a-file",
        )
        self.assertEqual(
            self.service.resolve_selection(identity_b, 1).item_id,
            "chat-b-file",
        )


if __name__ == "__main__":
    unittest.main()
