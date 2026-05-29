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


if __name__ == "__main__":
    unittest.main()
