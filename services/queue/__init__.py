"""
Download Queue Module


A modular, SOLID-compliant task queue implementation.

Architecture:
    - task.py: Task data class and state machine
    - events.py: Event system (Observer pattern)
    - stats.py: Statistics collector
    - storage.py: Pure queue data structure
    - processor.py: Task processing loop
    - formatter.py: Output formatting

Usage:
    from services.queue import DownloadQueue, DownloadTask, TaskPriority

    # Create queue with download function
    queue = DownloadQueue(
        max_size=20,
        task_timeout=600,
        download_fn=my_download_function,
    )

    # Register event handlers
    queue.on_completed(handle_completed)
    queue.on_failed(handle_failed)

    # Start processing
    await queue.start()

    # Add task
    success, message, task = await queue.enqueue(
        url="https://music.apple.com/...",
        quality="alac",
        user_id="user123",
        user_name="User",
    )

    # Stop processing
    await queue.stop()
"""

from __future__ import annotations
import asyncio
import logging
from typing import Callable, Awaitable, Optional, List, TYPE_CHECKING

from .task import DownloadTask, TaskStatus, TaskPriority, TaskStateMachine
from .events import QueueEventEmitter, QueueEvent, TaskEventAdapter, EventSubscription
from .stats import QueueStats, QueueStatsCollector, TaskTiming
from .storage import TaskQueue, PriorityStrategy, FIFOWithPriorityStrategy
from .processor import TaskProcessor
from .formatter import QueueFormatter, ChineseFormatter, MinimalFormatter, default_formatter

if TYPE_CHECKING:
    from ..downloader import DownloadResult

logger = logging.getLogger(__name__)


# Type aliases
TaskEventHandler = Callable[[DownloadTask], Awaitable[None]]
DownloadFunction = Callable[[DownloadTask], Awaitable["DownloadResult"]]


