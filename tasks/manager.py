"""Background task queue for heavy operations (download, ZIP)."""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from bot import formatter
from db import models
from drive import drive_service as ds
from monitoring import context as monitoring_context
from security import limits, validators
from services.zip_service import create_zip
from storage import sandbox

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TaskJob:
    job_id: str
    task_type: str
    telegram_id: int
    chat_id: int | None
    message_id: int | None
    payload: dict
    notify: bool = True


class TaskManager:
    def __init__(self, bot, worker_count: int | None = None) -> None:
        self._bot = bot
        self._queue: asyncio.Queue[TaskJob | None] = asyncio.Queue()
        self._workers: list[asyncio.Task] = []
        self._running = False
        self._worker_count = worker_count or limits.TASK_WORKERS

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for _ in range(self._worker_count):
            self._workers.append(asyncio.create_task(self._worker_loop()))
        logger.info("task_manager_started workers=%s", self._worker_count)

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        for _ in self._workers:
            await self._queue.put(None)
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        logger.info("task_manager_stopped")

    async def enqueue_download(
        self,
        telegram_id: int,
        chat_id: int,
        file_id: str,
        filename: str,
        size_str: str,
    ) -> str:
        job_id = uuid.uuid4().hex
        message = await self._bot.send_message(
            chat_id=chat_id,
            text=formatter.task_queued("Download", filename, size_str, job_id),
        )
        models.create_task_job(job_id, telegram_id, "download", f"{filename} ({size_str})")
        await self._queue.put(
            TaskJob(
                job_id=job_id,
                task_type="download",
                telegram_id=telegram_id,
                chat_id=chat_id,
                message_id=message.message_id,
                payload={"file_id": file_id, "filename": filename},
                notify=True,
            )
        )
        return job_id

    async def enqueue_zip(self, telegram_id: int, chat_id: int, keyword: str) -> str:
        job_id = uuid.uuid4().hex
        message = await self._bot.send_message(
            chat_id=chat_id,
            text=formatter.task_queued("ZIP", keyword, None, job_id),
        )
        models.create_task_job(job_id, telegram_id, "zip", f"keyword={keyword}")
        await self._queue.put(
            TaskJob(
                job_id=job_id,
                task_type="zip",
                telegram_id=telegram_id,
                chat_id=chat_id,
                message_id=message.message_id,
                payload={"keyword": keyword},
                notify=True,
            )
        )
        return job_id

    async def enqueue_index(self, telegram_id: int, file_id: str) -> str:
        job_id = uuid.uuid4().hex
        models.create_task_job(job_id, telegram_id, "index", f"file_id={file_id}")
        await self._queue.put(
            TaskJob(
                job_id=job_id,
                task_type="index",
                telegram_id=telegram_id,
                chat_id=None,
                message_id=None,
                payload={"file_id": file_id},
                notify=False,
            )
        )
        return job_id

    async def _worker_loop(self) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                break
            monitoring_context.set_request_context(
                user_id=job.telegram_id,
                operation=f"task:{job.task_type}",
            )
            start = time.monotonic()
            status = "completed"
            try:
                models.update_task_job(job.job_id, status="running", progress=10, detail="running")
                await self._update_message(job, formatter.task_running(job.task_type))
                if job.task_type == "download":
                    await self._run_download(job)
                elif job.task_type == "zip":
                    await self._run_zip(job)
                elif job.task_type == "index":
                    await self._run_index(job)
                else:
                    raise ValueError("Unknown task type.")
                models.update_task_job(job.job_id, status="completed", progress=100, detail="done")
            except PermissionError:
                logger.warning("task_auth_failed job_id=%s type=%s", job.job_id, job.task_type)
                models.update_task_job(job.job_id, status="failed", progress=100, error_message="auth_required")
                await self._update_message(job, formatter.login_required())
                status = "auth_failed"
            except ValueError as exc:
                logger.warning("task_validation_failed job_id=%s type=%s", job.job_id, job.task_type)
                models.update_task_job(job.job_id, status="failed", progress=100, error_message=str(exc)[:200])
                await self._update_message(job, formatter.error(str(exc)))
                status = "validation_failed"
            except Exception as exc:
                logger.exception("task_failed job_id=%s type=%s", job.job_id, job.task_type)
                models.update_task_job(job.job_id, status="failed", progress=100, error_message=str(exc)[:200])
                await self._update_message(job, formatter.task_failed(job.task_type))
                status = "failed"
            finally:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.info(
                    "task_timing job_id=%s type=%s status=%s duration_ms=%s",
                    job.job_id,
                    job.task_type,
                    status,
                    duration_ms,
                )
                self._queue.task_done()

    async def _update_message(self, job: TaskJob, text: str) -> None:
        if not job.notify or not job.chat_id or not job.message_id:
            return
        try:
            await self._bot.edit_message_text(
                chat_id=job.chat_id,
                message_id=job.message_id,
                text=text,
            )
        except Exception:
            pass

    async def _run_download(self, job: TaskJob) -> None:
        file_id = job.payload.get("file_id", "")
        if not validators.validate_drive_id(file_id, allow_root=False):
            raise ValueError("Invalid file reference.")

        file_bytes, downloaded_name = await asyncio.to_thread(
            ds.download_file, job.telegram_id, file_id
        )
        path = sandbox.write_bytes(job.telegram_id, downloaded_name, file_bytes)
        try:
            with path.open("rb") as fh:
                await self._bot.send_document(
                    chat_id=job.chat_id,
                    document=fh,
                    filename=downloaded_name,
                    read_timeout=180,
                    write_timeout=180,
                    connect_timeout=30,
                )
            await self._update_message(job, formatter.task_complete("Download", downloaded_name))
        finally:
            sandbox.remove_file(path)

    async def _run_zip(self, job: TaskJob) -> None:
        keyword = validators.normalize_keyword(job.payload.get("keyword", ""), limits.MAX_SEARCH_LEN)
        if not keyword:
            raise ValueError("Empty keyword.")

        zip_bytes, zip_name, count = await asyncio.to_thread(
            self._build_zip_payload, job.telegram_id, keyword
        )
        path = sandbox.write_bytes(job.telegram_id, zip_name, zip_bytes)
        try:
            with path.open("rb") as fh:
                await self._bot.send_document(
                    chat_id=job.chat_id,
                    document=fh,
                    filename=zip_name,
                    caption=formatter.zip_ready(zip_name, count),
                )
            await self._update_message(job, formatter.task_complete("ZIP", zip_name))
        finally:
            sandbox.remove_file(path)

    async def _run_index(self, job: TaskJob) -> None:
        from indexing import indexer

        file_id = job.payload.get("file_id", "")
        if not file_id:
            raise ValueError("Invalid file reference.")
        await asyncio.to_thread(indexer.index_drive_file, job.telegram_id, file_id)

    @staticmethod
    def _build_zip_payload(telegram_id: int, keyword: str) -> tuple[bytes, str, int]:
        files = ds.search_files(telegram_id, keyword)
        if not files:
            raise ValueError("No files matched your keyword.")
        if len(files) > limits.MAX_ZIP_FILES:
            raise ValueError("Too many files to archive.")

        total = sum(int(f.get("size", 0)) for f in files)
        if total > limits.MAX_ZIP_BYTES:
            raise ValueError("Combined size too large for ZIP.")

        collected: list[tuple[bytes, str]] = []
        for f in files:
            try:
                file_bytes, fname = ds.download_file(telegram_id, f["id"])
                collected.append((file_bytes, fname))
            except Exception:
                logger.warning("zip_skip file_id=%s", f.get("id"))

        if not collected:
            raise ValueError("Could not download files for archive.")

        zip_bytes = create_zip(collected)
        zip_name = f"{validators.sanitize_zip_filename(keyword)}_files.zip"
        return zip_bytes, zip_name, len(collected)


def get_task_manager(context) -> TaskManager | None:
    return context.application.bot_data.get("task_manager") if context and context.application else None
