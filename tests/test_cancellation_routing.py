"""Deterministic cancellation precedence for typed pending dialogue work."""

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from application.dialogue import PendingOperationStatus
from bot import callbacks, handlers, ui
from bot.dialogue import (
    begin_create_folder_dialogue,
    get_dialogue_service,
    initialize_dialogue_service,
    is_exact_cancellation,
    telegram_session_identity_from_update,
)
from tests.helpers import FakeClock, make_update_context


class CancellationRoutingTests(unittest.IsolatedAsyncioTestCase):
    def _context(
        self,
        uid=101,
        text="create a folder",
        chat_id=None,
        thread_id=None,
        bot_data=None,
    ):
        update, context = make_update_context(
            uid,
            text,
            chat_id=chat_id,
            thread_id=thread_id,
            bot_data=bot_data,
        )
        initialize_dialogue_service(context.application, clock=FakeClock())
        return update, context

    def _callback(self, *, uid=101, chat_id=None, thread_id=None, bot_data=None):
        update, context = self._context(
            uid=uid,
            chat_id=chat_id,
            thread_id=thread_id,
            bot_data=bot_data,
        )
        message = SimpleNamespace(message_thread_id=thread_id)
        query = SimpleNamespace(
            data="dialogue:cancel",
            answer=AsyncMock(),
            message=message,
        )
        update.callback_query = query
        update.effective_message = message
        update.effective_chat.send_message = AsyncMock()
        return update, context, query

    def test_typed_prompt_cancel_button_uses_exact_callback_data(self) -> None:
        keyboard = ui.typed_pending_cancel_keyboard()

        self.assertEqual(len(keyboard.inline_keyboard), 1)
        button = keyboard.inline_keyboard[0][0]
        self.assertEqual(button.callback_data, "dialogue:cancel")
        self.assertEqual(button.text, "Cancel")

    def test_recognizer_is_exact_and_conservative(self) -> None:
        for text in ("cancel", "stop", "never mind", "nevermind", "forget it", "no", "abort"):
            self.assertTrue(is_exact_cancellation(text))
        for text in (
            "create a folder called Cancelled Projects",
            "find cancel culture notes",
            "rename it to Stop Motion",
        ):
            self.assertFalse(is_exact_cancellation(text))

    async def test_cancel_is_processed_before_folder_slot_and_llm(self) -> None:
        bot_data: dict = {}
        start, start_context = self._context(bot_data=bot_data)
        await begin_create_folder_dialogue(start, start_context, None)
        cancel, cancel_context = self._context(
            text="cancel",
            bot_data=bot_data,
        )

        with (
            patch.object(
                handlers,
                "_handle_copilot_message",
                new=AsyncMock(),
            ) as copilot,
            patch(
                "drive.drive_service.create_folder_async",
                new=AsyncMock(),
            ) as create,
            patch("bot.commands._is_authenticated", return_value=True),
        ):
            await handlers.handle_text_input(cancel, cancel_context)

        create.assert_not_awaited()
        copilot.assert_not_awaited()
        service = get_dialogue_service(cancel_context)
        identity = telegram_session_identity_from_update(cancel)
        self.assertEqual(
            service.get_session(identity).pending_operation.status,
            PendingOperationStatus.CANCELLED,
        )

    async def test_never_mind_cancels(self) -> None:
        bot_data: dict = {}
        start, context = self._context(bot_data=bot_data)
        await begin_create_folder_dialogue(start, context, None)
        update, context = self._context(
            text="never mind",
            bot_data=bot_data,
        )

        with patch("bot.commands._is_authenticated", return_value=True):
            await handlers.handle_text_input(update, context)

        self.assertIn("Cancelled", update.message.reply_text.await_args.args[0])

    async def test_unrelated_cancel_words_reach_existing_interpretation(self) -> None:
        update, context = self._context(text="find cancel culture notes")

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch.object(
                handlers,
                "_handle_copilot_message",
                new=AsyncMock(return_value=True),
            ) as copilot,
        ):
            await handlers.handle_text_input(update, context)

        copilot.assert_awaited_once()

    async def test_cancellation_is_isolated_by_user_and_chat(self) -> None:
        bot_data: dict = {}
        first, first_context = self._context(
            uid=101,
            chat_id=1001,
            bot_data=bot_data,
        )
        await begin_create_folder_dialogue(first, first_context, None)

        for uid, chat_id in ((202, 1001), (101, 2002)):
            other, other_context = self._context(
                uid=uid,
                text="cancel",
                chat_id=chat_id,
                bot_data=bot_data,
            )
            with (
                patch("bot.commands._is_authenticated", return_value=True),
                patch.object(
                    handlers,
                    "_handle_copilot_message",
                    new=AsyncMock(return_value=True),
                ),
            ):
                await handlers.handle_text_input(other, other_context)

        service = get_dialogue_service(first_context)
        identity = telegram_session_identity_from_update(first)
        self.assertEqual(
            service.get_session(identity).pending_operation.status,
            PendingOperationStatus.AWAITING_SLOT,
        )

    async def test_new_consequential_action_does_not_become_slot_value(self) -> None:
        bot_data: dict = {}
        first, context = self._context(bot_data=bot_data)
        await begin_create_folder_dialogue(first, context, None)
        update, context = self._context(
            text="delete 1",
            bot_data=bot_data,
        )

        with (
            patch("bot.commands._is_authenticated", return_value=True),
            patch.object(
                handlers,
                "_handle_copilot_message",
                new=AsyncMock(),
            ) as copilot,
        ):
            await handlers.handle_text_input(update, context)

        copilot.assert_not_awaited()
        self.assertIn(
            "unfinished action",
            update.message.reply_text.await_args.args[0],
        )

    async def test_callback_cancels_only_its_typed_session_without_execution(self) -> None:
        bot_data: dict = {}
        start, start_context = self._context(
            chat_id=1001,
            bot_data=bot_data,
        )
        await begin_create_folder_dialogue(start, start_context, None)
        callback_update, callback_context, query = self._callback(
            chat_id=1001,
            bot_data=bot_data,
        )
        callback_context.user_data["pending_action"] = {
            "intent": "delete",
            "file_id": "abcdefghij",
        }

        with (
            patch(
                "bot.callbacks.ds.create_folder_async",
                new=AsyncMock(),
            ) as create,
            patch.object(
                handlers,
                "_handle_copilot_message",
                new=AsyncMock(),
            ) as copilot,
        ):
            await callbacks.handle_callback(callback_update, callback_context)

        create.assert_not_awaited()
        copilot.assert_not_awaited()
        query.answer.assert_awaited_once()
        callback_update.effective_chat.send_message.assert_awaited_once_with(
            text="Cancelled. No changes were made."
        )
        self.assertIn("pending_action", callback_context.user_data)
        service = get_dialogue_service(callback_context)
        identity = telegram_session_identity_from_update(callback_update)
        self.assertEqual(
            service.get_session(identity).pending_operation.status,
            PendingOperationStatus.CANCELLED,
        )

    async def test_callback_no_active_work_and_duplicate_are_safe(self) -> None:
        bot_data: dict = {}
        start, start_context = self._context(bot_data=bot_data)
        await begin_create_folder_dialogue(start, start_context, None)
        callback_update, callback_context, _ = self._callback(bot_data=bot_data)

        with patch(
            "bot.callbacks.ds.create_folder_async",
            new=AsyncMock(),
        ) as create:
            await callbacks.handle_callback(callback_update, callback_context)
            await callbacks.handle_callback(callback_update, callback_context)

        create.assert_not_awaited()
        calls = callback_update.effective_chat.send_message.await_args_list
        self.assertEqual(calls[0].kwargs["text"], "Cancelled. No changes were made.")
        self.assertEqual(
            calls[1].kwargs["text"],
            "There is no active action to cancel.",
        )

    async def test_callback_cancellation_does_not_cross_chat_boundary(self) -> None:
        bot_data: dict = {}
        first, first_context = self._context(
            chat_id=1001,
            bot_data=bot_data,
        )
        await begin_create_folder_dialogue(first, first_context, None)
        other, other_context, _ = self._callback(
            chat_id=2002,
            bot_data=bot_data,
        )

        await callbacks.handle_callback(other, other_context)

        self.assertEqual(
            other.effective_chat.send_message.await_args.kwargs["text"],
            "There is no active action to cancel.",
        )
        service = get_dialogue_service(first_context)
        first_identity = telegram_session_identity_from_update(first)
        self.assertEqual(
            service.get_session(first_identity).pending_operation.status,
            PendingOperationStatus.AWAITING_SLOT,
        )

    async def test_callback_cancellation_does_not_cross_thread_boundary(self) -> None:
        bot_data: dict = {}
        first, first_context = self._context(
            chat_id=1001,
            thread_id=10,
            bot_data=bot_data,
        )
        await begin_create_folder_dialogue(first, first_context, None)
        other, other_context, _ = self._callback(
            chat_id=1001,
            thread_id=20,
            bot_data=bot_data,
        )

        await callbacks.handle_callback(other, other_context)

        self.assertEqual(
            other.effective_chat.send_message.await_args.kwargs["text"],
            "There is no active action to cancel.",
        )
        service = get_dialogue_service(first_context)
        first_identity = telegram_session_identity_from_update(first)
        self.assertEqual(
            service.get_session(first_identity).pending_operation.status,
            PendingOperationStatus.AWAITING_SLOT,
        )


if __name__ == "__main__":
    unittest.main()
