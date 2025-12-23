"""
性能指标采集器。
用于统计 wrapper 管理器性能数据。
"""

import time
import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
from collections import defaultdict, deque
from enum import Enum

from ..logger import LoggerInterface, get_logger
logger = get_logger()


class MetricType(Enum):
    """指标类型。"""
    COUNTER = "counter"  # 计数
    GAUGE = "gauge"  # 当前值
    HISTOGRAM = "histogram"  # 分布
    TIMER = "timer"  # 耗时


@dataclass
class Metric:
    """单个指标数据点。"""
    name: str
    type: MetricType
    value: float
    timestamp: datetime = field(default_factory=datetime.now)
    tags: Dict[str, str] = field(default_factory=dict)


@dataclass
class PerformanceStats:
    """单个操作的性能统计。"""
    operation: str
    total_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_duration_ms: float = 0.0
    min_duration_ms: float = float('inf')
    max_duration_ms: float = 0.0
    avg_duration_ms: float = 0.0
    p50_duration_ms: float = 0.0
    p95_duration_ms: float = 0.0
    p99_duration_ms: float = 0.0


class MetricsCollector:
    """性能指标收集与聚合器。"""

    def __init__(self, window_size: int = 1000):
        """初始化指标采集器。"""
        self.window_size = window_size

        self._counters: Dict[str, float] = defaultdict(float)
        self._gauges: Dict[str, float] = {}
        self._histograms: Dict[str, deque] = defaultdict(lambda: deque(maxlen=window_size))
        self._timers: Dict[str, List[float]] = defaultdict(list)

        self._operation_stats: Dict[str, PerformanceStats] = {}

        self._start_time = datetime.now()

        logger.info(f"Metrics collector initialized (window_size={window_size})")

    def increment_counter(self, name: str, value: float = 1.0, tags: Optional[Dict[str, str]] = None):
        """递增计数器指标。"""
        key = self._make_key(name, tags)
        self._counters[key] += value

    def set_gauge(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """设置仪表指标值。"""
        key = self._make_key(name, tags)
        self._gauges[key] = value

    def record_histogram(self, name: str, value: float, tags: Optional[Dict[str, str]] = None):
        """记录直方图指标。"""
        key = self._make_key(name, tags)
        self._histograms[key].append(value)

    def start_timer(self, operation: str) -> 'TimerContext':
        """为操作启动计时器。"""
        return TimerContext(self, operation)

    def record_operation(
        self,
        operation: str,
        duration_ms: float,
        success: bool
    ):
        """记录操作性能。"""
        if operation not in self._operation_stats:
            self._operation_stats[operation] = PerformanceStats(operation=operation)

        stats = self._operation_stats[operation]
        stats.total_count += 1

        if success:
            stats.success_count += 1
        else:
            stats.failure_count += 1

        stats.total_duration_ms += duration_ms
        stats.min_duration_ms = min(stats.min_duration_ms, duration_ms)
        stats.max_duration_ms = max(stats.max_duration_ms, duration_ms)

        stats.avg_duration_ms = stats.total_duration_ms / stats.total_count

        self._timers[operation].append(duration_ms)

        if len(self._timers[operation]) > self.window_size:
            self._timers[operation] = self._timers[operation][-self.window_size:]

        self._update_percentiles(operation)

    def _update_percentiles(self, operation: str):
        """更新操作的分位数统计。"""
        if operation not in self._timers or not self._timers[operation]:
            return

        sorted_times = sorted(self._timers[operation])
        count = len(sorted_times)

        stats = self._operation_stats[operation]

        stats.p50_duration_ms = sorted_times[int(count * 0.50)]
        stats.p95_duration_ms = sorted_times[int(count * 0.95)]
        stats.p99_duration_ms = sorted_times[int(count * 0.99)]

    def get_counter(self, name: str, tags: Optional[Dict[str, str]] = None) -> float:
        """获取计数器当前值。"""
        key = self._make_key(name, tags)
        return self._counters.get(key, 0.0)

    def get_gauge(self, name: str, tags: Optional[Dict[str, str]] = None) -> Optional[float]:
        """获取仪表当前值。"""
        key = self._make_key(name, tags)
        return self._gauges.get(key)

    def get_histogram_stats(self, name: str, tags: Optional[Dict[str, str]] = None) -> Dict[str, float]:
        """获取直方图统计。"""
        key = self._make_key(name, tags)
        values = self._histograms.get(key, [])

        if not values:
            return {}

        sorted_values = sorted(values)
        count = len(sorted_values)

        return {
            "count": count,
            "min": sorted_values[0],
            "max": sorted_values[-1],
            "avg": sum(sorted_values) / count,
            "p50": sorted_values[int(count * 0.50)],
            "p95": sorted_values[int(count * 0.95)] if count > 20 else sorted_values[-1],
            "p99": sorted_values[int(count * 0.99)] if count > 100 else sorted_values[-1],
        }

    def get_operation_stats(self, operation: str) -> Optional[PerformanceStats]:
        """获取操作性能统计。"""
        return self._operation_stats.get(operation)

    def get_all_operation_stats(self) -> Dict[str, PerformanceStats]:
        """获取全部操作统计。"""
        return dict(self._operation_stats)

    def get_summary(self) -> Dict[str, Any]:
        """获取指标汇总。"""
        uptime = (datetime.now() - self._start_time).total_seconds()

        return {
            "uptime_seconds": uptime,
            "counters": dict(self._counters),
            "gauges": dict(self._gauges),
            "histograms": {
                name: self.get_histogram_stats(name)
                for name in self._histograms.keys()
            },
            "operations": {
                op: {
                    "total": stats.total_count,
                    "success": stats.success_count,
                    "failure": stats.failure_count,
                    "success_rate": stats.success_count / stats.total_count if stats.total_count > 0 else 0,
                    "avg_duration_ms": stats.avg_duration_ms,
                    "min_duration_ms": stats.min_duration_ms if stats.min_duration_ms != float('inf') else 0,
                    "max_duration_ms": stats.max_duration_ms,
                    "p50_duration_ms": stats.p50_duration_ms,
                    "p95_duration_ms": stats.p95_duration_ms,
                    "p99_duration_ms": stats.p99_duration_ms,
                }
                for op, stats in self._operation_stats.items()
            }
        }

    def reset(self):
        """重置全部指标。"""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()
        self._timers.clear()
        self._operation_stats.clear()
        self._start_time = datetime.now()
        logger.info("Metrics collector reset")

    def _make_key(self, name: str, tags: Optional[Dict[str, str]]) -> str:
        """生成带标签的指标键。"""
        if not tags:
            return name

        tag_str = ",".join(f"{k}={v}" for k, v in sorted(tags.items()))
        return f"{name}{{{tag_str}}}"


class TimerContext:
    """操作计时上下文管理器。"""

    def __init__(self, collector: MetricsCollector, operation: str):
        """初始化计时上下文。"""
        self.collector = collector
        self.operation = operation
        self.start_time: Optional[float] = None
        self.success = True

    def __enter__(self):
        """启动计时。"""
        self.start_time = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """停止计时并记录指标。"""
        if self.start_time is not None:
            duration_ms = (time.perf_counter() - self.start_time) * 1000

            # 出现异常则标记失败
            if exc_type is not None:
                self.success = False

            self.collector.record_operation(
                operation=self.operation,
                duration_ms=duration_ms,
                success=self.success
            )

    def mark_failure(self):
        """标记操作失败。"""
        self.success = False


# 全局指标采集器实例
_global_collector: Optional[MetricsCollector] = None


def get_metrics_collector() -> MetricsCollector:
    """获取全局指标采集器实例。"""
    global _global_collector

    if _global_collector is None:
        _global_collector = MetricsCollector()

    return _global_collector


def reset_metrics_collector():
    """重置全局指标采集器。"""
    global _global_collector

    if _global_collector:
        _global_collector.reset()
