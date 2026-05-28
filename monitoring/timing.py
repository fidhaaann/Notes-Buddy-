"""Operation timing utilities."""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def timed(operation: str) -> None:
    start = time.monotonic()
    status = "ok"
    try:
        yield
    except Exception:
        status = "error"
        raise
    finally:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "op_timing operation=%s duration_ms=%s status=%s",
            operation,
            duration_ms,
            status,
        )
