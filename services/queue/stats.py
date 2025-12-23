"""
队列统计收集器。
负责统计聚合与报告。
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
    """队列统计快照。"""
    total_tasks: int = 0
    pending_tasks: int = 0
    processing_tasks: int = 0
    completed_tasks: int = 0
    failed_tasks: int = 0
    cancelled_tasks: int = 0
    timeout_tasks: int = 0

    avg_wait_time: float = 0.0
    avg_process_time: float = 0.0

    queue_size: int = 0
    max_queue_size: int = 0

    throughput: float = 0.0

    @property
    def success_rate(self) -> float:
        """计算成功率。"""
        total = self.completed_tasks + self.failed_tasks + self.timeout_tasks
        if total == 0:
            return 0.0
        return self.completed_tasks / total


@dataclass
class TaskTiming:
    """单个任务的时间统计。"""
    task_id: str
    wait_time: float
    process_time: float
    completed_at: float
    success: bool


class QueueStatsCollector:
    """收集并计算队列统计信息。"""

    def __init__(
        self,
        max_history: int = 1000,
        throughput_window: float = 300.0  # 5 分钟
    ):
        """初始化统计收集器。"""
        self._max_history = max_history
        self._throughput_window = throughput_window

        self._timings: deque[TaskTiming] = deque(maxlen=max_history)

        self._total_completed = 0
        self._total_failed = 0
        self._total_cancelled = 0
        self._total_timeout = 0

        self._total_wait_time = 0.0
        self._total_process_time = 0.0

    def record_completion(self, task: DownloadTask) -> None:
        """记录任务成功完成。"""
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
        """记录任务失败。"""
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
        """获取当前统计快照。"""
        total_tasks = (
            self._total_completed +
            self._total_failed +
            self._total_cancelled +
            self._total_timeout
        )

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
        """计算吞吐量（窗口内每分钟成功任务数）。"""
        if not self._timings:
            return 0.0

        now = time.time()
        window_start = now - self._throughput_window

        successful_in_window = sum(
            1 for t in self._timings
            if t.completed_at >= window_start and t.success
        )

        window_minutes = self._throughput_window / 60.0
        return successful_in_window / window_minutes

    def get_recent_timings(self, count: int = 10) -> List[TaskTiming]:
        """获取最近任务时间统计。"""
        return list(self._timings)[-count:]

    def reset(self) -> None:
        """重置全部统计。"""
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
