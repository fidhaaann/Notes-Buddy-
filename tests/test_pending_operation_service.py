"""Transition, expiry, confirmation, and replay tests."""

import unittest

from application.dialogue import (
    ClientSessionIdentity,
    ConfirmationAlreadyConsumed,
    ConfirmationExpired,
    ConfirmationMismatch,
    CreateFolderParameters,
    DialogueSessionService,
    InMemoryDialogueSessionRepository,
    ItemKind,
    OperationAlreadyConsumed,
    OperationTargetSnapshot,
    OperationType,
    PendingOperationExists,
    PendingOperationStatus,
    SlotExpired,
    ResultItem,
    VersionConflict,
)
from tests.helpers import FakeClock, SequenceIds


class PendingOperationServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        ids = SequenceIds("id")
        repository = InMemoryDialogueSessionRepository(
            clock=self.clock,
            id_factory=ids,
        )
        self.service = DialogueSessionService(
            repository,
            clock=self.clock,
            id_factory=ids,
            operation_ttl_seconds=30,
            slot_ttl_seconds=10,
            confirmation_ttl_seconds=5,
        )
        self.key = ClientSessionIdentity("101", "telegram", "chat-1")
        session = self.service.get_or_create_session(self.key)
        self.service.set_account(self.key, "account-101")

    def _begin(self, name: str | None = None):
        session = self.service.get_session(self.key)
        return self.service.begin_operation(
            self.key,
            operation_type=OperationType.CREATE_FOLDER,
            parameters=CreateFolderParameters(name, "root", "Home"),
            expected_version=session.state_version,
        )

    def _request_slot(self):
        session = self._begin()
        operation = session.pending_operation
        assert operation is not None
        return self.service.request_slot(
            self.key,
            operation_id=operation.operation_id,
            slot_name="folder_name",
            expected_type="string",
            prompt_key="create_folder.folder_name",
            expected_version=session.state_version,
        )

    def test_slot_fill_updates_parameters_and_versions_predictably(self) -> None:
        requested = self._request_slot()
        operation = requested.pending_operation
        slot = requested.slot_request
        assert operation is not None and slot is not None

        filled = self.service.fill_slot(
            self.key,
            operation_id=operation.operation_id,
            slot_request_id=slot.slot_request_id,
            value="Projects",
            expected_version=requested.state_version,
        )

        self.assertEqual(filled.state_version, requested.state_version + 1)
        self.assertEqual(
            filled.pending_operation.parameters.folder_name,
            "Projects",
        )
        self.assertEqual(
            filled.pending_operation.status,
            PendingOperationStatus.READY,
        )
        self.assertIsNone(filled.slot_request)

    def test_slot_expiry_transitions_operation_and_cannot_execute(self) -> None:
        requested = self._request_slot()
        operation = requested.pending_operation
        slot = requested.slot_request
        assert operation is not None and slot is not None
        self.clock.advance(11)

        with self.assertRaises(SlotExpired):
            self.service.fill_slot(
                self.key,
                operation_id=operation.operation_id,
                slot_request_id=slot.slot_request_id,
                value="Projects",
            )

        expired = self.service.get_session(self.key)
        self.assertEqual(
            expired.pending_operation.status,
            PendingOperationStatus.EXPIRED,
        )
        self.assertIsNone(expired.slot_request)

    def test_cancellation_clears_slot_and_is_session_isolated(self) -> None:
        requested = self._request_slot()
        other = ClientSessionIdentity("202", "telegram", "chat-2")
        self.service.get_or_create_session(other)
        self.service.set_account(other, "account-202")

        cancelled = self.service.cancel_pending(
            self.key,
            expected_version=requested.state_version,
        )

        self.assertEqual(
            cancelled.pending_operation.status,
            PendingOperationStatus.CANCELLED,
        )
        self.assertIsNone(cancelled.slot_request)
        self.assertIsNone(self.service.get_session(other).pending_operation)

    def test_second_operation_cannot_overwrite_first(self) -> None:
        first = self._begin()

        with self.assertRaises(PendingOperationExists):
            self.service.begin_operation(
                self.key,
                operation_type=OperationType.CREATE_FOLDER,
                parameters=CreateFolderParameters("Other", "root", "Home"),
                expected_version=first.state_version,
            )

        self.assertEqual(
            self.service.get_session(self.key).pending_operation.operation_id,
            first.pending_operation.operation_id,
        )

    def test_confirmation_is_bound_expires_and_is_single_use(self) -> None:
        ready = self._begin("Projects")
        operation = ready.pending_operation
        assert operation is not None
        waiting = self.service.create_confirmation(
            self.key,
            operation_id=operation.operation_id,
            operation_summary="Create Projects",
            consequence="Creates one folder.",
            reversible=True,
            expected_version=ready.state_version,
        )
        confirmation = waiting.confirmation
        assert confirmation is not None

        with self.assertRaises(ConfirmationMismatch):
            self.service.confirm_operation(
                self.key,
                operation_id=operation.operation_id,
                confirmation_id="wrong-confirmation",
            )

        confirmed = self.service.confirm_operation(
            self.key,
            operation_id=operation.operation_id,
            confirmation_id=confirmation.confirmation_id,
            expected_version=waiting.state_version,
        )
        with self.assertRaises(ConfirmationAlreadyConsumed):
            self.service.confirm_operation(
                self.key,
                operation_id=operation.operation_id,
                confirmation_id=confirmation.confirmation_id,
                expected_version=confirmed.state_version,
            )

    def test_confirmation_expiry_is_enabled(self) -> None:
        ready = self._begin("Projects")
        operation = ready.pending_operation
        assert operation is not None
        waiting = self.service.create_confirmation(
            self.key,
            operation_id=operation.operation_id,
            operation_summary="Create Projects",
            consequence="Creates one folder.",
            reversible=True,
        )
        confirmation = waiting.confirmation
        assert confirmation is not None
        self.clock.advance(6)

        with self.assertRaises(ConfirmationExpired):
            self.service.confirm_operation(
                self.key,
                operation_id=operation.operation_id,
                confirmation_id=confirmation.confirmation_id,
            )

    def test_confirmation_target_survives_active_result_replacement(self) -> None:
        session = self.service.get_session(self.key)
        ready = self.service.begin_operation(
            self.key,
            operation_type=OperationType.CREATE_FOLDER,
            parameters=CreateFolderParameters("Projects", "parent-1", "Original"),
            targets=(
                OperationTargetSnapshot(
                    "parent-1",
                    "Original",
                    ItemKind.FOLDER,
                ),
            ),
            expected_version=session.state_version,
        )
        operation = ready.pending_operation
        assert operation is not None
        waiting = self.service.create_confirmation(
            self.key,
            operation_id=operation.operation_id,
            operation_summary="Create Projects",
            consequence="Creates one folder.",
            reversible=True,
        )
        self.service.replace_active_results(
            self.key,
            source="new-list",
            items=(
                ResultItem(
                    ordinal=1,
                    item_id="different-item",
                    account_id="account-101",
                    name_snapshot="Different",
                    item_kind=ItemKind.FILE,
                ),
            ),
        )
        confirmation = waiting.confirmation
        assert confirmation is not None

        confirmed = self.service.confirm_operation(
            self.key,
            operation_id=operation.operation_id,
            confirmation_id=confirmation.confirmation_id,
        )

        self.assertEqual(
            confirmed.pending_operation.parameters.parent_folder_id,
            "parent-1",
        )
        self.assertEqual(
            confirmed.confirmation.target_snapshots[0].item_id,
            "parent-1",
        )

    def test_operation_execution_and_completion_are_single_use(self) -> None:
        ready = self._begin("Projects")
        operation = ready.pending_operation
        assert operation is not None
        executing = self.service.consume_operation(
            self.key,
            operation_id=operation.operation_id,
            expected_version=ready.state_version,
        )
        with self.assertRaises(OperationAlreadyConsumed):
            self.service.consume_operation(
                self.key,
                operation_id=operation.operation_id,
                expected_version=executing.state_version,
            )
        completed = self.service.complete_operation(
            self.key,
            operation_id=operation.operation_id,
            expected_version=executing.state_version,
        )
        self.assertIsNone(completed.pending_operation)
        self.assertIn(operation.operation_id, completed.consumed_operation_ids)
        with self.assertRaises(OperationAlreadyConsumed):
            self.service.consume_operation(
                self.key,
                operation_id=operation.operation_id,
            )

    def test_stale_expected_version_fails(self) -> None:
        session = self.service.get_session(self.key)
        self.service.set_experience_mode(
            self.key,
            session.experience_mode,
        )

        with self.assertRaises(VersionConflict):
            self.service.begin_operation(
                self.key,
                operation_type=OperationType.CREATE_FOLDER,
                parameters=CreateFolderParameters("Projects", "root", "Home"),
                expected_version=session.state_version,
            )


if __name__ == "__main__":
    unittest.main()
