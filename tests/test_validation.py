import unittest

from security import validators, uploads


class TestValidators(unittest.TestCase):
    def test_drive_id_validation(self) -> None:
        self.assertTrue(validators.validate_drive_id("1A2b3C4d5E6F7g8H9i0J"))
        self.assertTrue(validators.validate_drive_id("drive:1A2b3C4d5E6F7g8H9i0J"))
        self.assertFalse(validators.validate_drive_id("invalid id"))

    def test_normalize_keyword(self) -> None:
        value = validators.normalize_keyword("  hello\nworld\t", max_len=20)
        self.assertEqual(value, "hello world")

    def test_sanitize_filename(self) -> None:
        value = validators.sanitize_filename("../../etc/passwd")
        self.assertNotIn("..", value)
        self.assertNotIn("/", value)

    def test_zip_filename(self) -> None:
        value = validators.sanitize_zip_filename("My* Zip  Name!")
        self.assertEqual(value, "My_Zip_Name")


class TestUploads(unittest.TestCase):
    def test_upload_rejects_executables(self) -> None:
        ok, reason, safe_name, detected = uploads.validate_upload(
            b"dummy", "evil.exe", "application/x-msdownload", 1024
        )
        self.assertFalse(ok)
        self.assertIn("Executable", reason)


if __name__ == "__main__":
    unittest.main()
