"""
Task Queue Data Structure


Pure queue data structure with:
- Priority-based ordering
- Task indexing by ID and user
- Thread-safe operations
"""

from __future__ import annotations
import asyncio
import heapq
from typing import Optional, List, Dict, Iterator, Callable
from abc import ABC, abstractmethod

from .task import DownloadTask, TaskStatus, TaskPriority


class PriorityStrategy(ABC):
    """Abstract base for queue ordering strategies."""

    @abstractmethod
    def compare(self, task1: DownloadTask, task2: DownloadTask) -> int:
        """
        Compare two tasks for ordering.

        Returns:
            < 0 if task1 should come before task2
            = 0 if equal priority
            > 0 if task1 should come after task2
        """
        pass

    @abstractmethod
    def sort_key(self, task: DownloadTask) -> tuple:
        """Get sort key for a task."""
        pass


class FIFOWithPriorityStrategy(PriorityStrategy):
    """
    FIFO ordering with priority levels.

    Higher priority tasks come first.
    Within same priority, earlier tasks come first (FIFO).
    """

    def compare(self, task1: DownloadTask, task2: DownloadTask) -> int:
        # Higher priority value = higher priority
        if task1.priority.value != task2.priority.value:
            return task2.priority.value - task1.priority.value
        # Earlier created = higher priority
        if task1.created_at != task2.created_at:
            return -1 if task1.created_at < task2.created_at else 1
        return 0

    def sort_key(self, task: DownloadTask) -> tuple:
        # Negated priority (so higher priority sorts first)
        # Created_at (so earlier sorts first)
        return (-task.priority.value, task.created_at)


