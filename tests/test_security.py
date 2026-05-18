"""Security test cases for Notes-Buddy."""

import re
import unittest

from bot.commands import _safe_error
from drive.drive_service import _sanitize_filename, _sanitize_query_value


class TestErrorSanitization(unittest.TestCase):
    def test_oauth_token_redaction(self) -> None:
        error = Exception("Token: ya29.a0AXooCgv-abc123")
        redacted = _safe_error(error)
        self.assertIn("[redacted]", redacted)
        self.assertNotIn("ya29", redacted)

    def test_file_path_redaction(self) -> None:
        error = Exception(r"Error in C:\Users\data\bot_data.db")
        redacted = _safe_error(error)
        self.assertIn("[path]", redacted)
        self.assertNotIn(r"C:\Users", redacted)

    def test_fernet_token_redaction(self) -> None:
        error = Exception("Token: gAAAAABlYwK3XW0dPp8oKJvQkQ2-1234567890")
        redacted = _safe_error(error)
        self.assertIn("[redacted]", redacted)

    def test_truncation(self) -> None:
        error = Exception("x" * 300)
        redacted = _safe_error(error)
        self.assertLessEqual(len(redacted), 200)


class TestFilenameSanitization(unittest.TestCase):
    def test_directory_traversal_blocked(self) -> None:
        self.assertNotIn("..", _sanitize_filename("../../etc/passwd"))
        self.assertNotIn("\\", _sanitize_filename(r"..\..\windows\system32"))

    def test_null_bytes_removed(self) -> None:
        result = _sanitize_filename("file\x00name.txt")
        self.assertNotIn("\x00", result)
        self.assertEqual(result, "filename.txt")

    def test_control_characters_removed(self) -> None:
        result = _sanitize_filename("file\x1f\x1e\x1dname.txt")
        self.assertNotIn("\x1f", result)
        self.assertNotIn("\x1e", result)
        self.assertNotIn("\x1d", result)

    def test_leading_dot_stripped(self) -> None:
        result = _sanitize_filename("...hidden_file.txt")
        self.assertFalse(result.startswith("."))

    def test_max_length_enforced(self) -> None:
        long_name = "x" * 500 + ".txt"
        result = _sanitize_filename(long_name)
        self.assertLessEqual(len(result), 200)


class TestQuerySanitization(unittest.TestCase):
    def test_quote_escaping(self) -> None:
        result = _sanitize_query_value("O'Reilly")
        self.assertIn("\\'", result)

    def test_backslash_escaping(self) -> None:
        result = _sanitize_query_value(r"C:\folder")
        self.assertIn("\\\\", result)

    def test_newline_removal(self) -> None:
        result = _sanitize_query_value("file\nname")
        self.assertNotIn("\n", result)
        self.assertEqual(result, "file name")


class TestStateValidation(unittest.TestCase):
    def test_state_format_regex(self) -> None:
        state = "123456789:abc123_-ABC123"
        self.assertIsNotNone(re.match(r'^\d+:[A-Za-z0-9_-]+$', state))

    def test_invalid_state_rejected(self) -> None:
        invalid_states = [
            "not-a-valid-state",
            "123",
            "123:|bad-nonce",
            "abc123:123456",
        ]
        for state in invalid_states:
            self.assertIsNone(re.match(r'^\d+:[A-Za-z0-9_-]+$', state))


if __name__ == "__main__":
    unittest.main()
