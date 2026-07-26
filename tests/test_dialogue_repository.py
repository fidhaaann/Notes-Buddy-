"""Unit tests for the in-memory dialogue repository."""

import unittest
from dataclasses import replace

from application.dialogue.errors import VersionConflict
from application.dialogue.memory_repository import (
    InMemoryDialogueSessionRepository,
)
from application.dialogue.models import ClientSessionIdentity
from tests.helpers import FakeClock, SequenceIds


def _key(
    conversation_id: str,
    *,
    principal_id: str = "user-1",
) -> ClientSessionIdentity:
    return ClientSessionIdentity(
        principal_id=principal_id,
        client_type="telegram",
        conversation_id=conversation_id,
    )


class InMemoryDialogueSessionRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.repository = InMemoryDialogueSessionRepository(
            max_sessions=3,
            session_ttl_seconds=100.0,
            clock=self.clock,
            id_factory=SequenceIds("session"),
        )

    def test_get_or_create_is_stable(self) -> None:
        key = _key("chat-1")

        first = self.repository.get_or_create(key, self.clock())
        second = self.repository.get_or_create(key, self.clock())

        self.assertIs(first, second)
        self.assertEqual(self.repository.count(), 1)

    def test_same_principal_in_different_conversations_has_separate_sessions(self) -> None:
        private = self.repository.get_or_create(_key("private"), self.clock())
        group = self.repository.get_or_create(_key("group"), self.clock())

        self.assertNotEqual(private.session_key, group.session_key)
        self.assertNotEqual(private.session_id, group.session_id)
        self.assertEqual(self.repository.count(), 2)

    def test_different_principals_cannot_overwrite_one_another(self) -> None:
        first_key = _key("chat", principal_id="user-1")
        second_key = _key("chat", principal_id="user-2")
        first = self.repository.get_or_create(first_key, self.clock())
        second = self.repository.get_or_create(second_key, self.clock())
        updated_first = replace(
            first,
            account_id="account-1",
            state_version=2,
            updated_at=self.clock(),
            expires_at=self.clock() + 100.0,
        )

        self.repository.save(updated_first, expected_version=1)

        self.assertEqual(self.repository.get(first_key).account_id, "account-1")
        self.assertIsNone(self.repository.get(second_key).account_id)
        self.assertEqual(self.repository.get(second_key), second)

    def test_save_accepts_exact_next_version_and_expected_version(self) -> None:
        key = _key("chat-1")
        current = self.repository.get_or_create(key, self.clock())
        updated = replace(
            current,
            account_id="account-1",
            state_version=2,
            updated_at=self.clock(),
            expires_at=self.clock() + 100.0,
        )

        saved = self.repository.save(updated, expected_version=1)

        self.assertEqual(saved.state_version, 2)
        self.assertEqual(self.repository.get(key).account_id, "account-1")

    def test_stale_save_raises_version_conflict(self) -> None:
        key = _key("chat-1")
        current = self.repository.get_or_create(key, self.clock())
        version_two = replace(
            current,
            account_id="account-1",
            state_version=2,
            updated_at=self.clock(),
            expires_at=self.clock() + 100.0,
        )
        self.repository.save(version_two, expected_version=1)
        stale_version_two = replace(version_two, account_id="stale-account")

        with self.assertRaises(VersionConflict):
            self.repository.save(stale_version_two, expected_version=1)

    def test_save_rejects_skipped_state_version(self) -> None:
        key = _key("chat-1")
        current = self.repository.get_or_create(key, self.clock())
        skipped = replace(
            current,
            state_version=3,
            updated_at=self.clock(),
            expires_at=self.clock() + 100.0,
        )

        with self.assertRaises(VersionConflict):
            self.repository.save(skipped, expected_version=1)

    def test_expiry_cleanup_uses_injected_time(self) -> None:
        first_key = _key("chat-1")
        second_key = _key("chat-2")
        self.repository.get_or_create(first_key, self.clock())
        self.clock.advance(50.0)
        self.repository.get_or_create(second_key, self.clock())
        self.clock.advance(51.0)

        removed = self.repository.cleanup_expired(self.clock())

        self.assertEqual(removed, 1)
        self.assertIsNone(self.repository.get(first_key))
        self.assertIsNotNone(self.repository.get(second_key))

    def test_maximum_capacity_evicts_least_recently_used_session(self) -> None:
        repository = InMemoryDialogueSessionRepository(
            max_sessions=2,
            session_ttl_seconds=100.0,
            clock=self.clock,
            id_factory=SequenceIds("session"),
        )
        first_key = _key("chat-1")
        second_key = _key("chat-2")
        third_key = _key("chat-3")
        repository.get_or_create(first_key, self.clock())
        repository.get_or_create(second_key, self.clock())
        repository.get(first_key)

        repository.get_or_create(third_key, self.clock())

        self.assertIsNotNone(repository.get(first_key))
        self.assertIsNone(repository.get(second_key))
        self.assertIsNotNone(repository.get(third_key))
        self.assertEqual(repository.count(), 2)

    def test_delete_is_scoped_and_idempotent(self) -> None:
        key = _key("chat-1")
        self.repository.get_or_create(key, self.clock())

        self.assertTrue(self.repository.delete(key))
        self.assertFalse(self.repository.delete(key))
        self.assertEqual(self.repository.count(), 0)


if __name__ == "__main__":
    unittest.main()
