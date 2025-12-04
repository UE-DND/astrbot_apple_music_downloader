"""
队列回调处理器
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from astrbot.api.event import MessageChain
from astrbot.api import logger
import astrbot.api.message_components as Comp

from ..services import DownloadTask, DownloadResult, DownloadQuality, TaskStatus

if TYPE_CHECKING:
    from ..main import AppleMusicDownloader


class QueueCallbacks:
    """队列事件回调处理"""

    def __init__(self, plugin: "AppleMusicDownloader"):
        self._plugin = plugin

    @property
    def _notify_progress(self) -> bool:
        return self._plugin._notify_progress

    @property
    def _notify_queue_position(self) -> bool:
        return self._plugin._notify_queue_position

    async def execute_download(self, task: DownloadTask) -> DownloadResult:
        """执行下载任务（队列处理器调用）"""
        quality = DownloadQuality(task.quality)
        return await self._plugin.docker_service.download(
            url=task.url,
            quality=quality,
            single_song=task.single_song,
        )

    async def on_task_start(self, task: DownloadTask) -> None:
        """任务开始回调"""
        if not self._notify_progress:
            return

        try:
            song_info = f"【{task.song_name}】" if task.song_name else ""
            message = (
                f"♪ 轮到你了，{task.user_name}！开始下载{song_info}\n"
                f"> 音质: {task.quality_display}\n"
                f"> 任务ID: {task.task_id}\n"
                f"○ 请稍候..."
            )
            await self._send_notification(task.unified_msg_origin, message)
        except Exception as e:
            logger.warning(f"发送任务开始通知失败: {e}")

    async def on_task_complete(self, task: DownloadTask) -> None:
        """任务完成回调 - 发送下载文件"""
        if not task.result:
            return

        result: DownloadResult = task.result

        try:
            if result.success:
                await self._send_notification(
                    task.unified_msg_origin,
                    f"√ 下载完成！\n> 任务ID: {task.task_id}\n> 耗时: {task.process_time:.1f}s\n> 文件将在稍后发送...",
                )
                await self._plugin.file_manager.send_downloaded_files(
                    task.unified_msg_origin, result
                )
            else:
                await self._send_notification(
                    task.unified_msg_origin,
                    f"× 下载失败\n> 任务ID: {task.task_id}\n> 原因: {result.error or result.message}",
                )
        except Exception as e:
            logger.error(f"发送任务完成通知失败: {e}")

    async def on_task_failed(self, task: DownloadTask) -> None:
        """任务失败回调"""
        if not self._notify_progress:
            return

        try:
            status_text = {
                TaskStatus.TIMEOUT: "下载超时",
                TaskStatus.CANCELLED: "任务已取消",
                TaskStatus.FAILED: "下载失败",
            }.get(task.status, "任务异常")

            message = f"× {status_text}\n> 任务ID: {task.task_id}"
            if task.error:
                message += f"\n> 原因: {task.error}"

            await self._send_notification(task.unified_msg_origin, message)
        except Exception as e:
            logger.warning(f"发送任务失败通知失败: {e}")

    async def on_queue_position_changed(self, task: DownloadTask) -> None:
        """队列位置变化回调"""
        if not self._notify_queue_position:
            return

        try:
            position = self._plugin._queue.get_position(task.task_id)
            if position > 0:
                message = f"○ 队列更新\n> 任务ID: {task.task_id}\n> 当前位置: 第 {position} 位"
                await self._send_notification(task.unified_msg_origin, message)
        except Exception as e:
            logger.warning(f"发送位置变化通知失败: {e}")

    async def _send_notification(self, unified_msg_origin: str, message: str) -> None:
        """发送主动消息通知"""
        message_chain = MessageChain(chain=[Comp.Plain(message)])
        await self._plugin.context.send_message(unified_msg_origin, message_chain)
