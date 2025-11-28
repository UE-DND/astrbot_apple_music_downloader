"""
Download Task and State Machine


Implements:
- DownloadTask: Immutable task data container
- TaskStatus: Task lifecycle states
- TaskStateMachine: Ensures valid state transitions
"""

from __future__ import annotations
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional, Any, Set, FrozenSet


class TaskStatus(Enum):
    """Task lifecycle states."""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"

    @property
    def is_terminal(self) -> bool:
        """Check if this is a terminal state."""
        return self in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.TIMEOUT,
        )

    @property
    def is_active(self) -> bool:
        """Check if task is still active."""
        return self in (TaskStatus.PENDING, TaskStatus.PROCESSING)


class TaskPriority(Enum):
    """Task priority levels."""
    LOW = 0
    NORMAL = 1
    HIGH = 2
    URGENT = 3


class TaskStateMachine:
    """
    Ensures valid state transitions for tasks.

    State Diagram:
        PENDING -> PROCESSING -> COMPLETED
                             -> FAILED
                             -> TIMEOUT
                             -> CANCELLED
        PENDING -> CANCELLED
        PENDING -> TIMEOUT
    """

    # Valid transitions: from_state -> {allowed_to_states}
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
        # Terminal states have no valid transitions
        TaskStatus.COMPLETED: frozenset(),
        TaskStatus.FAILED: frozenset(),
        TaskStatus.CANCELLED: frozenset(),
        TaskStatus.TIMEOUT: frozenset(),
    }

    @classmethod
    def can_transition(cls, from_status: TaskStatus, to_status: TaskStatus) -> bool:
        """Check if transition is valid."""
        allowed = cls._TRANSITIONS.get(from_status, frozenset())
        return to_status in allowed

    @classmethod
    def validate_transition(cls, from_status: TaskStatus, to_status: TaskStatus) -> None:
        """Validate transition, raise if invalid."""
        if not cls.can_transition(from_status, to_status):
            raise InvalidStateTransitionError(
                f"Invalid state transition: {from_status.value} -> {to_status.value}"
            )

    @classmethod
    def get_allowed_transitions(cls, status: TaskStatus) -> FrozenSet[TaskStatus]:
        """Get all allowed transitions from a status."""
        return cls._TRANSITIONS.get(status, frozenset())


class InvalidStateTransitionError(Exception):
    """Raised when attempting an invalid state transition."""
    pass


@dataclass
class DownloadTask:
    """
    Download task data container.

    Responsibilities:
    - Hold task data (immutable after creation except status)
    - Track timing information
    - Provide serialization for display

    Note: Status transitions should go through TaskStateMachine.
    """

    # Unique identifier
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])

    # Download parameters
    url: str = ""
    quality: str = "alac"
    quality_display: str = ""

    # User information
    user_id: str = ""
    user_name: str = ""
    unified_msg_origin: str = ""

    # Metadata (optional, fetched before download)
    song_name: Optional[str] = None

    # Task configuration
    priority: TaskPriority = TaskPriority.NORMAL

    # State tracking (managed by TaskStateMachine)
    _status: TaskStatus = field(default=TaskStatus.PENDING, repr=False)

    # Timing
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None

    # Result
    result: Optional[Any] = field(default=None, repr=False)
    error: Optional[str] = None

    # Internal: Future for await support
    _future: Optional[asyncio.Future] = field(default=None, repr=False, compare=False)

    # ==================== Status Management ====================

    @property
    def status(self) -> TaskStatus:
        """Get current status."""
        return self._status

    def transition_to(self, new_status: TaskStatus) -> None:
        """
        Transition to a new status with validation.

        Raises:
            InvalidStateTransitionError: If transition is not allowed.
        """
        TaskStateMachine.validate_transition(self._status, new_status)
        self._status = new_status

        # Update timing based on new status
        if new_status == TaskStatus.PROCESSING:
            self.started_at = time.time()
        elif new_status.is_terminal:
            self.completed_at = time.time()

    def try_transition_to(self, new_status: TaskStatus) -> bool:
        """
        Try to transition to a new status.

        Returns:
            True if transition succeeded, False otherwise.
        """
        if TaskStateMachine.can_transition(self._status, new_status):
            self.transition_to(new_status)
            return True
        return False

    # ==================== Timing Properties ====================

    @property
    def wait_time(self) -> float:
        """Time spent waiting in queue (seconds)."""
        if self.started_at:
            return self.started_at - self.created_at
        return time.time() - self.created_at

    @property
    def process_time(self) -> float:
        """Time spent processing (seconds)."""
        if not self.started_at:
            return 0.0
        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    @property
    def total_time(self) -> float:
        """Total time from creation to completion (seconds)."""
        if not self.completed_at:
            return time.time() - self.created_at
        return self.completed_at - self.created_at

    # ==================== Convenience Properties ====================

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

    # ==================== Comparison (for priority queue) ====================

    def __lt__(self, other: DownloadTask) -> bool:
        """
        Compare for priority queue sorting.
        Higher priority first, then earlier creation time.
        """
        if self.priority.value != other.priority.value:
            return self.priority.value > other.priority.value
        return self.created_at < other.created_at

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, DownloadTask):
            return NotImplemented
        return self.task_id == other.task_id

    def __hash__(self) -> int:
        return hash(self.task_id)

    # ==================== Serialization ====================

    def to_dict(self) -> dict:
        """Convert to dictionary for display/API."""
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
        """Truncate URL for display."""
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
