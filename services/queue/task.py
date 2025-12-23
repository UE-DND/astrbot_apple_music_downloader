"""
下载任务与状态机。
包含任务数据与状态流转规则。
"""

from __future__ import annotations
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Set, FrozenSet


class TaskStatus(Enum):
    """任务生命周期状态。"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    @property
    def is_terminal(self) -> bool:
        """判断是否为终态。"""
        return self in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        )

    @property
    def is_active(self) -> bool:
        """判断任务是否仍处于活跃状态。"""
        return self in (TaskStatus.PENDING, TaskStatus.PROCESSING)


class TaskPriority(Enum):
    """任务优先级。"""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


class TaskStateMachine:
    """任务状态流转校验器。"""

    _TRANSITIONS: dict[TaskStatus, FrozenSet[TaskStatus]] = {
        TaskStatus.PENDING: frozenset({
            TaskStatus.PROCESSING,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        }),
        TaskStatus.PROCESSING: frozenset({
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.TIMEOUT,
            TaskStatus.CANCELLED,
        }),
        TaskStatus.COMPLETED: frozenset(),
        TaskStatus.FAILED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
        TaskStatus.TIMEOUT: frozenset(),
    }

    @classmethod
    def can_transition(cls, from_status: TaskStatus, to_status: TaskStatus) -> bool:
        """检查状态流转是否合法。"""
        allowed = cls._TRANSITIONS.get(from_status, frozenset())
        return to_status in allowed

    @classmethod
    def validate_transition(cls, from_status: TaskStatus, to_status: TaskStatus) -> None:
        """校验状态流转，非法则抛错。"""
        if not cls.can_transition(from_status, to_status):
            raise InvalidStateTransitionError(
                f"Invalid state transition: {from_status.value} -> {to_status.value}"
            )

    @classmethod
    def get_allowed_transitions(cls, status: TaskStatus) -> FrozenSet[TaskStatus]:
        """获取指定状态允许的流转集合。"""
        return cls._TRANSITIONS.get(status, frozenset())


class InvalidStateTransitionError(Exception):
    """状态流转非法时抛出。"""
    pass


@dataclass
class DownloadTask:
    """下载任务数据容器。"""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    url: str = ""
    quality: str = "alac"
    quality_display: str = ""

    user_id: str = ""
    user_name: str = ""
    unified_msg_origin: str = ""

    song_name: Optional[str] = None

    priority: TaskPriority = TaskPriority.NORMAL

    _status: TaskStatus = field(default=TaskStatus.PENDING, repr=False)

    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    result: Optional[Any] = field(default=None, repr=False)
    error: Optional[str] = None

    _future: Optional[asyncio.Future] = field(default=None, repr=False, compare=False)


    @property
    def status(self) -> TaskStatus:
        """获取当前状态。"""
        return self._status

    def transition_to(self, new_status: TaskStatus) -> None:
        """校验后切换到新状态。"""
        TaskStateMachine.validate_transition(self._status, new_status)
        self._status = new_status

        if new_status == TaskStatus.PROCESSING:
            self.started_at = time.time()
        elif new_status.is_terminal:
            self.completed_at = time.time()

    def try_transition_to(self, new_status: TaskStatus) -> bool:
        """尝试切换状态，成功返回 True。"""
        if TaskStateMachine.can_transition(self._status, new_status):
            self.transition_to(new_status)
            return True
        return False


    @property
    def wait_time(self) -> float:
        """队列等待时长（秒）。"""
        if self.started_at:
            return self.started_at - self.created_at
        return time.time() - self.created_at

    @property
    def process_time(self) -> float:
        """处理时长（秒）。"""
        if not self.started_at:
            return 0.0
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    @property
    def total_time(self) -> float:
        """从创建到完成的总时长（秒）。"""
        if not self.completed_at:
            return time.time() - self.created_at
        return self.completed_at - self.created_at


    @property
    def is_pending(self) -> bool:
        return self._status == TaskStatus.PENDING

    @property
    def is_processing(self) -> bool:
        return self._status == TaskStatus.PROCESSING

    @property
    def is_completed(self) -> bool:
        return self._status == TaskStatus.COMPLETED

    @property
    def is_failed(self) -> bool:
        return self._status == TaskStatus.FAILED

    @property
    def is_cancelled(self) -> bool:
        return self._status == TaskStatus.CANCELLED

    @property
    def is_terminal(self) -> bool:
        return self._status.is_terminal

    @property
    def is_active(self) -> bool:
        return self._status.is_active


    def __lt__(self, other: DownloadTask) -> bool:
        """用于优先队列排序。"""
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.created_at < other.created_at

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DownloadTask):
            return NotImplemented
        return self.task_id == other.task_id

    def __hash__(self) -> int:
        return hash(self.task_id)


    def to_dict(self) -> dict:
        """转换为字典供展示/API 使用。"""
        return {
            "task_id": self.task_id,
            "url": self._truncate_url(self.url),
            "quality": self.quality,
            "quality_display": self.quality_display,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "song_name": self.song_name,
            "status": self._status.value,
            "priority": self.priority.name,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "wait_time": round(self.wait_time, 2),
            "process_time": round(self.process_time, 2),
            "error": self.error,
        }

    @staticmethod
    def _truncate_url(url: str, max_length: int = 50) -> str:
        """截断 URL 便于显示。"""
        if len(url) <= max_length:
            return url
        return url[:max_length - 3] + "..."

    def __repr__(self) -> str:
        return (
            f"DownloadTask(id={self.task_id}, "
            f"status={self._status.value}, "
            f"user={self.user_name}, "
            f"priority={self.priority.name})"
        )
