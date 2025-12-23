"""
下载队列模块。
提供队列、事件、统计与处理器封装。
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


TaskEventHandler = Callable[[DownloadTask], Awaitable[None]]
DownloadFunction = Callable[[DownloadTask], Awaitable["DownloadResult"]]


class DownloadQueue:
    """下载队列系统门面。"""

    def __init__(
        self,
        max_size: int = 20,
        task_timeout: float = 600.0,
        download_fn: Optional[DownloadFunction] = None,
        formatter: Optional[QueueFormatter] = None,
    ):
        """初始化下载队列。"""
        self._storage = TaskQueue(max_size=max_size)
        self._events = QueueEventEmitter()
        self._stats = QueueStatsCollector()
        self._formatter = formatter or default_formatter

        self._processor: Optional[TaskProcessor] = None
        self._download_fn = download_fn
        self._task_timeout = task_timeout

        if download_fn:
            self._create_processor()

        self._lock = asyncio.Lock()

    def _create_processor(self) -> None:
        """创建任务处理器。"""
        if self._download_fn:
            self._processor = TaskProcessor(
                queue=self._storage,
                download_fn=self._download_fn,
                events=self._events,
                stats=self._stats,
                task_timeout=self._task_timeout,
            )


    async def start(self) -> bool:
        """启动队列处理器。"""
        if not self._processor:
            logger.error("Cannot start queue: no download function configured")
            return False

        return await self._processor.start()

    async def stop(self, timeout: float = 30.0) -> bool:
        """停止队列处理器。"""
        if not self._processor:
            return True

        return await self._processor.stop(timeout)

    def set_download_function(self, fn: DownloadFunction) -> None:
        """设置或更新下载函数。"""
        self._download_fn = fn
        self._create_processor()


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
        """添加任务到队列。"""
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
            await self._events.emit(QueueEvent.TASK_ENQUEUED, task)
            logger.info(f"Task {task.task_id} enqueued: {message}")
            return True, message, task
        else:
            logger.warning(f"Failed to enqueue task: {message}")
            return False, message, None

    async def cancel_task(self, task_id: str) -> tuple[bool, str]:
        """按 ID 取消任务。"""
        if (
            self._processor
            and self._processor.current_task
            and self._processor.current_task.task_id == task_id
        ):
            success = await self._processor.cancel_current()
            if success:
                return True, "正在取消当前任务"
            return False, "无法取消正在处理的任务"

        task = await self._storage.remove(task_id)
        if task:
            task.try_transition_to(TaskStatus.CANCELLED)
            await self._events.emit(QueueEvent.TASK_CANCELLED, task)
            self._stats.record_failure(task, reason="cancelled")
            return True, f"任务 {task_id} 已取消"

        return False, f"未找到任务 {task_id}"

    async def cancel_user_tasks(self, user_id: str) -> tuple[int, str]:
        """取消用户的全部任务。"""
        removed = await self._storage.remove_user_tasks(user_id)

        for task in removed:
            task.try_transition_to(TaskStatus.CANCELLED)
            await self._events.emit(QueueEvent.TASK_CANCELLED, task)
            self._stats.record_failure(task, reason="cancelled")

        count = len(removed)
        if count > 0:
            return count, f"已取消 {count} 个任务"
        return 0, "没有找到该用户的任务"


    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """按 ID 获取任务。"""
        return self._storage.get(task_id)

    def get_user_tasks(self, user_id: str) -> List[DownloadTask]:
        """获取用户的全部任务。"""
        return self._storage.get_user_tasks(user_id)

    def get_position(self, task_id: str) -> int:
        """获取任务在队列中的位置（从 1 开始）。"""
        return self._storage.get_position(task_id)

    def has_duplicate(self, user_id: str, url: str) -> bool:
        """检查用户是否有重复待处理任务。"""
        return self._storage.has_duplicate(user_id, url)

    def list_tasks(self, limit: Optional[int] = None) -> List[DownloadTask]:
        """获取待处理任务列表。"""
        return self._storage.list_tasks(limit)

    @property
    def current_task(self) -> Optional[DownloadTask]:
        """获取当前处理中的任务。"""
        if self._processor:
            return self._processor.current_task
        return None

    @property
    def is_empty(self) -> bool:
        """检查队列是否为空。"""
        return self._storage.is_empty

    @property
    def is_full(self) -> bool:
        """检查队列是否已满。"""
        return self._storage.is_full

    @property
    def size(self) -> int:
        """获取当前队列大小。"""
        return len(self._storage)

    @property
    def max_size(self) -> int:
        """获取队列最大容量。"""
        return self._storage.max_size

    @property
    def is_running(self) -> bool:
        """检查处理器是否在运行。"""
        return self._processor.is_running if self._processor else False


    def on_enqueued(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务入队事件处理器。"""
        return self._events.on(QueueEvent.TASK_ENQUEUED, handler)

    def on_started(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务开始事件处理器。"""
        return self._events.on(QueueEvent.TASK_STARTED, handler)

    def on_completed(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务完成事件处理器。"""
        return self._events.on(QueueEvent.TASK_COMPLETED, handler)

    def on_failed(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务失败事件处理器。"""
        return self._events.on(QueueEvent.TASK_FAILED, handler)

    def on_cancelled(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务取消事件处理器。"""
        return self._events.on(QueueEvent.TASK_CANCELLED, handler)

    def on_timeout(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务超时事件处理器。"""
        return self._events.on(QueueEvent.TASK_TIMEOUT, handler)

    def off(self, event: QueueEvent, handler: Optional[TaskEventHandler] = None) -> int:
        """移除事件处理器。"""
        return self._events.off(event, handler)


    def get_stats(self) -> QueueStats:
        """获取队列统计信息。"""
        return self._stats.get_stats(
            pending_count=len(self._storage),
            processing_count=1 if self.current_task else 0,
            queue_size=len(self._storage),
            max_queue_size=self._storage.max_size,
        )


    def format_queue_status(self) -> str:
        """获取格式化队列状态。"""
        return self._formatter.format_queue_status(
            tasks=self.list_tasks(),
            current_task=self.current_task,
            stats=self.get_stats(),
        )

    def format_task_info(self, task_id: str) -> str:
        """获取格式化任务信息。"""
        task = self.get_task(task_id)
        if not task:
            return f"未找到任务 {task_id}"

        position = self.get_position(task_id)
        return self._formatter.format_task_info(task, position)

    def format_user_tasks(self, user_id: str, user_name: str = "") -> str:
        """获取格式化用户任务列表。"""
        tasks = self.get_user_tasks(user_id)
        return self._formatter.format_user_tasks(tasks, user_name or user_id)


    async def clear(self) -> int:
        """清空队列任务。"""
        return await self._storage.clear()

    def __repr__(self) -> str:
        running = "running" if self.is_running else "stopped"
        return f"DownloadQueue(size={self.size}/{self.max_size}, status={running})"


__all__ = [
    "DownloadQueue",
    "DownloadTask",
    "TaskStatus",
    "TaskPriority",
    "TaskStateMachine",
    "QueueEvent",
    "QueueEventEmitter",
    "TaskEventAdapter",
    "EventSubscription",
    "QueueStats",
    "QueueStatsCollector",
    "TaskTiming",
    "TaskQueue",
    "PriorityStrategy",
    "FIFOWithPriorityStrategy",
    "TaskProcessor",
    "QueueFormatter",
    "ChineseFormatter",
    "MinimalFormatter",
    "default_formatter",
]
