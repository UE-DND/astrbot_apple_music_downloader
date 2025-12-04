"""
队列管理命令处理器
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

from ..services import TaskStatus

if TYPE_CHECKING:
    from ..main import AppleMusicDownloader


class QueueCommandsHandler:
    """队列管理命令处理"""

    def __init__(self, plugin: "AppleMusicDownloader"):
        self._plugin = plugin

    async def handle_show_queue(self, event: AstrMessageEvent):
        """查看下载队列状态"""
        status = self._plugin._queue.format_queue_status()
        yield event.plain_result(status)

    async def handle_cancel_task(self, event: AstrMessageEvent, task_id: str = ""):
        """取消下载任务"""
        if not self._plugin._allow_cancel:
            yield event.plain_result("× 管理员已禁用任务取消功能")
            return

        sender_id = event.get_sender_id()

        if not task_id:
            user_tasks = self._plugin._queue.get_user_tasks(sender_id)
            pending = [t for t in user_tasks if t.status == TaskStatus.PENDING]

            if not pending:
                yield event.plain_result("○ 您没有等待中的任务")
                return

            lines = ["您的等待任务:", ""]
            for task in pending:
                song_info = f"《{task.song_name}》" if task.song_name else ""
                position = self._plugin._queue.get_position(task.task_id)
                lines.append(f"  • {task.task_id}: {song_info} (位置: {position})")

            lines.extend(
                [
                    "",
                    "使用 /am_cancel <任务ID> 取消指定任务",
                    "使用 /am_cancel all 取消所有任务",
                ]
            )
            yield event.plain_result("\n".join(lines))
            return

        if task_id.lower() == "all":
            count, msg = await self._plugin._queue.cancel_user_tasks(sender_id)
            yield event.plain_result(f"{'√' if count > 0 else '○'} {msg}")
            return

        success, msg = await self._plugin._queue.cancel_task(task_id, sender_id)
        yield event.plain_result(f"{'√' if success else '×'} {msg}")

    async def handle_show_my_tasks(self, event: AstrMessageEvent):
        """查看我的下载任务"""
        sender_id = event.get_sender_id()
        tasks = self._plugin._queue.get_user_tasks(sender_id)

        if not tasks:
            yield event.plain_result("○ 您没有下载任务")
            return

        lines = ["* 我的下载任务", "─" * 20]

        for task in tasks:
            song_info = f"《{task.song_name}》" if task.song_name else task.url[:30]
            status_icon = {
                TaskStatus.PENDING: "○",
                TaskStatus.PROCESSING: "▶",
                TaskStatus.COMPLETED: "√",
                TaskStatus.FAILED: "×",
                TaskStatus.CANCELLED: "-",
                TaskStatus.TIMEOUT: "!",
            }.get(task.status, "?")

            position = ""
            if task.status == TaskStatus.PENDING:
                pos = self._plugin._queue.get_position(task.task_id)
                position = f" (队列位置: {pos})"

            lines.append(f"{status_icon} {task.task_id}: {song_info}{position}")

        yield event.plain_result("\n".join(lines))
