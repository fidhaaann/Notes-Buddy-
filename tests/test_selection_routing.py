"""Tests for deterministic pure-selection routing before Copilot and NLP."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from application.dialogue.errors import StaleResultSet
from application.dialogue.models import FileSelectionBehavior
from bot import handlers, nav
from bot.dialogue import (
    initialize_dialogue_service,
    publish_active_view_to_dialogue,
    telegram_session_identity_from_update,
)
from nlp import router
from tests.helpers import FakeClock, make_update_context


def _item(
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


class SelectionRoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        nav._sessions.clear()
        self.clock = FakeClock()
        self.bot_data: dict = {}

    def tearDown(self) -> None:
        nav._sessions.clear()

    def _published_context(
        self,
        text: str,
        index_map: dict[str, nav.IndexedItem],
        *,
        chat_id: int = 501,
        result_ttl: float = 30.0,
    ):
        update, context = make_update_context(
            101,
            text,
            chat_id=chat_id,
            bot_data=self.bot_data,
        )
        service = initialize_dialogue_service(
            context.application,
            clock=self.clock,
            result_ttl_seconds=result_ttl,
        )
        nav.set_active_view(101, "folder", index_map)
        publish_active_view_to_dialogue(update, context, authenticated=True)
        return update, context, service

    async def _run_handler(self, update, context):
        pending = AsyncMock(return_value=False)
        copilot = AsyncMock(return_value=True)
        keyword = AsyncMock(return_value=True)
        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch.object(
                handlers.nlp_router,
                "handle_pending_action",
                new=pending,
            ),
            patch.object(handlers, "_handle_copilot_message", new=copilot),
            patch.object(
                handlers.nlp_router,
                "handle_nlp_message",
                new=keyword,
            ),
        ):
            await handlers.handle_text_input(update, context)
        return pending, copilot, keyword

    async def test_numeric_ordinal_and_last_folder_selections_precede_copilot(self) -> None:
        cases = (
            ("1", "folder-1"),
            ("first", "folder-1"),
            ("last one", "folder-2"),
        )
        for offset, (text, expected_id) in enumerate(cases):
            with self.subTest(text=text):
                nav._sessions.clear()
                update, context, service = self._published_context(
                    text,
                    {
                        "1": _item("folder-1", "First", "1", is_folder=True),
                        "2": _item("folder-2", "Second", "2", is_folder=True),
                    },
                    chat_id=501 + offset,
                )
                with patch.object(
                    handlers.nlp_router,
                    "open_resolved_folder",
                    new=AsyncMock(return_value=True),
                ) as open_folder:
                    _, copilot, keyword = await self._run_handler(update, context)

                selected_item = open_folder.await_args.args[2]
                self.assertEqual(selected_item.id, expected_id)
                copilot.assert_not_awaited()
                keyword.assert_not_awaited()
                identity = telegram_session_identity_from_update(update)
                self.assertEqual(
                    service.get_session(identity).last_selected_item.item_id,
                    expected_id,
                )

    async def test_fresh_query_with_number_is_not_intercepted(self) -> None:
        update, context, _ = self._published_context(
            "find module 1 notes",
            {"1": _item("file-1", "Notes.pdf", "1")},
        )

        _, copilot, _ = await self._run_handler(update, context)

        copilot.assert_awaited_once_with(update, context, "find module 1 notes")

    async def test_unrelated_text_still_reaches_existing_interpretation(self) -> None:
        update, context, _ = self._published_context(
            "hello there",
            {"1": _item("file-1", "Notes.pdf", "1")},
        )

        _, copilot, _ = await self._run_handler(update, context)

        copilot.assert_awaited_once_with(update, context, "hello there")

    async def test_folder_selection_calls_open_and_never_download(self) -> None:
        update, context, _ = self._published_context(
            "1",
            {"1": _item("folder-1", "Projects", "1", is_folder=True)},
        )
        with (
            patch.object(
                handlers.nlp_router,
                "open_resolved_folder",
                new=AsyncMock(return_value=True),
            ) as open_folder,
            patch.object(
                handlers.nlp_router,
                "download_resolved_item",
                new=AsyncMock(),
            ) as download,
        ):
            await self._run_handler(update, context)

        open_folder.assert_awaited_once()
        download.assert_not_awaited()

    async def test_default_show_details_routes_to_existing_details_path(self) -> None:
        update, context, _ = self._published_context(
            "1",
            {"1": _item("file-1", "Notes.pdf", "1")},
        )
        with (
            patch.object(
                handlers.nlp_router,
                "show_resolved_item_details",
                new=AsyncMock(),
            ) as details,
            patch.object(
                handlers.nlp_router,
                "download_resolved_item",
                new=AsyncMock(),
            ) as download,
        ):
            await self._run_handler(update, context)

        details.assert_awaited_once()
        download.assert_not_awaited()

    async def test_download_preference_routes_to_existing_download_path(self) -> None:
        update, context, service = self._published_context(
            "1",
            {"1": _item("file-1", "Notes.pdf", "1")},
        )
        identity = telegram_session_identity_from_update(update)
        service.set_file_selection_behavior(
            identity,
            FileSelectionBehavior.DOWNLOAD,
        )
        with (
            patch.object(
                handlers.nlp_router,
                "show_resolved_item_details",
                new=AsyncMock(),
            ) as details,
            patch.object(
                handlers.nlp_router,
                "download_resolved_item",
                new=AsyncMock(),
            ) as download,
        ):
            await self._run_handler(update, context)

        download.assert_awaited_once()
        details.assert_not_awaited()

    async def test_ask_preference_renders_choices_without_execution(self) -> None:
        update, context, service = self._published_context(
            "1",
            {"1": _item("file-1", "Notes.pdf", "1")},
        )
        identity = telegram_session_identity_from_update(update)
        service.set_file_selection_behavior(identity, FileSelectionBehavior.ASK)
        with (
            patch.object(
                handlers.nlp_router,
                "show_resolved_item_details",
                new=AsyncMock(),
            ) as details,
            patch.object(
                handlers.nlp_router,
                "download_resolved_item",
                new=AsyncMock(),
            ) as download,
        ):
            await self._run_handler(update, context)

        details.assert_not_awaited()
        download.assert_not_awaited()
        reply = update.message.reply_text.await_args
        self.assertIn("Choose an action", reply.args[0])
        self.assertIn("reply_markup", reply.kwargs)

    async def test_invalid_number_returns_guidance_without_copilot(self) -> None:
        update, context, _ = self._published_context(
            "9",
            {"1": _item("file-1", "Notes.pdf", "1")},
        )

        _, copilot, keyword = await self._run_handler(update, context)

        copilot.assert_not_awaited()
        keyword.assert_not_awaited()
        self.assertIn(
            "not in the current list",
            update.message.reply_text.await_args.args[0],
        )

    async def test_expired_results_return_guidance_without_copilot(self) -> None:
        update, context, _ = self._published_context(
            "1",
            {"1": _item("file-1", "Notes.pdf", "1")},
            result_ttl=5.0,
        )
        self.clock.advance(5.0)

        _, copilot, keyword = await self._run_handler(update, context)

        copilot.assert_not_awaited()
        keyword.assert_not_awaited()
        self.assertIn("expired", update.message.reply_text.await_args.args[0])

    async def test_stale_result_error_never_executes_replacement_or_copilot(self) -> None:
        update, context, service = self._published_context(
            "1",
            {"1": _item("file-old", "Old.pdf", "1")},
        )
        with (
            patch.object(
                service,
                "resolve_selection",
                side_effect=StaleResultSet("synthetic stale correlation"),
            ),
            patch.object(
                handlers.nlp_router,
                "show_resolved_item_details",
                new=AsyncMock(),
            ) as details,
        ):
            _, copilot, keyword = await self._run_handler(update, context)

        details.assert_not_awaited()
        copilot.assert_not_awaited()
        keyword.assert_not_awaited()
        self.assertIn("changed", update.message.reply_text.await_args.args[0])

    async def test_no_active_list_returns_help_without_copilot(self) -> None:
        update, context = make_update_context(
            101,
            "1",
            chat_id=501,
            bot_data=self.bot_data,
        )
        initialize_dialogue_service(context.application, clock=self.clock)

        _, copilot, keyword = await self._run_handler(update, context)

        copilot.assert_not_awaited()
        keyword.assert_not_awaited()
        self.assertIn("active list", update.message.reply_text.await_args.args[0])

    async def test_another_chat_cannot_select_first_chats_results(self) -> None:
        first_update, first_context, _ = self._published_context(
            "ignored",
            {"1": _item("file-1", "Notes.pdf", "1")},
            chat_id=501,
        )
        second_update, second_context = make_update_context(
            101,
            "1",
            chat_id=502,
            bot_data=self.bot_data,
        )

        _, copilot, keyword = await self._run_handler(
            second_update,
            second_context,
        )

        copilot.assert_not_awaited()
        keyword.assert_not_awaited()
        self.assertIn(
            "active list",
            second_update.message.reply_text.await_args.args[0],
        )
        self.assertIsNotNone(first_update)
        self.assertIsNotNone(first_context)


class ResolvedFolderActionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        nav._sessions.clear()
        self.bot_data: dict = {}
        self.update, self.context = make_update_context(
            101,
            "1",
            chat_id=501,
            bot_data=self.bot_data,
        )
        self.service = initialize_dialogue_service(self.context.application)

    def tearDown(self) -> None:
        nav._sessions.clear()

    async def test_opened_folder_child_listing_is_mirrored(self) -> None:
        folder = _item("folder-1", "Projects", "1", is_folder=True)
        nav.set_active_view(101, "folder", {"1": folder})
        publish_active_view_to_dialogue(
            self.update,
            self.context,
            authenticated=True,
        )
        listing = SimpleNamespace(
            folders=[
                {
                    "id": "child-folder",
                    "name": "Child",
                    "mimeType": nav.FOLDER_MIME,
                }
            ],
            files=[],
        )

        with patch.object(
            router.ds,
            "list_directory_async",
            new=AsyncMock(return_value=listing),
        ):
            opened = await router.open_resolved_folder(
                self.update,
                self.context,
                folder,
            )

        self.assertTrue(opened)
        identity = telegram_session_identity_from_update(self.update)
        session = self.service.get_session(identity)
        self.assertEqual(session.current_folder.item_id, "folder-1")
        self.assertEqual(session.active_result_set.items[0].item_id, "child-folder")

    async def test_folder_failure_restores_legacy_path_and_replies_safely(self) -> None:
        folder = _item("folder-1", "Projects", "1", is_folder=True)
        before = nav.get_folder_stack(101)

        with (
            patch.object(
                router.ds,
                "list_directory_async",
                new=AsyncMock(side_effect=RuntimeError("synthetic drive failure")),
            ),
            self.assertLogs("nlp.router", level="ERROR"),
        ):
            opened = await router.open_resolved_folder(
                self.update,
                self.context,
                folder,
            )

        self.assertFalse(opened)
        self.assertEqual(nav.get_folder_stack(101), before)
        self.assertIn(
            "Could not open",
            self.update.message.reply_text.await_args.args[0],
        )


if __name__ == "__main__":
    unittest.main()
