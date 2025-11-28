"""
Event System for Download Queue


Implements Observer pattern for decoupled event handling.
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
    """Queue event types."""
    # Task lifecycle events
    TASK_ENQUEUED = auto()      # Task added to queue
    TASK_STARTED = auto()       # Task processing started
    TASK_COMPLETED = auto()     # Task completed successfully
    TASK_FAILED = auto()        # Task failed
    TASK_CANCELLED = auto()     # Task was cancelled
    TASK_TIMEOUT = auto()       # Task timed out

    # Queue state events
    QUEUE_POSITION_CHANGED = auto()  # Task position in queue changed
    QUEUE_EMPTY = auto()             # Queue became empty
    QUEUE_FULL = auto()              # Queue became full

    # Processor events
    PROCESSOR_STARTED = auto()   # Processor started
    PROCESSOR_STOPPED = auto()   # Processor stopped


# Type aliases for event handlers
EventHandler = Callable[["DownloadTask"], Awaitable[None]]
GenericEventHandler = Callable[..., Awaitable[None]]


@dataclass
class EventSubscription:
    """Represents a single event subscription."""
    event: QueueEvent
    handler: GenericEventHandler
    priority: int = 0  # Higher priority handlers run first
    once: bool = False  # If True, handler is removed after first call

    def __lt__(self, other: EventSubscription) -> bool:
        return self.priority > other.priority  # Higher priority first


class QueueEventEmitter:
    """
    Event emitter for download queue.

    Features:
    - Async event handlers
    - Priority-based handler ordering
    - One-time handlers (auto-removed after execution)
    - Error isolation (one handler failure doesn't affect others)
    - Event filtering support

    Usage:
        emitter = QueueEventEmitter()

        # Register handler
        emitter.on(QueueEvent.TASK_COMPLETED, my_handler)

        # Register one-time handler
        emitter.once(QueueEvent.TASK_STARTED, one_time_handler)

        # Emit event
        await emitter.emit(QueueEvent.TASK_COMPLETED, task)

        # Remove handler
        emitter.off(QueueEvent.TASK_COMPLETED, my_handler)
    """

    def __init__(self):
        self._subscriptions: Dict[QueueEvent, List[EventSubscription]] = {}
        self._lock = asyncio.Lock()

    def on(
        self,
        event: QueueEvent,
        handler: GenericEventHandler,
        priority: int = 0
    ) -> EventSubscription:
        """
        Register an event handler.

        Args:
            event: Event type to listen for
            handler: Async function to call when event occurs
            priority: Handler priority (higher runs first)

        Returns:
            Subscription object (can be used to unsubscribe)
        """
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
        """
        Register a one-time event handler (removed after first call).

        Args:
            event: Event type to listen for
            handler: Async function to call when event occurs
            priority: Handler priority (higher runs first)

        Returns:
            Subscription object
        """
        subscription = EventSubscription(
            event=event,
            handler=handler,
            priority=priority,
            once=True
        )
        self._add_subscription(subscription)
        return subscription

    def _add_subscription(self, subscription: EventSubscription) -> None:
        """Add subscription to the registry."""
        if subscription.event not in self._subscriptions:
            self._subscriptions[subscription.event] = []

        subs = self._subscriptions[subscription.event]
        subs.append(subscription)
        # Keep sorted by priority
        subs.sort()

    def off(
        self,
        event: QueueEvent,
        handler: Optional[GenericEventHandler] = None
    ) -> int:
        """
        Remove event handler(s).

        Args:
            event: Event type
            handler: Specific handler to remove, or None to remove all

        Returns:
            Number of handlers removed
        """
        if event not in self._subscriptions:
            return 0

        if handler is None:
            # Remove all handlers for this event
            count = len(self._subscriptions[event])
            self._subscriptions[event] = []
            return count

        # Remove specific handler
        original_count = len(self._subscriptions[event])
        self._subscriptions[event] = [
            sub for sub in self._subscriptions[event]
            if sub.handler != handler
        ]
        return original_count - len(self._subscriptions[event])

    def remove_subscription(self, subscription: EventSubscription) -> bool:
        """
        Remove a specific subscription.

        Args:
            subscription: The subscription object to remove

        Returns:
            True if removed, False if not found
        """
        if subscription.event not in self._subscriptions:
            return False

        try:
            self._subscriptions[subscription.event].remove(subscription)
            return True
        except ValueError:
            return False

    async def emit(self, event: QueueEvent, *args: Any, **kwargs: Any) -> int:
        """
        Emit an event to all registered handlers.

        Args:
            event: Event type to emit
            *args: Positional arguments passed to handlers
            **kwargs: Keyword arguments passed to handlers

        Returns:
            Number of handlers that were called
        """
        if event not in self._subscriptions:
            return 0

        # Copy list to avoid modification during iteration
        subscriptions = self._subscriptions[event].copy()
        called_count = 0
        to_remove: List[EventSubscription] = []

        for sub in subscriptions:
            try:
                await sub.handler(*args, **kwargs)
                called_count += 1

                # Mark one-time handlers for removal
                if sub.once:
                    to_remove.append(sub)

            except Exception as e:
                logger.warning(
                    f"Event handler error for {event.name}: {e}",
                    exc_info=True
                )
                # Continue with other handlers

        # Remove one-time handlers
        for sub in to_remove:
            self.remove_subscription(sub)

        return called_count

    async def emit_concurrent(
        self,
        event: QueueEvent,
        *args: Any,
        **kwargs: Any
    ) -> List[Optional[Exception]]:
        """
        Emit an event to all handlers concurrently.

        Unlike emit(), this runs all handlers in parallel using asyncio.gather().

        Args:
            event: Event type to emit
            *args: Positional arguments passed to handlers
            **kwargs: Keyword arguments passed to handlers

        Returns:
            List of exceptions (None for successful handlers)
        """
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

        results = await asyncio.gather(
            *[safe_call(sub) for sub in subscriptions],
            return_exceptions=False
        )

        # Remove one-time handlers
        for sub in to_remove:
            self.remove_subscription(sub)

        return results

    def has_listeners(self, event: QueueEvent) -> bool:
        """Check if event has any listeners."""
        return bool(self._subscriptions.get(event))

    def listener_count(self, event: QueueEvent) -> int:
        """Get number of listeners for an event."""
        return len(self._subscriptions.get(event, []))

    def clear(self) -> None:
        """Remove all event handlers."""
        self._subscriptions.clear()

    def events_with_listeners(self) -> List[QueueEvent]:
        """Get list of events that have listeners."""
        return [
            event for event, subs in self._subscriptions.items()
            if subs
        ]


# Convenience type for task-specific event handlers
TaskEventHandler = Callable[["DownloadTask"], Awaitable[None]]


class TaskEventAdapter:
    """
    Adapter to simplify task event registration.

    Provides a cleaner API for common task events.

    Usage:
        adapter = TaskEventAdapter(emitter)
        adapter.on_start(my_start_handler)
        adapter.on_complete(my_complete_handler)
    """

    def __init__(self, emitter: QueueEventEmitter):
        self._emitter = emitter

    def on_enqueued(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task enqueued event."""
        return self._emitter.on(QueueEvent.TASK_ENQUEUED, handler)

    def on_start(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task start event."""
        return self._emitter.on(QueueEvent.TASK_STARTED, handler)

    def on_complete(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task complete event."""
        return self._emitter.on(QueueEvent.TASK_COMPLETED, handler)

    def on_failed(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task failed event."""
        return self._emitter.on(QueueEvent.TASK_FAILED, handler)

    def on_cancelled(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task cancelled event."""
        return self._emitter.on(QueueEvent.TASK_CANCELLED, handler)

    def on_timeout(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for task timeout event."""
        return self._emitter.on(QueueEvent.TASK_TIMEOUT, handler)

    def on_position_changed(self, handler: TaskEventHandler) -> EventSubscription:
        """Register handler for queue position changed event."""
        return self._emitter.on(QueueEvent.QUEUE_POSITION_CHANGED, handler)
