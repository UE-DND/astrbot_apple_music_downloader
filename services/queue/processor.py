"""
下载队列任务处理器。
负责任务生命周期、超时与事件统计。
"""

from __future__ import annotations
import asyncio
import logging
from typing import Callable, Awaitable, Optional, Any, TYPE_CHECKING

from .task import DownloadTask, TaskStatus
from .storage import TaskQueue
from .events import QueueEventEmitter, QueueEvent
from .stats import QueueStatsCollector

if TYPE_CHECKING:
    from ..downloader import DownloadResult

logger = logging.getLogger(__name__)

import sys
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
logger.setLevel(logging.DEBUG)

DownloadFunction = Callable[[DownloadTask], Awaitable["DownloadResult"]]


class TaskProcessor:
    """处理队列任务的执行循环。"""

    def __init__(
        self,
        queue: TaskQueue,
        download_fn: DownloadFunction,
        events: QueueEventEmitter,
        stats: QueueStatsCollector,
        task_timeout: float = 600.0,  # 默认 10 分钟
        poll_interval: float = 1.0,
        max_retries: int = 0,
    ):
        """初始化任务处理器。"""
        self._queue = queue
        self._download_fn = download_fn
        self._events = events
        self._stats = stats
        self._task_timeout = task_timeout
        self._poll_interval = poll_interval
        self._max_retries = max_retries

        self._running = False
        self._current_task: Optional[DownloadTask] = None
        self._processor_task: Optional[asyncio.Task] = None

        self._lock = asyncio.Lock()


    @property
    def is_running(self) -> bool:
        """检查处理器是否运行中。"""
        return self._running

    @property
    def current_task(self) -> Optional[DownloadTask]:
        """获取当前处理中的任务。"""
        return self._current_task

    async def start(self) -> bool:
        """启动处理器循环。"""
        async with self._lock:
            if self._running:
                logger.warning("Processor already running")
                return False

            self._running = True
            self._processor_task = asyncio.create_task(
                self._processing_loop(),
                name="task-processor"
            )

            await self._events.emit(QueueEvent.PROCESSOR_STARTED)
            logger.info("Task processor started")
            return True

    async def stop(self, timeout: float = 30.0) -> bool:
        """停止处理器循环。"""
        async with self._lock:
            if not self._running:
                logger.warning("Processor not running")
                return True

            self._running = False

            if self._processor_task:
                try:
                    await asyncio.wait_for(
                        self._processor_task,
                        timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        f"Processor did not stop within {timeout}s, cancelling"
                    )
                    self._processor_task.cancel()
                    try:
                        await self._processor_task
                    except asyncio.CancelledError:
                        pass
                    return False
                finally:
                    self._processor_task = None

            await self._events.emit(QueueEvent.PROCESSOR_STOPPED)
            logger.info("Task processor stopped")
            return True

    async def _processing_loop(self) -> None:
        """主处理循环。"""
        logger.debug("[Processor] Processing loop started")
        logger.info(f"[Processor] Running={self._running}, Timeout={self._task_timeout}s")

        while self._running:
            try:
                logger.debug("[Processor] Waiting for next task from queue...")
                task = await self._queue.pop()

                if task is None:
                    logger.debug(f"[Processor] Queue empty, sleeping for {self._poll_interval}s")
                    await asyncio.sleep(self._poll_interval)
                    continue

                logger.info(f"[Processor] Got task {task.task_id}, processing...")
                await self._process_task(task)

            except asyncio.CancelledError:
                logger.debug("Processing loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in processing loop: {e}", exc_info=True)
                await asyncio.sleep(self._poll_interval)

        logger.debug("Processing loop exited")


    async def _process_task(self, task: DownloadTask) -> None:
        """处理单个任务。"""
        self._current_task = task
        logger.info(f"[Processor] Processing task {task.task_id}: url={task.url[:50]}..., quality={task.quality}")

        try:
            if not task.try_transition_to(TaskStatus.PROCESSING):
                logger.warning(
                    f"[Processor] Cannot start task {task.task_id}: invalid state {task.status}"
                )
                return

            logger.debug(f"[Processor] Emitting TASK_STARTED event for {task.task_id}")
            await self._events.emit(QueueEvent.TASK_STARTED, task)
            logger.info(f"[Processor] Started task {task.task_id}")

            try:
                logger.info(f"[Processor] Calling download function for {task.task_id}, timeout={self._task_timeout}s")
                result = await asyncio.wait_for(
                    self._download_fn(task),
                    timeout=self._task_timeout
                )
                logger.info(f"[Processor] Download function returned for {task.task_id}: success={result.success if result else 'None'}")

                if result and result.success:
                    await self._handle_success(task, result)
                else:
                    error_msg = result.error if result else "Unknown error"
                    logger.warning(f"[Processor] Task {task.task_id} returned failure: {error_msg}")
                    await self._handle_failure(task, error_msg)

            except asyncio.TimeoutError:
                await self._handle_timeout(task)
            except asyncio.CancelledError:
                await self._handle_cancelled(task)
                raise

        except Exception as e:
            logger.error(f"Unexpected error processing task {task.task_id}: {e}")
            await self._handle_failure(task, str(e))

        finally:
            self._current_task = None

    async def _handle_success(
        self,
        task: DownloadTask,
        result: "DownloadResult"
    ) -> None:
        """处理任务成功完成。"""
        task.result = result
        task.try_transition_to(TaskStatus.COMPLETED)

        self._stats.record_completion(task)
        await self._events.emit(QueueEvent.TASK_COMPLETED, task)

        logger.info(
            f"Task {task.task_id} completed successfully "
            f"(process_time={task.process_time:.2f}s)"
        )

    async def _handle_failure(
        self,
        task: DownloadTask,
        error: str
    ) -> None:
        """处理任务失败。"""
        task.error = error
        task.try_transition_to(TaskStatus.FAILED)

        self._stats.record_failure(task, reason="failed")
        await self._events.emit(QueueEvent.TASK_FAILED, task)

        logger.warning(f"Task {task.task_id} failed: {error}")

    async def _handle_timeout(self, task: DownloadTask) -> None:
        """处理任务超时。"""
        task.error = f"Task timed out after {self._task_timeout}s"
        task.try_transition_to(TaskStatus.TIMEOUT)

        self._stats.record_failure(task, reason="timeout")
        await self._events.emit(QueueEvent.TASK_TIMEOUT, task)

        logger.warning(
            f"Task {task.task_id} timed out "
            f"(timeout={self._task_timeout}s)"
        )

    async def _handle_cancelled(self, task: DownloadTask) -> None:
        """处理任务取消。"""
        task.error = "Task was cancelled"
        task.try_transition_to(TaskStatus.CANCELLED)

        self._stats.record_failure(task, reason="cancelled")
        await self._events.emit(QueueEvent.TASK_CANCELLED, task)

        logger.info(f"Task {task.task_id} was cancelled")


    async def cancel_current(self) -> bool:
        """取消当前处理中的任务。"""
        if not self._current_task:
            return False

        if self._processor_task:
            # 仅标记取消，实际中断取决于下载函数
            logger.info(f"Requesting cancellation of task {self._current_task.task_id}")
            return True

        return False


    def get_status(self) -> dict:
        """获取处理器状态。"""
        return {
            "running": self._running,
            "current_task": (
                self._current_task.task_id if self._current_task else None
            ),
            "task_timeout": self._task_timeout,
            "poll_interval": self._poll_interval,
        }

    def __repr__(self) -> str:
        status = "running" if self._running else "stopped"
        current = self._current_task.task_id if self._current_task else "none"
        return f"TaskProcessor(status={status}, current={current})"