class TaskQueue:
    """
    Thread-safe priority queue for download tasks.

    Single responsibility: manage task collection and ordering.

    Features:
    - Priority-based ordering (configurable strategy)
    - O(1) lookup by task ID
    - O(1) lookup of user's tasks
    - Thread-safe with asyncio.Lock
    - Duplicate detection

    Usage:
        queue = TaskQueue(max_size=20)

        # Add task
        success = await queue.push(task)

        # Get next task
        task = await queue.pop()

        # Check for duplicates
        if queue.has_duplicate(user_id, url):
            ...

        # Get by ID
        task = queue.get(task_id)
    """

    def __init__(
        self,
        max_size: int = 20,
        strategy: Optional[PriorityStrategy] = None
    ):
        """
        Initialize task queue.

        Args:
            max_size: Maximum number of tasks in queue
            strategy: Ordering strategy (default: FIFOWithPriorityStrategy)
        """
        self._max_size = max_size
        self._strategy = strategy or FIFOWithPriorityStrategy()

        # Primary storage: sorted list
        self._tasks: List[DownloadTask] = []

        # Indexes for fast lookup
        self._by_id: Dict[str, DownloadTask] = {}
        self._by_user: Dict[str, List[str]] = {}  # user_id -> [task_ids]

        # Thread safety
        self._lock = asyncio.Lock()

    # ==================== Properties ====================

    @property
    def max_size(self) -> int:
        """Maximum queue size."""
        return self._max_size

    def __len__(self) -> int:
        """Current queue size."""
        return len(self._tasks)

    @property
    def is_empty(self) -> bool:
        """Check if queue is empty."""
        return len(self._tasks) == 0

    @property
    def is_full(self) -> bool:
        """Check if queue is full."""
        return len(self._tasks) >= self._max_size

    # ==================== Core Operations ====================

    async def push(self, task: DownloadTask) -> tuple[bool, str]:
        """
        Add task to queue.

        Args:
            task: Task to add

        Returns:
            (success, message)
        """
        async with self._lock:
            # Check capacity
            if self.is_full:
                return False, f"队列已满（最大 {self._max_size} 个任务）"

            # Check duplicate
            if task.task_id in self._by_id:
                return False, f"任务已存在: {task.task_id}"

            # Check duplicate URL for same user
            if self.has_duplicate_unlocked(task.user_id, task.url):
                existing = self._find_duplicate_unlocked(task.user_id, task.url)
                return False, f"您已有相同的下载任务在队列中（ID: {existing.task_id}）"

            # Add to sorted list
            self._tasks.append(task)
            self._sort()

            # Update indexes
            self._by_id[task.task_id] = task
            if task.user_id not in self._by_user:
                self._by_user[task.user_id] = []
            self._by_user[task.user_id].append(task.task_id)

            position = self._get_position_unlocked(task.task_id)
            return True, f"已加入队列，位置：第 {position} 位"

    async def pop(self) -> Optional[DownloadTask]:
        """
        Remove and return the highest priority task.

        Returns:
            Next task, or None if queue is empty
        """
        async with self._lock:
            if not self._tasks:
                return None

            task = self._tasks.pop(0)

            # Update indexes
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
        """
        Return the highest priority task without removing it.

        Returns:
            Next task, or None if queue is empty
        """
        async with self._lock:
            if not self._tasks:
                return None
            return self._tasks[0]

    async def remove(self, task_id: str) -> Optional[DownloadTask]:
        """
        Remove a specific task by ID.

        Args:
            task_id: ID of task to remove

        Returns:
            Removed task, or None if not found
        """
        async with self._lock:
            task = self._by_id.get(task_id)
            if not task:
                return None

            # Remove from list
            try:
                self._tasks.remove(task)
            except ValueError:
                return None

            # Update indexes
            del self._by_id[task_id]
            if task.user_id in self._by_user:
                try:
                    self._by_user[task.user_id].remove(task_id)
                except ValueError:
                    pass
                if not self._by_user[task.user_id]:
                    del self._by_user[task.user_id]

            return task

    # ==================== Lookup Operations ====================

    def get(self, task_id: str) -> Optional[DownloadTask]:
        """
        Get task by ID (no lock needed for read).

        Args:
            task_id: Task ID

        Returns:
            Task or None
        """
        return self._by_id.get(task_id)

    def get_user_tasks(self, user_id: str) -> List[DownloadTask]:
        """
        Get all tasks for a user.

        Args:
            user_id: User ID

        Returns:
            List of user's tasks
        """
        task_ids = self._by_user.get(user_id, [])
        return [self._by_id[tid] for tid in task_ids if tid in self._by_id]

    def get_position(self, task_id: str) -> int:
        """
        Get task position in queue (1-based).

        Args:
            task_id: Task ID

        Returns:
            Position (1-based), or -1 if not found
        """
        return self._get_position_unlocked(task_id)

    def _get_position_unlocked(self, task_id: str) -> int:
        """Get position without lock."""
        for i, task in enumerate(self._tasks):
            if task.task_id == task_id:
                return i + 1
        return -1

    def has_duplicate(self, user_id: str, url: str) -> bool:
        """
        Check if user has duplicate pending task for URL.

        Args:
            user_id: User ID
            url: Download URL

        Returns:
            True if duplicate exists
        """
        return self.has_duplicate_unlocked(user_id, url)

    def has_duplicate_unlocked(self, user_id: str, url: str) -> bool:
        """Check duplicate without lock."""
        task_ids = self._by_user.get(user_id, [])
        for task_id in task_ids:
            task = self._by_id.get(task_id)
            if task and task.url == url and task.is_pending:
                return True
        return False

    def _find_duplicate_unlocked(self, user_id: str, url: str) -> Optional[DownloadTask]:
        """Find duplicate task."""
        task_ids = self._by_user.get(user_id, [])
        for task_id in task_ids:
            task = self._by_id.get(task_id)
            if task and task.url == url and task.is_pending:
                return task
        return None

    # ==================== Iteration ====================

    def __iter__(self) -> Iterator[DownloadTask]:
        """Iterate over tasks in priority order."""
        return iter(self._tasks.copy())

    def list_tasks(self, limit: Optional[int] = None) -> List[DownloadTask]:
        """
        Get list of tasks.

        Args:
            limit: Maximum number of tasks to return

        Returns:
            List of tasks in priority order
        """
        if limit is None:
            return self._tasks.copy()
        return self._tasks[:limit]

    # ==================== Bulk Operations ====================

    async def clear(self) -> int:
        """
        Clear all tasks from queue.

        Returns:
            Number of tasks cleared
        """
        async with self._lock:
            count = len(self._tasks)
            self._tasks.clear()
            self._by_id.clear()
            self._by_user.clear()
            return count

    async def remove_user_tasks(self, user_id: str) -> List[DownloadTask]:
        """
        Remove all tasks for a user.

        Args:
            user_id: User ID

        Returns:
            List of removed tasks
        """
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

    # ==================== Internal ====================

    def _sort(self) -> None:
        """Sort tasks by priority strategy."""
        self._tasks.sort(key=self._strategy.sort_key)

    async def resort(self) -> None:
        """Re-sort queue (call after priority changes)."""
        async with self._lock:
            self._sort()

    def __repr__(self) -> str:
        return f"TaskQueue(size={len(self)}, max={self._max_size})"