class DownloadQueue:
    """
    Facade for the download queue system.

    Provides a simplified interface to the underlying components:
    - TaskQueue: Task storage and ordering
    - TaskProcessor: Task execution
    - QueueEventEmitter: Event notifications
    - QueueStatsCollector: Statistics
    - QueueFormatter: Output formatting

    This class maintains API compatibility while internally using
    the modular component architecture.
    """

    def __init__(
        self,
        max_size: int = 20,
        task_timeout: float = 600.0,
        download_fn: Optional[DownloadFunction] = None,
        formatter: Optional[QueueFormatter] = None,
    ):
        """
        Initialize the download queue.

        Args:
            max_size: Maximum queue size
            task_timeout: Task timeout in seconds
            download_fn: Function to execute downloads
            formatter: Output formatter (default: ChineseFormatter)
        """
        # Components
        self._storage = TaskQueue(max_size=max_size)
        self._events = QueueEventEmitter()
        self._stats = QueueStatsCollector()
        self._formatter = formatter or default_formatter

        # Processor (created when download_fn is set)
        self._processor: Optional[TaskProcessor] = None
        self._download_fn = download_fn
        self._task_timeout = task_timeout

        if download_fn:
            self._create_processor()

        # Lock for thread-safe operations
        self._lock = asyncio.Lock()

    def _create_processor(self) -> None:
        """Create the task processor."""
        if self._download_fn:
            self._processor = TaskProcessor(
                queue=self._storage,
                download_fn=self._download_fn,
                events=self._events,
                stats=self._stats,
                task_timeout=self._task_timeout,
            )

    # ==================== Lifecycle ====================

    async def start(self) -> bool:
        """
        Start the queue processor.

        Returns:
            True if started, False if already running or no download function
        """
        if not self._processor:
            logger.error("Cannot start queue: no download function configured")
            return False

        return await self._processor.start()

    async def stop(self, timeout: float = 30.0) -> bool:
        """
        Stop the queue processor.

        Args:
            timeout: Maximum time to wait for graceful shutdown

        Returns:
            True if stopped gracefully
        """
        if not self._processor:
            return True

        return await self._processor.stop(timeout)

    def set_download_function(self, fn: DownloadFunction) -> None:
        """
        Set or update the download function.

        Args:
            fn: Async function to execute downloads
        """
        self._download_fn = fn
        self._create_processor()

    # ==================== Task Management ====================

    async def enqueue(
        self,
        url: str,
        quality: str,
        user_id: str,
        user_name: str,
        unified_msg_origin: str = "",
        quality_display: str = "",
        song_name: Optional[str] = None,
        priority: TaskPriority = TaskPriority.NORMAL,
    ) -> tuple[bool, str, Optional[DownloadTask]]:
        """
        Add a task to the queue.

        Args:
            url: Download URL
            quality: Quality setting
            user_id: User ID
            user_name: User display name
            unified_msg_origin: Message origin for reply
            quality_display: Human-readable quality name
            song_name: Song name (optional)
            priority: Task priority

        Returns:
            Tuple of (success, message, task)
        """
        task = DownloadTask(
            url=url,
            quality=quality,
            quality_display=quality_display,
            user_id=user_id,
            user_name=user_name,
            unified_msg_origin=unified_msg_origin,
            song_name=song_name,
            priority=priority,
        )

        success, message = await self._storage.push(task)

        if success:
            # Emit enqueued event
            await self._events.emit(QueueEvent.TASK_ENQUEUED, task)
            logger.info(f"Task {task.task_id} enqueued: {message}")
            return True, message, task
        else:
            logger.warning(f"Failed to enqueue task: {message}")
            return False, message, None

    async def cancel_task(self, task_id: str) -> tuple[bool, str]:
        """
        Cancel a task by ID.

        Args:
            task_id: Task ID to cancel

        Returns:
            Tuple of (success, message)
        """
        # Check if it's the current task
        if (
            self._processor
            and self._processor.current_task
            and self._processor.current_task.task_id == task_id
        ):
            # Request cancellation of current task
            success = await self._processor.cancel_current()
            if success:
                return True, "正在取消当前任务"
            return False, "无法取消正在处理的任务"

        # Try to remove from queue
        task = await self._storage.remove(task_id)
        if task:
            task.try_transition_to(TaskStatus.CANCELLED)
            await self._events.emit(QueueEvent.TASK_CANCELLED, task)
            self._stats.record_failure(task, reason="cancelled")
            return True, f"任务 {task_id} 已取消"

        return False, f"未找到任务 {task_id}"

    async def cancel_user_tasks(self, user_id: str) -> tuple[int, str]:
        """
        Cancel all tasks for a user.

        Args:
            user_id: User ID

        Returns:
            Tuple of (cancelled_count, message)
        """
        removed = await self._storage.remove_user_tasks(user_id)

        for task in removed:
            task.try_transition_to(TaskStatus.CANCELLED)
            await self._events.emit(QueueEvent.TASK_CANCELLED, task)
            self._stats.record_failure(task, reason="cancelled")

        count = len(removed)
        if count > 0:
            return count, f"已取消 {count} 个任务"
        return 0, "没有找到该用户的任务"

    # ==================== Query Methods ====================

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """Get task by ID."""
        return self._storage.get(task_id)

    def get_user_tasks(self, user_id: str) -> List[DownloadTask]:
        """Get all tasks for a user."""
        return self._storage.get_user_tasks(user_id)

    def get_position(self, task_id: str) -> int:
        """Get task position in queue (1-based)."""
        return self._storage.get_position(task_id)

    def has_duplicate(self, user_id: str, url: str) -> bool:
        """Check if user has duplicate pending task."""
        return self._storage.has_duplicate(user_id, url)

    def list_tasks(self, limit: Optional[int] = None) -> List[DownloadTask]:
        """Get list of pending tasks."""
        return self._storage.list_tasks(limit)

    @property
    def current_task(self) -> Optional[DownloadTask]:
        """Get currently processing task."""
        if self._processor:
            return self._processor.current_task
        return None

    @property
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return self._storage.is_empty

    @property
    def is_full(self) -> bool:
        """Check if queue is full."""
        return self._storage.is_full

    @property
    def size(self) -> int:
        """Get current queue size."""
        return len(self._storage)

    @property
    def max_size(self) -> int:
        """Get maximum queue size."""
        return self._storage.max_size

    @property
    def is_running(self) -> bool:
        """Check if processor is running."""
        return self._processor.is_running if self._processor else False

    # ==================== Event Registration ====================

    def on_enqueued(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task enqueued event."""
        return self._events.on(QueueEvent.TASK_ENQUEUED, handler)

    def on_started(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task started event."""
        return self._events.on(QueueEvent.TASK_STARTED, handler)

    def on_completed(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task completed event."""
        return self._events.on(QueueEvent.TASK_COMPLETED, handler)

    def on_failed(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task failed event."""
        return self._events.on(QueueEvent.TASK_FAILED, handler)

    def on_cancelled(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task cancelled event."""
        return self._events.on(QueueEvent.TASK_CANCELLED, handler)

    def on_timeout(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task timeout event."""
        return self._events.on(QueueEvent.TASK_TIMEOUT, handler)

    def off(self, event: QueueEvent, handler: Optional[TaskEventHandler] = None) -> int:
        """Remove event handler(s)."""
        return self._events.off(event, handler)

    # ==================== Statistics ====================

    def get_stats(self) -> QueueStats:
        """Get queue statistics."""
        return self._stats.get_stats(
            pending_count=len(self._storage),
            processing_count=1 if self.current_task else 0,
            queue_size=len(self._storage),
            max_queue_size=self._storage.max_size,
        )

    # ==================== Formatted Output ====================

    def format_queue_status(self) -> str:
        """Get formatted queue status."""
        return self._formatter.format_queue_status(
            tasks=self.list_tasks(),
            current_task=self.current_task,
            stats=self.get_stats(),
        )

    def format_task_info(self, task_id: str) -> str:
        """Get formatted task information."""
        task = self.get_task(task_id)
        if not task:
            return f"未找到任务 {task_id}"

        position = self.get_position(task_id)
        return self._formatter.format_task_info(task, position)

    def format_user_tasks(self, user_id: str, user_name: str = "") -> str:
        """Get formatted user tasks."""
        tasks = self.get_user_tasks(user_id)
        return self._formatter.format_user_tasks(tasks, user_name or user_id)

    # ==================== Utilities ====================

    async def clear(self) -> int:
        """Clear all tasks from queue."""
        return await self._storage.clear()

    def __repr__(self) -> str:
        running = "running" if self.is_running else "stopped"
        return f"DownloadQueue(size={self.size}/{self.max_size}, status={running})"


# Module exports
__all__ = [
    # Main class
    "DownloadQueue",

    # Task
    "DownloadTask",
    "TaskStatus",
    "TaskPriority",
    "TaskStateMachine",

    # Events
    "QueueEvent",
    "QueueEventEmitter",
    "TaskEventAdapter",
    "EventSubscription",

    # Stats
    "QueueStats",
    "QueueStatsCollector",
    "TaskTiming",

    # Storage
    "TaskQueue",
    "PriorityStrategy",
    "FIFOWithPriorityStrategy",

    # Processor
    "TaskProcessor",

    # Formatter
    "QueueFormatter",
    "ChineseFormatter",
    "MinimalFormatter",
    "default_formatter",
]
