"""
Apple Music Downloader - 下载队列管理模块
"""

import asyncio
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Callable, Awaitable, Dict, List, Any, TYPE_CHECKING

from astrbot.api import logger

if TYPE_CHECKING:
    from astrbot.api.event import AstrMessageEvent


class TaskStatus(Enum):
    """任务状态枚举"""

    PENDING = "pending"  # 等待中
    PROCESSING = "processing"  # 处理中
    COMPLETED = "completed"  # 已完成
    FAILED = "failed"  # 失败
    CANCELLED = "cancelled"  # 已取消
    TIMEOUT = "timeout"  # 超时


class TaskPriority(Enum):
    """任务优先级"""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


@dataclass
class DownloadTask:
    """下载任务数据类"""

    # 基本信息
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    url: str = ""
    quality: str = "alac"
    single_song: bool = True

    # 用户信息
    user_id: str = ""
    user_name: str = ""
    unified_msg_origin: str = ""  # 用于主动消息推送

    # 元数据
    song_name: Optional[str] = None
    quality_display: str = ""

    # 状态信息
    status: TaskStatus = TaskStatus.PENDING
    priority: TaskPriority = TaskPriority.NORMAL
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # 结果信息
    result: Optional[Any] = None
    error: Optional[str] = None

    # 内部控制
    _future: Optional[asyncio.Future] = field(default=None, repr=False)
    _cancelled: bool = field(default=False, repr=False)

    def __lt__(self, other: "DownloadTask") -> bool:
        """用于优先队列排序：优先级高的在前，同优先级按时间排序"""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.created_at < other.created_at

    @property
    def wait_time(self) -> float:
        """等待时间（秒）"""
        if self.started_at:
            return self.started_at - self.created_at
        return time.time() - self.created_at

    @property
    def process_time(self) -> float:
        """处理时间（秒）"""
        if not self.started_at:
            return 0
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    def cancel(self) -> bool:
        """取消任务"""
        if self.status in (TaskStatus.PENDING,):
            self._cancelled = True
            self.status = TaskStatus.CANCELLED
            if self._future and not self._future.done():
                self._future.cancel()
            return True
        return False

    def to_dict(self) -> dict:
        """转换为字典（用于状态展示）"""
        return {
            "task_id": self.task_id,
            "url": self.url[:50] + "..." if len(self.url) > 50 else self.url,
            "quality": self.quality,
            "user_name": self.user_name,
            "song_name": self.song_name,
            "status": self.status.value,
            "priority": self.priority.name,
            "wait_time": round(self.wait_time, 1),
            "process_time": round(self.process_time, 1),
        }


@dataclass
class QueueStats:
    """队列统计信息"""

    total_tasks: int = 0
    pending_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    avg_wait_time: float = 0.0
    avg_process_time: float = 0.0
    current_task: Optional[DownloadTask] = None


# 回调类型定义
TaskCallback = Callable[[DownloadTask], Awaitable[None]]
DownloadHandler = Callable[[DownloadTask], Awaitable[Any]]


