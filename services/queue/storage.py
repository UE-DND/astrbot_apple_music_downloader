"""
任务队列数据结构。
提供优先级排序与索引能力。
"""

from __future__ import annotations
import asyncio
import heapq
from typing import Optional, List, Dict, Iterator, Callable
from abc import ABC, abstractmethod

from .task import DownloadTask, TaskStatus, TaskPriority


class PriorityStrategy(ABC):
    """队列排序策略抽象基类。"""

    @abstractmethod
    def compare(self, task1: DownloadTask, task2: DownloadTask) -> int:
        """比较两个任务的排序优先级。"""
        pass

    @abstractmethod
    def sort_key(self, task: DownloadTask) -> tuple:
        """获取任务排序键。"""
        pass


class FIFOWithPriorityStrategy(PriorityStrategy):
    """基于优先级的 FIFO 排序策略。"""

    def compare(self, task1: DownloadTask, task2: DownloadTask) -> int:
        if task1.priority.value != task2.priority.value:
            return task2.priority.value - task1.priority.value
        if task1.created_at != task2.created_at:
            return -1 if task1.created_at < task2.created_at else 1
        return 0

    def sort_key(self, task: DownloadTask) -> tuple:
        return (-task.priority.value, task.created_at)


class TaskQueue:
    """线程安全的下载任务优先队列。"""

    def __init__(
        self,
        max_size: int = 20,
        strategy: Optional[PriorityStrategy] = None
    ):
        """初始化任务队列。"""
        self._max_size = max_size
        self._strategy = strategy or FIFOWithPriorityStrategy()

        self._tasks: List[DownloadTask] = []

        self._by_id: Dict[str, DownloadTask] = {}
        self._by_user: Dict[str, List[str]] = {}  # 用户ID -> [任务ID]

        self._lock = asyncio.Lock()


    @property
    def max_size(self) -> int:
        """队列最大容量。"""
        return self._max_size

    def __len__(self) -> int:
        """当前队列长度。"""
        return len(self._tasks)

    @property
    def is_empty(self) -> bool:
        """检查队列是否为空。"""
        return len(self._tasks) == 0

    @property
    def is_full(self) -> bool:
        """检查队列是否已满。"""
        return len(self._tasks) >= self._max_size


    async def push(self, task: DownloadTask) -> tuple[bool, str]:
        """将任务加入队列。"""
        async with self._lock:
            if self.is_full:
                return False, f"队列已满（最大 {self._max_size} 个任务）"

            if task.task_id in self._by_id:
                return False, f"任务已存在: {task.task_id}"

            if self.has_duplicate_unlocked(task.user_id, task.url):
                existing = self._find_duplicate_unlocked(task.user_id, task.url)
                return False, f"您已有相同的下载任务在队列中（ID: {existing.task_id}）"

            self._tasks.append(task)
            self._sort()

            self._by_id[task.task_id] = task
            if task.user_id not in self._by_user:
                self._by_user[task.user_id] = []
            self._by_user[task.user_id].append(task.task_id)

            position = self._get_position_unlocked(task.task_id)
            return True, f"已加入队列，位置：第 {position} 位"

    async def pop(self) -> Optional[DownloadTask]:
        """取出最高优先级任务。"""
        async with self._lock:
            if not self._tasks:
                return None

            task = self._tasks.pop(0)

            del self._by_id[task.task_id]
            if task.user_id in self._by_user:
                try:
                    self._by_user[task.user_id].remove(task.task_id)
                except ValueError:
                    pass
                if not self._by_user[task.user_id]:
                    del self._by_user[task.user_id]

            return task

    async def peek(self) -> Optional[DownloadTask]:
        """查看最高优先级任务但不移除。"""
        async with self._lock:
            if not self._tasks:
                return None
            return self._tasks[0]

    async def remove(self, task_id: str) -> Optional[DownloadTask]:
        """按 ID 移除任务。"""
        async with self._lock:
            task = self._by_id.get(task_id)
            if not task:
                return None

            try:
                self._tasks.remove(task)
            except ValueError:
                return None

            del self._by_id[task_id]
            if task.user_id in self._by_user:
                try:
                    self._by_user[task.user_id].remove(task_id)
                except ValueError:
                    pass
                if not self._by_user[task.user_id]:
                    del self._by_user[task.user_id]

            return task


    def get(self, task_id: str) -> Optional[DownloadTask]:
        """按 ID 获取任务（只读无需锁）。"""
        return self._by_id.get(task_id)

    def get_user_tasks(self, user_id: str) -> List[DownloadTask]:
        """获取用户的全部任务。"""
        task_ids = self._by_user.get(user_id, [])
        return [self._by_id[tid] for tid in task_ids if tid in self._by_id]

    def get_position(self, task_id: str) -> int:
        """获取任务在队列中的位置（从 1 开始）。"""
        return self._get_position_unlocked(task_id)

    def _get_position_unlocked(self, task_id: str) -> int:
        """无锁获取任务位置。"""
        for i, task in enumerate(self._tasks):
            if task.task_id == task_id:
                return i + 1
        return -1

    def has_duplicate(self, user_id: str, url: str) -> bool:
        """检查用户是否存在重复待处理任务。"""
        return self.has_duplicate_unlocked(user_id, url)

    def has_duplicate_unlocked(self, user_id: str, url: str) -> bool:
        """无锁检查重复任务。"""
        task_ids = self._by_user.get(user_id, [])
        for task_id in task_ids:
            task = self._by_id.get(task_id)
            if task and task.url == url and task.is_pending:
                return True
        return False

    def _find_duplicate_unlocked(self, user_id: str, url: str) -> Optional[DownloadTask]:
        """查找重复任务。"""
        task_ids = self._by_user.get(user_id, [])
        for task_id in task_ids:
            task = self._by_id.get(task_id)
            if task and task.url == url and task.is_pending:
                return task
        return None


    def __iter__(self) -> Iterator[DownloadTask]:
        """按优先级顺序遍历任务。"""
        return iter(self._tasks.copy())

    def list_tasks(self, limit: Optional[int] = None) -> List[DownloadTask]:
        """获取任务列表。"""
        if limit is None:
            return self._tasks.copy()
        return self._tasks[:limit]


    async def clear(self) -> int:
        """清空队列任务。"""
        async with self._lock:
            count = len(self._tasks)
            self._tasks.clear()
            self._by_id.clear()
            self._by_user.clear()
            return count

    async def remove_user_tasks(self, user_id: str) -> List[DownloadTask]:
        """移除用户的全部任务。"""
        async with self._lock:
            task_ids = self._by_user.get(user_id, []).copy()
            removed = []

            for task_id in task_ids:
                task = self._by_id.get(task_id)
                if task:
                    try:
                        self._tasks.remove(task)
                        del self._by_id[task_id]
                        removed.append(task)
                    except (ValueError, KeyError):
                        pass

            if user_id in self._by_user:
                del self._by_user[user_id]

            return removed


    def _sort(self) -> None:
        """按排序策略排序任务。"""
        self._tasks.sort(key=self._strategy.sort_key)

    async def resort(self) -> None:
        """重新排序队列。"""
        async with self._lock:
            self._sort()

    def __repr__(self) -> str:
        return f"TaskQueue(size={len(self)}, max={self._max_size})"
