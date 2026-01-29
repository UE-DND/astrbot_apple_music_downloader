"""
下载队列事件系统。
基于观察者模式解耦事件处理。
"""

from __future__ import annotations
import asyncio
import logging
from enum import Enum, auto
from typing import Callable, Awaitable, Any, Dict, List, Optional, TYPE_CHECKING
from dataclasses import dataclass, field

if TYPE_CHECKING:
    from .task import DownloadTask

logger = logging.getLogger(__name__)


class QueueEvent(Enum):
    """队列事件类型。"""
    TASK_ENQUEUED = auto()
    TASK_STARTED = auto()
    TASK_COMPLETED = auto()
    TASK_FAILED = auto()
    TASK_CANCELLED = auto()
    TASK_TIMEOUT = auto()

    QUEUE_POSITION_CHANGED = auto()
    QUEUE_EMPTY = auto()
    QUEUE_FULL = auto()

    PROCESSOR_STARTED = auto()
    PROCESSOR_STOPPED = auto()


EventHandler = Callable[["DownloadTask"], Awaitable[None]]
GenericEventHandler = Callable[..., Awaitable[None]]


@dataclass
class EventSubscription:
    """单个事件订阅。"""
    event: QueueEvent
    handler: GenericEventHandler
    priority: int = 0
    once: bool = False

    def __lt__(self, other: EventSubscription) -> bool:
        return self.priority > other.priority


class QueueEventEmitter:
    """下载队列事件分发器。"""

    def __init__(self):
        self._subscriptions: Dict[QueueEvent, List[EventSubscription]] = {}
        self._lock = asyncio.Lock()

    def on(
        self,
        event: QueueEvent,
        handler: GenericEventHandler,
        priority: int = 0
    ) -> EventSubscription:
        """注册事件处理器。"""
        subscription = EventSubscription(
            event=event,
            handler=handler,
            priority=priority,
            once=False
        )
        self._add_subscription(subscription)
        return subscription

    def once(
        self,
        event: QueueEvent,
        handler: GenericEventHandler,
        priority: int = 0
    ) -> EventSubscription:
        """注册一次性事件处理器。"""
        subscription = EventSubscription(
            event=event,
            handler=handler,
            priority=priority,
            once=True
        )
        self._add_subscription(subscription)
        return subscription

    def _add_subscription(self, subscription: EventSubscription) -> None:
        """添加订阅到注册表。"""
        if subscription.event not in self._subscriptions:
            self._subscriptions[subscription.event] = []

        subs = self._subscriptions[subscription.event]
        subs.append(subscription)
        subs.sort()

    def off(
        self,
        event: QueueEvent,
        handler: Optional[GenericEventHandler] = None
    ) -> int:
        """移除事件处理器。"""
        if event not in self._subscriptions:
            return 0

        if handler is None:
            count = len(self._subscriptions[event])
            self._subscriptions[event] = []
            return count

        original_count = len(self._subscriptions[event])
        self._subscriptions[event] = [
            sub for sub in self._subscriptions[event]
            if sub.handler != handler
        ]
        return original_count - len(self._subscriptions[event])

    def remove_subscription(self, subscription: EventSubscription) -> bool:
        """移除指定订阅。"""
        if subscription.event not in self._subscriptions:
            return False

        try:
            self._subscriptions[subscription.event].remove(subscription)
            return True
        except ValueError:
            return False

    async def emit(self, event: QueueEvent, *args: Any, **kwargs: Any) -> int:
        """向已注册处理器广播事件。"""
        if event not in self._subscriptions:
            return 0

        subscriptions = self._subscriptions[event].copy()
        called_count = 0
        to_remove: List[EventSubscription] = []

        for sub in subscriptions:
            try:
                await sub.handler(*args, **kwargs)
                called_count += 1

                if sub.once:
                    to_remove.append(sub)

            except Exception as e:
                logger.warning(
                    f"Event handler error for {event.name}: {e}",
                    exc_info=True
                )

        for sub in to_remove:
            self.remove_subscription(sub)

        return called_count

    async def emit_concurrent(
        self,
        event: QueueEvent,
        *args: Any,
        **kwargs: Any
    ) -> List[Optional[Exception]]:
        """并发广播事件。"""
        if event not in self._subscriptions:
            return []

        subscriptions = self._subscriptions[event].copy()
        to_remove: List[EventSubscription] = []

        async def safe_call(sub: EventSubscription) -> Optional[Exception]:
            try:
                await sub.handler(*args, **kwargs)
                if sub.once:
                    to_remove.append(sub)
                return None
            except Exception as e:
                logger.warning(f"Event handler error for {event.name}: {e}")
                return e

        results = list(await asyncio.gather(
            *[safe_call(sub) for sub in subscriptions],
            return_exceptions=False
        ))

        for sub in to_remove:
            self.remove_subscription(sub)

        return results

    def has_listeners(self, event: QueueEvent) -> bool:
        """检查事件是否有监听器。"""
        return bool(self._subscriptions.get(event))

    def listener_count(self, event: QueueEvent) -> int:
        """获取事件监听器数量。"""
        return len(self._subscriptions.get(event, []))

    def clear(self) -> None:
        """移除所有事件处理器。"""
        self._subscriptions.clear()

    def events_with_listeners(self) -> List[QueueEvent]:
        """获取已有监听器的事件列表。"""
        return [
            event for event, subs in self._subscriptions.items()
            if subs
        ]


TaskEventHandler = Callable[["DownloadTask"], Awaitable[None]]


class TaskEventAdapter:
    """任务事件注册适配器。"""

    def __init__(self, emitter: QueueEventEmitter):
        self._emitter = emitter

    def on_enqueued(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务入队事件处理器。"""
        return self._emitter.on(QueueEvent.TASK_ENQUEUED, handler)

    def on_start(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务开始事件处理器。"""
        return self._emitter.on(QueueEvent.TASK_STARTED, handler)

    def on_complete(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务完成事件处理器。"""
        return self._emitter.on(QueueEvent.TASK_COMPLETED, handler)

    def on_failed(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务失败事件处理器。"""
        return self._emitter.on(QueueEvent.TASK_FAILED, handler)

    def on_cancelled(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务取消事件处理器。"""
        return self._emitter.on(QueueEvent.TASK_CANCELLED, handler)

    def on_timeout(self, handler: TaskEventHandler) -> EventSubscription:
        """注册任务超时事件处理器。"""
        return self._emitter.on(QueueEvent.TASK_TIMEOUT, handler)

    def on_position_changed(self, handler: TaskEventHandler) -> EventSubscription:
        """注册队列位置变化事件处理器。"""
        return self._emitter.on(QueueEvent.QUEUE_POSITION_CHANGED, handler)