class DownloadQueue:
    """
    异步下载队列管理器

    功能特性：
    1. FIFO 队列，支持优先级
    2. 并发控制（单任务执行）
    3. 任务超时保护
    4. 任务取消支持
    5. 回调通知机制
    6. 完整的状态追踪
    """

    def __init__(
        self,
        max_queue_size: int = 20,
        task_timeout: int = 600,
        queue_timeout: int = 300,
    ):
        """
        初始化队列管理器

        Args:
            max_queue_size: 最大队列长度
            task_timeout: 单个任务超时时间（秒）
            queue_timeout: 队列等待超时时间（秒）
        """
        self._max_queue_size = max_queue_size
        self._task_timeout = task_timeout
        self._queue_timeout = queue_timeout

        # 队列存储
        self._pending_queue: deque[DownloadTask] = deque()
        self._current_task: Optional[DownloadTask] = None
        self._task_history: deque[DownloadTask] = deque(maxlen=100)  # 保留最近100条历史

        # 任务索引（快速查找）
        self._tasks_by_id: Dict[str, DownloadTask] = {}
        self._tasks_by_user: Dict[str, List[str]] = {}  # user_id -> [task_id]

        # 并发控制
        self._lock = asyncio.Lock()
        self._processing = False
        self._processor_task: Optional[asyncio.Task] = None

        # 回调函数
        self._on_task_start: Optional[TaskCallback] = None
        self._on_task_complete: Optional[TaskCallback] = None
        self._on_task_failed: Optional[TaskCallback] = None
        self._on_queue_position_changed: Optional[TaskCallback] = None
        self._download_handler: Optional[DownloadHandler] = None

        # 统计信息
        self._total_completed = 0
        self._total_failed = 0
        self._total_wait_time = 0.0
        self._total_process_time = 0.0

    # ==================== 属性 ====================

    @property
    def queue_size(self) -> int:
        """当前队列长度"""
        return len(self._pending_queue)

    @property
    def is_full(self) -> bool:
        """队列是否已满"""
        return len(self._pending_queue) >= self._max_queue_size

    @property
    def is_empty(self) -> bool:
        """队列是否为空"""
        return len(self._pending_queue) == 0

    @property
    def is_processing(self) -> bool:
        """是否正在处理任务"""
        return self._current_task is not None

    @property
    def current_task(self) -> Optional[DownloadTask]:
        """当前正在处理的任务"""
        return self._current_task

    # ==================== 回调注册 ====================

    def set_download_handler(self, handler: DownloadHandler) -> None:
        """设置下载处理函数"""
        self._download_handler = handler

    def on_task_start(self, callback: TaskCallback) -> None:
        """注册任务开始回调"""
        self._on_task_start = callback

    def on_task_complete(self, callback: TaskCallback) -> None:
        """注册任务完成回调"""
        self._on_task_complete = callback

    def on_task_failed(self, callback: TaskCallback) -> None:
        """注册任务失败回调"""
        self._on_task_failed = callback

    def on_queue_position_changed(self, callback: TaskCallback) -> None:
        """注册队列位置变化回调"""
        self._on_queue_position_changed = callback

    # ==================== 任务管理 ====================

    async def enqueue(
        self,
        url: str,
        quality: str,
        user_id: str,
        user_name: str,
        unified_msg_origin: str,
        song_name: Optional[str] = None,
        quality_display: str = "",
        priority: TaskPriority = TaskPriority.NORMAL,
        single_song: bool = True,
    ) -> tuple[bool, str, Optional[DownloadTask]]:
        """
        添加任务到队列

        Returns:
            (成功标志, 消息, 任务对象)
        """
        async with self._lock:
            # 检查队列是否已满
            if self.is_full:
                return (
                    False,
                    f"队列已满（最大 {self._max_queue_size} 个任务），请稍后再试",
                    None,
                )

            # 检查是否有相同用户的重复任务
            user_tasks = self._tasks_by_user.get(user_id, [])
            for task_id in user_tasks:
                task = self._tasks_by_id.get(task_id)
                if (
                    task
                    and task.url == url
                    and task.quality == quality
                    and task.status == TaskStatus.PENDING
                ):
                    return False, f"您已有相同的下载任务在队列中（ID: {task_id}）", task

            # 创建任务
            task = DownloadTask(
                url=url,
                quality=quality,
                single_song=single_song,
                user_id=user_id,
                user_name=user_name,
                unified_msg_origin=unified_msg_origin,
                song_name=song_name,
                quality_display=quality_display,
                priority=priority,
            )
            task._future = asyncio.get_event_loop().create_future()

            # 添加到队列
            self._pending_queue.append(task)
            self._tasks_by_id[task.task_id] = task

            # 更新用户任务索引
            if user_id not in self._tasks_by_user:
                self._tasks_by_user[user_id] = []
            self._tasks_by_user[user_id].append(task.task_id)

            # 按优先级排序
            self._sort_queue()

            position = self.get_position(task.task_id)
            logger.info(
                f"[Queue] 任务入队: {task.task_id}, 位置: {position}, 用户: {user_name}"
            )

            return True, f"已加入队列，位置：第 {position} 位", task

    def _sort_queue(self) -> None:
        """按优先级排序队列"""
        sorted_tasks = sorted(
            self._pending_queue, key=lambda t: (-t.priority.value, t.created_at)
        )
        self._pending_queue = deque(sorted_tasks)

    def get_position(self, task_id: str) -> int:
        """获取任务在队列中的位置（1-based）"""
        for i, task in enumerate(self._pending_queue):
            if task.task_id == task_id:
                return i + 1
        return -1

    def get_task(self, task_id: str) -> Optional[DownloadTask]:
        """根据 ID 获取任务"""
        return self._tasks_by_id.get(task_id)

    def get_user_tasks(self, user_id: str) -> List[DownloadTask]:
        """获取用户的所有任务"""
        task_ids = self._tasks_by_user.get(user_id, [])
        return [self._tasks_by_id[tid] for tid in task_ids if tid in self._tasks_by_id]

    async def cancel_task(
        self, task_id: str, user_id: Optional[str] = None
    ) -> tuple[bool, str]:
        """
        取消任务

        Args:
            task_id: 任务 ID
            user_id: 用户 ID（如果提供，则验证权限）

        Returns:
            (成功标志, 消息)
        """
        async with self._lock:
            task = self._tasks_by_id.get(task_id)

            if not task:
                return False, f"任务不存在: {task_id}"

            # 权限检查
            if user_id and task.user_id != user_id:
                return False, "无权取消他人的任务"

            # 检查任务状态
            if task.status == TaskStatus.PROCESSING:
                return False, "任务正在处理中，无法取消"

            if task.status != TaskStatus.PENDING:
                return False, f"任务已{task.status.value}，无法取消"

            # 执行取消
            if task.cancel():
                # 从队列中移除
                if task in self._pending_queue:
                    self._pending_queue.remove(task)

                # 移入历史
                self._task_history.append(task)

                # 通知队列中其他任务位置变化
                await self._notify_position_changes()

                logger.info(f"[Queue] 任务已取消: {task_id}")
                return True, f"任务 {task_id} 已取消"

            return False, "取消失败"

    async def cancel_user_tasks(self, user_id: str) -> tuple[int, str]:
        """取消用户所有等待中的任务"""
        cancelled_count = 0
        task_ids = self._tasks_by_user.get(user_id, []).copy()

        for task_id in task_ids:
            success, _ = await self.cancel_task(task_id, user_id)
            if success:
                cancelled_count += 1

        if cancelled_count > 0:
            return cancelled_count, f"已取消 {cancelled_count} 个任务"
        return 0, "没有可取消的任务"

    # ==================== 队列处理 ====================

    async def start_processor(self) -> None:
        """启动队列处理器"""
        if self._processing:
            logger.warning("[Queue] 处理器已在运行")
            return

        self._processing = True
        self._processor_task = asyncio.create_task(self._process_loop())
        logger.info("[Queue] 队列处理器已启动")

    async def stop_processor(self) -> None:
        """停止队列处理器"""
        self._processing = False
        if self._processor_task and not self._processor_task.done():
            self._processor_task.cancel()
            try:
                await self._processor_task
            except asyncio.CancelledError:
                pass
        logger.info("[Queue] 队列处理器已停止")

    async def _process_loop(self) -> None:
        """队列处理循环"""
        while self._processing:
            try:
                # 获取下一个任务
                task = await self._get_next_task()
                if not task:
                    await asyncio.sleep(0.5)
                    continue

                # 处理任务
                await self._process_task(task)

            except asyncio.CancelledError:
                logger.info("[Queue] 处理循环被取消")
                break
            except Exception as e:
                logger.error(f"[Queue] 处理循环异常: {e}")
                await asyncio.sleep(1)

    async def _get_next_task(self) -> Optional[DownloadTask]:
        """获取下一个待处理任务"""
        async with self._lock:
            while self._pending_queue:
                task = self._pending_queue[0]

                # 检查是否已取消
                if task._cancelled or task.status == TaskStatus.CANCELLED:
                    self._pending_queue.popleft()
                    continue

                # 检查是否超时
                if time.time() - task.created_at > self._queue_timeout:
                    task.status = TaskStatus.TIMEOUT
                    task.error = "队列等待超时"
                    self._pending_queue.popleft()
                    self._task_history.append(task)

                    if self._on_task_failed:
                        await self._on_task_failed(task)

                    logger.warning(f"[Queue] 任务等待超时: {task.task_id}")
                    continue

                # 取出任务
                self._pending_queue.popleft()
                return task

            return None

    async def _process_task(self, task: DownloadTask) -> None:
        """处理单个任务"""
        task.status = TaskStatus.PROCESSING
        task.started_at = time.time()
        self._current_task = task

        logger.info(f"[Queue] 开始处理任务: {task.task_id}, 用户: {task.user_name}")

        # 通知任务开始
        if self._on_task_start:
            try:
                await self._on_task_start(task)
            except Exception as e:
                logger.warning(f"[Queue] 任务开始回调异常: {e}")

        # 通知其他任务位置变化
        await self._notify_position_changes()

        try:
            # 执行下载
            if self._download_handler:
                result = await asyncio.wait_for(
                    self._download_handler(task),
                    timeout=self._task_timeout,
                )
                task.result = result
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()

                # 更新统计
                self._total_completed += 1
                self._total_wait_time += task.wait_time
                self._total_process_time += task.process_time

                logger.info(
                    f"[Queue] 任务完成: {task.task_id}, 耗时: {task.process_time:.1f}s"
                )

                # 通知任务完成
                if self._on_task_complete:
                    await self._on_task_complete(task)

                # 设置 Future 结果
                if task._future and not task._future.done():
                    task._future.set_result(result)

            else:
                raise RuntimeError("未设置下载处理函数")

        except asyncio.TimeoutError:
            task.status = TaskStatus.TIMEOUT
            task.error = f"下载超时（{self._task_timeout}秒）"
            task.completed_at = time.time()
            self._total_failed += 1

            logger.error(f"[Queue] 任务超时: {task.task_id}")

            if self._on_task_failed:
                await self._on_task_failed(task)

            if task._future and not task._future.done():
                task._future.set_exception(TimeoutError(task.error))

        except asyncio.CancelledError:
            task.status = TaskStatus.CANCELLED
            task.error = "任务被取消"
            task.completed_at = time.time()

            logger.info(f"[Queue] 任务被取消: {task.task_id}")

            if task._future and not task._future.done():
                task._future.cancel()

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            task.completed_at = time.time()
            self._total_failed += 1

            logger.error(f"[Queue] 任务失败: {task.task_id}, 错误: {e}")

            if self._on_task_failed:
                await self._on_task_failed(task)

            if task._future and not task._future.done():
                task._future.set_exception(e)

        finally:
            self._current_task = None
            self._task_history.append(task)

            # 清理用户任务索引中的旧任务
            if task.user_id in self._tasks_by_user:
                if task.task_id in self._tasks_by_user[task.user_id]:
                    self._tasks_by_user[task.user_id].remove(task.task_id)

    async def _notify_position_changes(self) -> None:
        """通知队列中所有任务位置变化"""
        if not self._on_queue_position_changed:
            return

        for task in self._pending_queue:
            try:
                await self._on_queue_position_changed(task)
            except Exception as e:
                logger.warning(f"[Queue] 位置变化通知失败: {e}")

    # ==================== 等待任务完成 ====================

    async def wait_for_task(
        self,
        task_id: str,
        timeout: Optional[float] = None,
    ) -> Optional[Any]:
        """
        等待任务完成并返回结果

        Args:
            task_id: 任务 ID
            timeout: 超时时间（秒），None 表示使用默认超时

        Returns:
            任务结果，超时或取消时返回 None
        """
        task = self._tasks_by_id.get(task_id)
        if not task or not task._future:
            return None

        effective_timeout = timeout or (self._queue_timeout + self._task_timeout)

        try:
            return await asyncio.wait_for(task._future, timeout=effective_timeout)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            return None

    # ==================== 状态查询 ====================

    def get_queue_status(self) -> dict:
        """获取队列状态"""
        return {
            "queue_size": self.queue_size,
            "max_size": self._max_queue_size,
            "is_processing": self.is_processing,
            "current_task": self._current_task.to_dict()
            if self._current_task
            else None,
            "pending_tasks": [t.to_dict() for t in self._pending_queue],
            "stats": self.get_stats().__dict__,
        }

    def get_stats(self) -> QueueStats:
        """获取队列统计信息"""
        total = self._total_completed + self._total_failed
        return QueueStats(
            total_tasks=total,
            pending_tasks=self.queue_size,
            completed_tasks=self._total_completed,
            failed_tasks=self._total_failed,
            cancelled_tasks=sum(
                1 for t in self._task_history if t.status == TaskStatus.CANCELLED
            ),
            avg_wait_time=self._total_wait_time / max(total, 1),
            avg_process_time=self._total_process_time / max(self._total_completed, 1),
            current_task=self._current_task,
        )

    def format_queue_status(self) -> str:
        """格式化队列状态为可读字符串"""
        lines = ["* 下载队列状态", "─" * 25]

        # 当前任务（不显示歌曲名以保护隐私）
        if self._current_task:
            task = self._current_task
            lines.append(f"▶ 正在下载: {task.user_name}")
            lines.append(f"   音质: {task.quality_display or task.quality}")
            lines.append(f"   耗时: {task.process_time:.0f}s")
        else:
            lines.append("○ 当前无下载任务")

        # 等待队列（不显示歌曲名以保护隐私）
        if self._pending_queue:
            lines.append(
                f"\n等待队列 ({len(self._pending_queue)}/{self._max_queue_size}):"
            )
            for i, task in enumerate(list(self._pending_queue)[:5]):
                wait_time = int(task.wait_time)
                lines.append(f"  {i + 1}. {task.user_name} (等待 {wait_time}s)")

            if len(self._pending_queue) > 5:
                lines.append(f"  ... 还有 {len(self._pending_queue) - 5} 个任务")
        else:
            lines.append("\n○ 等待队列为空")

        # 统计信息
        stats = self.get_stats()
        if stats.total_tasks > 0:
            lines.extend(
                [
                    "",
                    f"统计: 完成 {stats.completed_tasks} / 失败 {stats.failed_tasks}",
                    f"平均等待: {stats.avg_wait_time:.1f}s / 处理: {stats.avg_process_time:.1f}s",
                ]
            )

        return "\n".join(lines)

    # ==================== 清理 ====================

    async def clear_queue(self) -> int:
        """清空等待队列（不影响正在处理的任务）"""
        async with self._lock:
            count = len(self._pending_queue)

            for task in self._pending_queue:
                task.status = TaskStatus.CANCELLED
                task.error = "队列被清空"
                self._task_history.append(task)

                if task._future and not task._future.done():
                    task._future.cancel()

            self._pending_queue.clear()
            logger.info(f"[Queue] 队列已清空，取消 {count} 个任务")

            return count

    def cleanup_old_tasks(self, max_age: float = 3600) -> int:
        """清理历史任务索引（内存优化）"""
        now = time.time()
        cleaned = 0

        # 清理任务索引
        to_remove = []
        for task_id, task in self._tasks_by_id.items():
            if task.status not in (TaskStatus.PENDING, TaskStatus.PROCESSING):
                if task.completed_at and now - task.completed_at > max_age:
                    to_remove.append(task_id)

        for task_id in to_remove:
            del self._tasks_by_id[task_id]
            cleaned += 1

        # 清理用户任务索引
        for user_id in list(self._tasks_by_user.keys()):
            self._tasks_by_user[user_id] = [
                tid for tid in self._tasks_by_user[user_id] if tid in self._tasks_by_id
            ]
            if not self._tasks_by_user[user_id]:
                del self._tasks_by_user[user_id]

        if cleaned > 0:
            logger.debug(f"[Queue] 清理 {cleaned} 个历史任务索引")

        return cleaned
