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
    # Main facade
    DownloadQueue,
    # Task
    DownloadTask,
    TaskStatus,
    TaskPriority,
    TaskStateMachine,
    # Events
    QueueEvent,
    QueueEventEmitter,
    TaskEventAdapter,
    EventSubscription,
    # Stats
    QueueStats,
    QueueStatsCollector,
    TaskTiming,
    # Storage
    TaskQueue,
    PriorityStrategy,
    FIFOWithPriorityStrategy,
    # Processor
    TaskProcessor,
    # Formatter
    QueueFormatter,
    ChineseFormatter,
    MinimalFormatter,
    default_formatter,
)

__all__ = [
    # Downloader service
    "DownloaderService",
    "DownloadQuality",
    "DownloadResult",
    "ServiceStatus",
    "URLParser",
    "MetadataFetcher",
    # Wrapper service
    "WrapperService",
    "WrapperMode",
    "WrapperStatus",
    # Queue - Main
    "DownloadQueue",
    # Queue - Task
    "DownloadTask",
    "TaskStatus",
    "TaskPriority",
    "TaskStateMachine",
    # Queue - Events
    "QueueEvent",
    "QueueEventEmitter",
    "TaskEventAdapter",
    "EventSubscription",
    # Queue - Stats
    "QueueStats",
    "QueueStatsCollector",
    "TaskTiming",
    # Queue - Storage
    "TaskQueue",
    "PriorityStrategy",
    "FIFOWithPriorityStrategy",
    # Queue - Processor
    "TaskProcessor",
    # Queue - Formatter
    "QueueFormatter",
    "ChineseFormatter",
    "MinimalFormatter",
    "default_formatter",
]
