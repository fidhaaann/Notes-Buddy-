"""Characterization tests for current text routing and mutable pending state."""

import unittest
from unittest.mock import AsyncMock, patch

from bot import handlers, nav
from bot.commands import cmd_cancel
from bot.dialogue import (
    initialize_dialogue_service,
    publish_active_view_to_dialogue,
)
from copilot import slot_filler
from nlp import normalize, router
from nlp.intents import Intent, IntentType

from tests.helpers import make_update_context


def _folder(item_id: str = "folder-1", index: str = "1") -> nav.IndexedItem:
    return nav.IndexedItem(
        id=item_id,
        name="Projects",
        mime_type=nav.FOLDER_MIME,
        is_folder=True,
        parent_index="",
        full_index=index,
        path="Home",
    )


def _file(item_id: str = "file-1", index: str = "1") -> nav.IndexedItem:
    return nav.IndexedItem(
        id=item_id,
        name="Notes.pdf",
        mime_type="application/pdf",
        is_folder=False,
        parent_index="",
        full_index=index,
        path="Home",
    )


class OrdinaryTextRoutingCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        nav._sessions.clear()

    def tearDown(self) -> None:
        nav._sessions.clear()

    async def test_plain_numeric_reply_resolves_before_copilot(self) -> None:
        """Enabled migration behavior at the real ordinary-text routing seam."""
        uid = 101
        nav.set_active_view(uid, "folder", {"1": _folder()})
        update, context = make_update_context(uid, "1")
        initialize_dialogue_service(context.application)
        publish_active_view_to_dialogue(
            update,
            context,
            authenticated=True,
        )

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch.object(
                handlers.nlp_router,
                "handle_pending_action",
                new=AsyncMock(return_value=False),
            ) as pending_action,
            patch.object(
                handlers,
                "_handle_copilot_message",
                new=AsyncMock(return_value=True),
            ) as copilot,
            patch.object(
                handlers.nlp_router,
                "handle_nlp_message",
                new=AsyncMock(return_value=True),
            ) as keyword_nlp,
            patch.object(
                handlers.nlp_router,
                "open_resolved_folder",
                new=AsyncMock(return_value=True),
            ) as open_folder,
        ):
            await handlers.handle_text_input(update, context)

        pending_action.assert_awaited_once()
        open_folder.assert_awaited_once()
        copilot.assert_not_awaited()
        keyword_nlp.assert_not_awaited()

    def test_action_phrases_have_index_extraction_helpers(self) -> None:
        self.assertEqual(normalize.extract_index("download 2"), "2")
        self.assertEqual(normalize.extract_index("open 1"), "1")

    def test_keyword_interpreter_wires_download_index_but_not_open_index(self) -> None:
        download = router.interpret_intent("download 2")
        open_folder = router.interpret_intent("open 1")

        self.assertEqual(download.intent, IntentType.DOWNLOAD)
        self.assertEqual(download.index, "2")
        self.assertEqual(open_folder.intent, IntentType.OPEN_FOLDER)
        self.assertIsNone(open_folder.index)

class PendingSlotCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_copilot_slot_is_filled_before_general_nlp(self) -> None:
        user_data: dict = {}
        slot_filler.set_pending(
            user_data,
            {
                "intent": "mkdir",
                "entities": {},
                "awaiting_slot": "folder_name",
                "entity_key": "folder_name",
            },
        )
        update, context = make_update_context(101, "Projects", user_data=user_data)

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch.object(
                handlers.nlp_router,
                "execute_intent",
                new=AsyncMock(return_value=True),
            ) as execute,
            patch.object(
                handlers.nlp_router,
                "handle_pending_action",
                new=AsyncMock(return_value=False),
            ) as pending_action,
            patch.object(
                handlers,
                "_handle_copilot_message",
                new=AsyncMock(return_value=True),
            ) as copilot,
        ):
            await handlers.handle_text_input(update, context)

        pending_action.assert_not_awaited()
        copilot.assert_not_awaited()
        execute.assert_awaited_once()
        filled_intent = execute.await_args.args[2]
        self.assertEqual(filled_intent.intent, IntentType.MKDIR)
        self.assertEqual(filled_intent.target_name, "Projects")
        self.assertFalse(slot_filler.has_pending(user_data))

    def test_pending_slot_storage_is_isolated_by_telegram_user_data(self) -> None:
        user_a: dict = {}
        user_b: dict = {}
        pending = {
            "intent": "mkdir",
            "entities": {},
            "awaiting_slot": "folder_name",
            "entity_key": "folder_name",
        }
        slot_filler.set_pending(user_a, pending)

        self.assertTrue(slot_filler.has_pending(user_a))
        self.assertFalse(slot_filler.has_pending(user_b))
        self.assertEqual(slot_filler.fill_pending_slot(pending, "Projects"), {"folder_name": "Projects"})

    async def test_cancel_text_is_currently_consumed_as_copilot_slot_value(self) -> None:
        """Known gap: a pending slot is handled before cancel intent detection."""
        user_data: dict = {}
        slot_filler.set_pending(
            user_data,
            {
                "intent": "mkdir",
                "entities": {},
                "awaiting_slot": "folder_name",
                "entity_key": "folder_name",
            },
        )
        update, context = make_update_context(101, "cancel", user_data=user_data)

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch.object(
                handlers.nlp_router,
                "execute_intent",
                new=AsyncMock(return_value=True),
            ) as execute,
        ):
            await handlers.handle_text_input(update, context)

        filled_intent = execute.await_args.args[2]
        self.assertEqual(filled_intent.target_name, "cancel")
        self.assertFalse(slot_filler.has_pending(user_data))

    def test_slot_helper_copies_entities_before_filling_value(self) -> None:
        original_entities = {"existing": "kept"}
        pending = {
            "intent": "mkdir",
            "entities": original_entities,
            "entity_key": "folder_name",
        }

        filled = slot_filler.fill_pending_slot(pending, "  Projects  ")

        self.assertEqual(filled, {"existing": "kept", "folder_name": "Projects"})
        self.assertEqual(original_entities, {"existing": "kept"})


class PendingActionCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        nav._sessions.clear()

    def tearDown(self) -> None:
        nav._sessions.clear()

    async def test_missing_pending_action_returns_false_without_reply(self) -> None:
        update, context = make_update_context(101, "yes")

        handled = await router.handle_pending_action(update, context)

        self.assertFalse(handled)
        update.message.reply_text.assert_not_awaited()

    async def test_confirm_executes_current_pending_dictionary_and_clears_it(self) -> None:
        pending = {"intent": "delete", "file_id": "file-1", "name": "Notes.pdf"}
        update, context = make_update_context(
            101,
            "yes",
            user_data={"pending_action": pending},
        )

        with patch.object(
            router,
            "_execute_pending_action",
            new=AsyncMock(),
        ) as execute:
            handled = await router.handle_pending_action(update, context)

        self.assertTrue(handled)
        execute.assert_awaited_once_with(update, context, pending)
        self.assertNotIn("pending_action", context.user_data)

    async def test_cancel_response_clears_confirmation_pending_action(self) -> None:
        update, context = make_update_context(
            101,
            "cancel",
            user_data={
                "pending_action": {
                    "intent": "delete",
                    "file_id": "file-1",
                    "name": "Notes.pdf",
                }
            },
        )

        handled = await router.handle_pending_action(update, context)

        self.assertTrue(handled)
        self.assertNotIn("pending_action", context.user_data)
        update.message.reply_text.assert_awaited_once()

    async def test_cancel_is_not_checked_before_pending_rename_name(self) -> None:
        """Known gap: cancel becomes the proposed new name when awaiting_name is set."""
        update, context = make_update_context(
            101,
            "cancel",
            user_data={
                "pending_action": {
                    "intent": "rename",
                    "file_id": "file-1",
                    "name": "Notes.pdf",
                    "awaiting_name": True,
                }
            },
        )

        handled = await router.handle_pending_action(update, context)

        self.assertTrue(handled)
        self.assertEqual(context.user_data["pending_action"]["new_name"], "cancel")
        self.assertNotIn("awaiting_name", context.user_data["pending_action"])

    async def test_later_sensitive_action_overwrites_earlier_pending_dictionary(self) -> None:
        """Current pending_action has one mutable slot and no operation version."""
        uid = 101
        nav.set_active_view(uid, "search", {"1": _file()})
        update, context = make_update_context(uid, "rename 1 to Archive.pdf")

        await router._handle_sensitive(
            update,
            context,
            Intent(
                intent=IntentType.RENAME,
                confidence=0.9,
                raw_text="rename 1 to Archive.pdf",
                index="1",
            ),
        )
        first_pending = dict(context.user_data["pending_action"])

        await router._handle_sensitive(
            update,
            context,
            Intent(
                intent=IntentType.DELETE,
                confidence=0.9,
                raw_text="delete 1",
                index="1",
            ),
        )

        self.assertEqual(first_pending["intent"], "rename")
        self.assertEqual(context.user_data["pending_action"]["intent"], "delete")
        self.assertNotIn("new_name", context.user_data["pending_action"])

    @unittest.skip(
        "Current pending_action dictionaries have no timestamps or expiry check."
    )
    async def test_target_expired_confirmation_is_rejected(self) -> None:
        self.fail("Enable after typed, expiring ConfirmationRequest is implemented")


class CommandCancellationCharacterizationTests(unittest.IsolatedAsyncioTestCase):
    async def test_slash_cancel_clears_upload_state_but_leaves_other_pending_state(self) -> None:
        user_data = {
            "upload_mode": True,
            "pending_upload": {"file_id": "telegram-file"},
            "pending_action": {"intent": "delete", "file_id": "drive-file"},
            "_pending_slots": {"intent": "mkdir", "entity_key": "folder_name"},
            "awaiting_otp": True,
            "pending_stepup_action": "delete file",
        }
        update, context = make_update_context(101, "/cancel", user_data=user_data)

        await cmd_cancel(update, context)

        self.assertNotIn("upload_mode", user_data)
        self.assertNotIn("pending_upload", user_data)
        self.assertIn("pending_action", user_data)
        self.assertIn("_pending_slots", user_data)
        self.assertTrue(user_data["awaiting_otp"])
        self.assertEqual(user_data["pending_stepup_action"], "delete file")


if __name__ == "__main__":
    unittest.main()
