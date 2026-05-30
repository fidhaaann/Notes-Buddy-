import unittest

from nlp import normalize
from nlp import router
from nlp import intents


class TestNlpNormalization(unittest.TestCase):
    def test_normalize_text(self) -> None:
        self.assertEqual(normalize.normalize_text("dwnld the 2nd file"), "download the 2 file")

    def test_extract_index(self) -> None:
        self.assertEqual(normalize.extract_index("download 2"), "2")


class TestNlpIntent(unittest.TestCase):
    def test_download_intent(self) -> None:
        intent = router.interpret_intent("dwnld the second file")
        self.assertEqual(intent.intent, intents.IntentType.DOWNLOAD)
        self.assertEqual(intent.index, "2")

    def test_search_intent(self) -> None:
        intent = router.interpret_intent("find module 2 pdf")
        self.assertEqual(intent.intent, intents.IntentType.SEARCH)

    def test_login_intent(self) -> None:
        intent = router.interpret_intent("connect my drive")
        self.assertEqual(intent.intent, intents.IntentType.LOGIN)

    def test_start_intent(self) -> None:
        intent = router.interpret_intent("start")
        self.assertEqual(intent.intent, intents.IntentType.START)

    def test_menu_intent(self) -> None:
        intent = router.interpret_intent("show menu")
        self.assertEqual(intent.intent, intents.IntentType.MENU)

    def test_tool_intent(self) -> None:
        intent = router.interpret_intent("what can you do")
        self.assertEqual(intent.intent, intents.IntentType.TOOL)

    def test_email_intent(self) -> None:
        intent = router.interpret_intent("set my email to you@example.com")
        self.assertEqual(intent.intent, intents.IntentType.EMAIL)
        self.assertEqual(intent.email, "you@example.com")

    def test_verify_intent(self) -> None:
        intent = router.interpret_intent("verify 123456")
        self.assertEqual(intent.intent, intents.IntentType.VERIFY)
        self.assertEqual(intent.otp, "123456")

    def test_cancel_intent(self) -> None:
        intent = router.interpret_intent("cancel")
        self.assertEqual(intent.intent, intents.IntentType.CANCEL)

    def test_logout_intent(self) -> None:
        intent = router.interpret_intent("log out")
        self.assertEqual(intent.intent, intents.IntentType.LOGOUT)

    def test_zip_intent(self) -> None:
        intent = router.interpret_intent("zip all dbms notes")
        self.assertEqual(intent.intent, intents.IntentType.ZIP)
        self.assertTrue(intent.bulk)

    def test_mkdir_intent(self) -> None:
        intent = router.interpret_intent("create folder called DBMS")
        self.assertEqual(intent.intent, intents.IntentType.MKDIR)
        self.assertEqual(intent.target_name, "DBMS")

    def test_favorite_intent(self) -> None:
        intent = router.interpret_intent("favorite this file")
        self.assertEqual(intent.intent, intents.IntentType.FAVORITE)

    def test_unfavorite_intent(self) -> None:
        intent = router.interpret_intent("remove favorite")
        self.assertEqual(intent.intent, intents.IntentType.UNFAVORITE)

    def test_recent_intent(self) -> None:
        intent = router.interpret_intent("show recent files")
        self.assertEqual(intent.intent, intents.IntentType.RECENT)

    def test_share_intent(self) -> None:
        intent = router.interpret_intent("share this file")
        self.assertEqual(intent.intent, intents.IntentType.SHARE)

    def test_copy_intent(self) -> None:
        intent = router.interpret_intent("duplicate this file")
        self.assertEqual(intent.intent, intents.IntentType.COPY)

    def test_bulk_delete_intent(self) -> None:
        intent = router.interpret_intent("delete all temporary files")
        self.assertEqual(intent.intent, intents.IntentType.DELETE)
        self.assertTrue(intent.bulk)

    def test_bulk_download_intent(self) -> None:
        intent = router.interpret_intent("download all module 2 notes")
        self.assertEqual(intent.intent, intents.IntentType.DOWNLOAD)
        self.assertTrue(intent.bulk)

    def test_bulk_move_these_files(self) -> None:
        intent = router.interpret_intent("move these files to semester 4")
        self.assertEqual(intent.intent, intents.IntentType.MOVE)
        self.assertTrue(intent.bulk)


if __name__ == "__main__":
    unittest.main()
