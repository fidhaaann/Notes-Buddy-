import unittest

from bot import nav


class TestNavLoopDetection(unittest.TestCase):
    def test_is_in_stack(self) -> None:
        uid = 9999
        nav.go_home(uid)
        self.assertTrue(nav.is_in_stack(uid, "root"))
        nav.push_folder(uid, "folder1", "Folder 1")
        self.assertTrue(nav.is_in_stack(uid, "folder1"))


if __name__ == "__main__":
    unittest.main()
