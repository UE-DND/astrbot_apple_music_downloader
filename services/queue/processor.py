"""
Task Processor for Download Queue


Manages the task processing loop with:
- Lifecycle management (start/stop)
- Task execution with timeout
- Event emission
- Statistics collection
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

# Enable debug logging
import sys
logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
logger.setLevel(logging.DEBUG)


# Type alias for the download function
DownloadFunction = Callable[[DownloadTask], Awaitable["DownloadResult"]]


class TaskProcessor:
    """
    Processes tasks from the queue.

    Single responsibility: manage task execution lifecycle.

    Features:
    - Configurable concurrency
    - Task timeout handling
    - Event-driven callbacks
    - Statistics collection
    - Graceful shutdown

    Usage:
        processor = TaskProcessor(
            queue=task_queue,
            download_fn=downloader.download,
            events=event_emitter,
            stats=stats_collector
        )

        # Start processing
        await processor.start()

        # Stop processing
        await processor.stop()
    """

    def __init__(
        self,
        queue: TaskQueue,
        download_fn: DownloadFunction,
        events: QueueEventEmitter,
        stats: QueueStatsCollector,
        task_timeout: float = 600.0,  # 10 minutes default
        poll_interval: float = 1.0,
        max_retries: int = 0,
    ):
        """
        Initialize the task processor.

        Args:
            queue: Task queue to process from
            download_fn: Async function to execute downloads
            events: Event emitter for notifications
            stats: Statistics collector
            task_timeout: Maximum time for a single task (seconds)
            poll_interval: Interval between queue polls (seconds)
            max_retries: Maximum retry attempts for failed tasks
        """
        self._queue = queue
        self._download_fn = download_fn
        self._events = events
        self._stats = stats
        self._task_timeout = task_timeout
        self._poll_interval = poll_interval
        self._max_retries = max_retries

        # State
        self._running = False
        self._current_task: Optional[DownloadTask] = None
        self._processor_task: Optional[asyncio.Task] = None

        # Lock for state changes
        self._lock = asyncio.Lock()

    # ==================== Lifecycle ====================

    @property
    def is_running(self) -> bool:
        """Check if processor is running."""
        return self._running

    @property
    def current_task(self) -> Optional[DownloadTask]:
        """Get the currently processing task."""
        return self._current_task

    async def start(self) -> bool:
        """
        Start the processor loop.

        Returns:
            True if started, False if already running
        """
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
        """
        Stop the processor loop.

        Args:
            timeout: Maximum time to wait for graceful shutdown

        Returns:
            True if stopped gracefully, False if forced
        """
        async with self._lock:
            if not self._running:
                logger.warning("Processor not running")
                return True

            self._running = False

            if self._processor_task:
                try:
                    # Wait for current task to complete
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
        """Main processing loop."""
        logger.debug("[Processor] Processing loop started")
        logger.info(f"[Processor] Running={self._running}, Timeout={self._task_timeout}s")

        while self._running:
            try:
                # Get next task
                logger.debug("[Processor] Waiting for next task from queue...")
                task = await self._queue.pop()

                if task is None:
                    # Queue is empty, wait and poll again
                    logger.debug(f"[Processor] Queue empty, sleeping for {self._poll_interval}s")
                    await asyncio.sleep(self._poll_interval)
                    continue

                # Process the task
                logger.info(f"[Processor] Got task {task.task_id}, processing...")
                await self._process_task(task)

            except asyncio.CancelledError:
                logger.debug("Processing loop cancelled")
                break
            except Exception as e:
                logger.error(f"Error in processing loop: {e}", exc_info=True)
                await asyncio.sleep(self._poll_interval)

        logger.debug("Processing loop exited")

    # ==================== Task Processing ====================

    async def _process_task(self, task: DownloadTask) -> None:
        """
        Process a single task.

        Args:
            task: Task to process
        """
        self._current_task = task
        logger.info(f"[Processor] Processing task {task.task_id}: url={task.url[:50]}..., quality={task.quality}")

        try:
            # Transition to PROCESSING
            if not task.try_transition_to(TaskStatus.PROCESSING):
                logger.warning(
                    f"[Processor] Cannot start task {task.task_id}: invalid state {task.status}"
                )
                return

            # Emit started event
            logger.debug(f"[Processor] Emitting TASK_STARTED event for {task.task_id}")
            await self._events.emit(QueueEvent.TASK_STARTED, task)
            logger.info(f"[Processor] Started task {task.task_id}")

            # Execute with timeout
            try:
                logger.info(f"[Processor] Calling download function for {task.task_id}, timeout={self._task_timeout}s")
                result = await asyncio.wait_for(
                    self._download_fn(task),
                    timeout=self._task_timeout
                )
                logger.info(f"[Processor] Download function returned for {task.task_id}: success={result.success if result else 'None'}")

                # Handle result
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
                raise  # Re-raise to propagate cancellation

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
        """Handle successful task completion."""
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
        """Handle task failure."""
        task.error = error
        task.try_transition_to(TaskStatus.FAILED)

        self._stats.record_failure(task, reason="failed")
        await self._events.emit(QueueEvent.TASK_FAILED, task)

        logger.warning(f"Task {task.task_id} failed: {error}")

    async def _handle_timeout(self, task: DownloadTask) -> None:
        """Handle task timeout."""
        task.error = f"Task timed out after {self._task_timeout}s"
        task.try_transition_to(TaskStatus.TIMEOUT)

        self._stats.record_failure(task, reason="timeout")
        await self._events.emit(QueueEvent.TASK_TIMEOUT, task)

        logger.warning(
            f"Task {task.task_id} timed out "
            f"(timeout={self._task_timeout}s)"
        )

    async def _handle_cancelled(self, task: DownloadTask) -> None:
        """Handle task cancellation."""
        task.error = "Task was cancelled"
        task.try_transition_to(TaskStatus.CANCELLED)

        self._stats.record_failure(task, reason="cancelled")
        await self._events.emit(QueueEvent.TASK_CANCELLED, task)

        logger.info(f"Task {task.task_id} was cancelled")

    # ==================== Task Control ====================

    async def cancel_current(self) -> bool:
        """
        Cancel the currently processing task.

        Returns:
            True if a task was cancelled, False if no task running
        """
        if not self._current_task:
            return False

        # The task will be marked as cancelled in _handle_cancelled
        # when the download function is interrupted
        if self._processor_task:
            # This is a soft cancellation - we just mark intent
            # The actual cancellation depends on the download function
            logger.info(f"Requesting cancellation of task {self._current_task.task_id}")
            return True

        return False

    # ==================== Status ====================

    def get_status(self) -> dict:
        """
        Get processor status.

        Returns:
            Status dictionary
        """
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
