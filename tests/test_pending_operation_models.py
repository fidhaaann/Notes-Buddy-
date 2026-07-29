"""Model invariants for immutable typed pending dialogue work."""

import unittest
from dataclasses import FrozenInstanceError

from application.dialogue import (
    ConfirmationRequest,
    CreateFolderParameters,
    OperationRiskLevel,
    OperationType,
    PendingOperation,
    PendingOperationStatus,
    SlotRequest,
)


class PendingOperationModelTests(unittest.TestCase):
    def _operation(self) -> PendingOperation:
        return PendingOperation(
            operation_id="operation-1",
            operation_type=OperationType.CREATE_FOLDER,
            principal_id="101",
            account_id="account-101",
            session_id="session-1",
            source_result_set_id=None,
            source_result_set_version=None,
            targets=(),
            parameters=CreateFolderParameters(
                folder_name=None,
                parent_folder_id="root",
                parent_folder_name_snapshot="Home",
            ),
            risk_level=OperationRiskLevel.LOW,
            status=PendingOperationStatus.DRAFT,
            idempotency_key="session-1:operation-1",
            created_at=10.0,
            expires_at=20.0,
        )

    def test_pending_operation_and_parameters_are_immutable(self) -> None:
        operation = self._operation()

        with self.assertRaises(FrozenInstanceError):
            operation.status = PendingOperationStatus.READY
        with self.assertRaises(FrozenInstanceError):
            operation.parameters.folder_name = "Projects"

    def test_operation_identity_and_idempotency_key_are_explicit(self) -> None:
        operation = self._operation()

        self.assertEqual(operation.operation_id, "operation-1")
        self.assertEqual(
            operation.idempotency_key,
            "session-1:operation-1",
        )
        self.assertIsInstance(operation.targets, tuple)

    def test_slot_and_confirmation_records_are_immutable(self) -> None:
        slot = SlotRequest(
            slot_request_id="slot-1",
            operation_id="operation-1",
            slot_name="folder_name",
            expected_type="string",
            prompt_key="create_folder.folder_name",
            attempts=0,
            created_at=10.0,
            expires_at=15.0,
        )
        confirmation = ConfirmationRequest(
            confirmation_id="confirmation-1",
            operation_id="operation-1",
            principal_id="101",
            session_id="session-1",
            operation_summary="Create Projects",
            target_snapshots=(),
            consequence="A folder will be created.",
            reversible=True,
            created_at=10.0,
            expires_at=15.0,
        )

        with self.assertRaises(FrozenInstanceError):
            slot.attempts = 1
        with self.assertRaises(FrozenInstanceError):
            confirmation.reversible = False


if __name__ == "__main__":
    unittest.main()
