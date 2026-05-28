import time
import unittest

from security.rate_limit import RateLimitPolicy, RateLimiter


class TestRateLimiter(unittest.TestCase):
    def test_cooldown_blocks(self) -> None:
        limiter = RateLimiter(RateLimitPolicy(1.0, 60, 10))
        uid = 1
        self.assertFalse(limiter.limited(uid, "download"))
        self.assertTrue(limiter.limited(uid, "download"))
        time.sleep(1.05)
        self.assertFalse(limiter.limited(uid, "download"))

    def test_window_limit_blocks(self) -> None:
        limiter = RateLimiter(RateLimitPolicy(0.0, 1, 2))
        uid = 2
        self.assertFalse(limiter.limited(uid, "search"))
        self.assertFalse(limiter.limited(uid, "search"))
        self.assertTrue(limiter.limited(uid, "search"))


if __name__ == "__main__":
    unittest.main()
