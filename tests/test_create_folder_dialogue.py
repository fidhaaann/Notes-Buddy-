"""Telegram integration tests for the first typed operation workflow."""

import unittest
from unittest.mock import AsyncMock, patch

from application.dialogue import (
    DialogueSessionService,
    InMemoryDialogueSessionRepository,
)
from bot import commands, handlers, nav
from bot.dialogue import (
    DIALOGUE_SERVICE_KEY,
    begin_create_folder_dialogue,
    get_dialogue_service,
    initialize_dialogue_service,
    telegram_session_identity_from_update,
)
from nlp import router
from nlp.intents import Intent, IntentType
from tests.helpers import FakeClock, SequenceIds, make_update_context


class CreateFolderDialogueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        nav._sessions.clear()

    def tearDown(self) -> None:
        nav._sessions.clear()

    def _context(self, text="create a folder", *, bot_data=None):
        update, context = make_update_context(101, text, bot_data=bot_data)
        initialize_dialogue_service(context.application, clock=FakeClock())
        return update, context

    async def test_incomplete_request_asks_for_name_without_legacy_state(self) -> None:
        update, context = self._context()

        await router._handle_mkdir(
            update,
            context,
            Intent(IntentType.MKDIR, 0.9, "create a folder"),
        )

        self.assertIn("What should I call it?", update.message.reply_text.await_args.args[0])
        prompt_keyboard = update.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual(
            prompt_keyboard.inline_keyboard[0][0].callback_data,
            "dialogue:cancel",
        )
        self.assertNotIn("pending_action", context.user_data)
        self.assertNotIn("_pending_slots", context.user_data)
        service = get_dialogue_service(context)
        identity = telegram_session_identity_from_update(update)
        self.assertIsNotNone(service.get_session(identity).slot_request)

    async def test_direct_complete_request_skips_slot_and_preserves_name(self) -> None:
        update, context = self._context("create a folder called Cancelled Projects")

        with patch(
            "drive.drive_service.create_folder_async",
            new=AsyncMock(return_value={"name": "Cancelled Projects"}),
        ) as create:
            await router._handle_mkdir(
                update,
                context,
                Intent(
                    IntentType.MKDIR,
                    0.9,
                    "create a folder called Cancelled Projects",
                    target_name="Cancelled Projects",
                ),
            )

        create.assert_awaited_once_with(
            101,
            "Cancelled Projects",
            parent_id="root",
        )

    async def test_slot_answer_creates_exact_name_once_and_never_calls_llm(self) -> None:
        bot_data: dict = {}
        start, context = self._context(bot_data=bot_data)
        await begin_create_folder_dialogue(start, context, None)
        answer, answer_context = self._context("Projects", bot_data=bot_data)

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch(
                "drive.drive_service.create_folder_async",
                new=AsyncMock(return_value={"name": "Projects"}),
            ) as create,
            patch.object(
                handlers,
                "_handle_copilot_message",
                new=AsyncMock(),
            ) as copilot,
        ):
            await handlers.handle_text_input(answer, answer_context)
            await handlers.handle_text_input(answer, answer_context)

        create.assert_awaited_once_with(101, "Projects", parent_id="root")
        copilot.assert_awaited_once()

    async def test_cancellation_and_expired_slot_create_nothing(self) -> None:
        clock = FakeClock()
        ids = SequenceIds("pending")
        repository = InMemoryDialogueSessionRepository(
            clock=clock,
            id_factory=ids,
        )
        service = DialogueSessionService(
            repository,
            clock=clock,
            id_factory=ids,
            operation_ttl_seconds=30,
            slot_ttl_seconds=1,
            confirmation_ttl_seconds=5,
        )
        bot_data = {DIALOGUE_SERVICE_KEY: service}
        start, context = make_update_context(101, "create a folder", bot_data=bot_data)
        await begin_create_folder_dialogue(start, context, None)
        clock.advance(2)
        answer, answer_context = make_update_context(
            101,
            "Projects",
            bot_data=bot_data,
        )

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch(
                "drive.drive_service.create_folder_async",
                new=AsyncMock(),
            ) as create,
        ):
            await handlers.handle_text_input(answer, answer_context)

        create.assert_not_awaited()
        self.assertIn("expired", answer.message.reply_text.await_args.args[0])

    async def test_drive_failure_is_truthful_and_marks_operation_failed(self) -> None:
        update, context = self._context()

        with (
            patch(
                "drive.drive_service.create_folder_async",
                new=AsyncMock(side_effect=RuntimeError("provider failed")),
            ),
            self.assertLogs("bot.dialogue", level="ERROR"),
        ):
            await begin_create_folder_dialogue(update, context, "Projects")

        reply = update.message.reply_text.await_args.args[0]
        self.assertIn("Could not create folder", reply)
        self.assertNotIn("Folder Created", reply)
        service = get_dialogue_service(context)
        identity = telegram_session_identity_from_update(update)
        self.assertEqual(
            service.get_session(identity).pending_operation.status.value,
            "failed",
        )

    async def test_existing_mkdir_command_uses_typed_execution(self) -> None:
        update, context = self._context("/mkdir Projects")

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch("bot.commands._check_rate_limit", return_value=False),
            patch(
                "drive.drive_service.create_folder_async",
                new=AsyncMock(return_value={"name": "Projects"}),
            ) as create,
        ):
            await commands.cmd_mkdir(update, context)

        create.assert_awaited_once_with(101, "Projects", parent_id="root")

    async def test_non_migrated_pending_action_still_executes(self) -> None:
        pending = {
            "intent": "delete",
            "file_id": "abcdefghij",
            "name": "Old.txt",
        }
        update, context = self._context("yes")
        context.user_data["pending_action"] = pending

        with patch.object(
            router,
            "_execute_pending_action",
            new=AsyncMock(),
        ) as execute:
            handled = await router.handle_pending_action(update, context)

        self.assertTrue(handled)
        execute.assert_awaited_once_with(update, context, pending)


if __name__ == "__main__":
    unittest.main()
