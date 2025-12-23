"""
Apple Music Downloader Services Module


Provides service layer for managing wrapper connections and download operations.
"""

from .downloader import (
    DownloaderService,
    DownloadQuality,
    DownloadResult,
    ServiceStatus,
    URLParser,
    MetadataFetcher,
)

from .wrapper_service import (
    WrapperService,
    WrapperMode,
    WrapperStatus,
)

from .queue import (
    DownloadQueue,
    DownloadTask,
    TaskStatus,
    TaskPriority,
    TaskStateMachine,
    QueueEvent,
    QueueEventEmitter,
    TaskEventAdapter,
    EventSubscription,
    QueueStats,
    QueueStatsCollector,
    TaskTiming,
    TaskQueue,
    PriorityStrategy,
    FIFOWithPriorityStrategy,
    TaskProcessor,
    QueueFormatter,
    ChineseFormatter,
    MinimalFormatter,
    default_formatter,
)

__all__ = [
    "DownloaderService",
    "DownloadQuality",
    "DownloadResult",
    "ServiceStatus",
    "URLParser",
    "MetadataFetcher",
    "WrapperService",
    "WrapperMode",
    "WrapperStatus",
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
