"""
Queue Statistics Collector


Collects and computes statistics for the download queue.
Single responsibility: statistics aggregation and reporting.
"""

from __future__ import annotations
import time
from dataclasses import dataclass, field
from typing import Optional, List, TYPE_CHECKING
from collections import deque

if TYPE_CHECKING:
    from .task import DownloadTask, TaskStatus


@dataclass
class QueueStats:
    """
    Snapshot of queue statistics.

    This is a read-only data class representing queue state at a point in time.
    """
    # Counts
    total_tasks: int = 0
    pending_tasks: int = 0
    processing_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    timeout_tasks: int = 0

    # Timing averages
    avg_wait_time: float = 0.0
    avg_process_time: float = 0.0

    # Current state
    queue_size: int = 0
    max_queue_size: int = 0

    # Throughput (tasks per minute)
    throughput: float = 0.0

    # Success rate (0.0 - 1.0)
    @property
    def success_rate(self) -> float:
        """Calculate success rate."""
        total = self.completed_tasks + self.failed_tasks + self.timeout_tasks
        if total == 0:
            return 0.0
        return self.completed_tasks / total


@dataclass
class TaskTiming:
    """Timing data for a single task."""
    task_id: str
    wait_time: float
    process_time: float
    completed_at: float
    success: bool


class QueueStatsCollector:
    """
    Collects and computes queue statistics.

    Features:
    - Real-time statistics collection
    - Rolling window for throughput calculation
    - Memory-efficient with configurable history size
    - Thread-safe design

    Usage:
        collector = QueueStatsCollector(max_history=1000)

        # Record task completion
        collector.record_completion(task)
        collector.record_failure(task)

        # Get statistics
        stats = collector.get_stats(pending_count=5, queue_size=10, max_size=20)
    """

    def __init__(
        self,
        max_history: int = 1000,
        throughput_window: float = 300.0  # 5 minutes
    ):
        """
        Initialize statistics collector.

        Args:
            max_history: Maximum number of task timings to keep
            throughput_window: Time window (seconds) for throughput calculation
        """
        self._max_history = max_history
        self._throughput_window = throughput_window

        # Task timing history (for averages and throughput)
        self._timings: deque[TaskTiming] = deque(maxlen=max_history)

        # Counters (never reset)
        self._total_completed = 0
        self._total_failed = 0
        self._total_cancelled = 0
        self._total_timeout = 0

        # Aggregates for averages
        self._total_wait_time = 0.0
        self._total_process_time = 0.0

    def record_completion(self, task: DownloadTask) -> None:
        """
        Record a successful task completion.

        Args:
            task: The completed task
        """
        timing = TaskTiming(
            task_id=task.task_id,
            wait_time=task.wait_time,
            process_time=task.process_time,
            completed_at=time.time(),
            success=True
        )
        self._timings.append(timing)

        self._total_completed += 1
        self._total_wait_time += task.wait_time
        self._total_process_time += task.process_time

    def record_failure(self, task: DownloadTask, reason: str = "failed") -> None:
        """
        Record a failed task.

        Args:
            task: The failed task
            reason: Failure reason ("failed", "timeout", "cancelled")
        """
        timing = TaskTiming(
            task_id=task.task_id,
            wait_time=task.wait_time,
            process_time=task.process_time,
            completed_at=time.time(),
            success=False
        )
        self._timings.append(timing)

        if reason == "timeout":
            self._total_timeout += 1
        elif reason == "cancelled":
            self._total_cancelled += 1
        else:
            self._total_failed += 1

        self._total_wait_time += task.wait_time
        self._total_process_time += task.process_time

    def get_stats(
        self,
        pending_count: int = 0,
        processing_count: int = 0,
        queue_size: int = 0,
        max_queue_size: int = 0
    ) -> QueueStats:
        """
        Get current statistics snapshot.

        Args:
            pending_count: Current number of pending tasks
            processing_count: Current number of processing tasks
            queue_size: Current queue size
            max_queue_size: Maximum queue size

        Returns:
            QueueStats snapshot
        """
        total_tasks = (
            self._total_completed +
            self._total_failed +
            self._total_cancelled +
            self._total_timeout
        )

        # Calculate averages
        avg_wait = 0.0
        avg_process = 0.0
        if total_tasks > 0:
            avg_wait = self._total_wait_time / total_tasks
        if self._total_completed > 0:
            avg_process = self._total_process_time / self._total_completed

        return QueueStats(
            total_tasks=total_tasks,
            pending_tasks=pending_count,
            processing_tasks=processing_count,
            completed_tasks=self._total_completed,
            failed_tasks=self._total_failed,
            cancelled_tasks=self._total_cancelled,
            timeout_tasks=self._total_timeout,
            avg_wait_time=avg_wait,
            avg_process_time=avg_process,
            queue_size=queue_size,
            max_queue_size=max_queue_size,
            throughput=self._calculate_throughput()
        )

    def _calculate_throughput(self) -> float:
        """
        Calculate throughput (successful tasks per minute) over the window.

        Returns:
            Tasks per minute
        """
        if not self._timings:
            return 0.0

        now = time.time()
        window_start = now - self._throughput_window

        # Count successful tasks in window
        successful_in_window = sum(
            1 for t in self._timings
            if t.completed_at >= window_start and t.success
        )

        # Convert to per-minute rate
        window_minutes = self._throughput_window / 60.0
        return successful_in_window / window_minutes

    def get_recent_timings(self, count: int = 10) -> List[TaskTiming]:
        """
        Get recent task timings.

        Args:
            count: Number of recent timings to return

        Returns:
            List of recent TaskTiming objects
        """
        return list(self._timings)[-count:]

    def reset(self) -> None:
        """Reset all statistics."""
        self._timings.clear()
        self._total_completed = 0
        self._total_failed = 0
        self._total_cancelled = 0
        self._total_timeout = 0
        self._total_wait_time = 0.0
        self._total_process_time = 0.0

    def __repr__(self) -> str:
        return (
            f"QueueStatsCollector("
            f"completed={self._total_completed}, "
            f"failed={self._total_failed}, "
            f"history_size={len(self._timings)})"
        )
