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
        """
        执行下载任务（队列处理器调用）
        """
        quality = DownloadQuality(task.quality)

        # 执行下载
        result = await self._plugin.docker_service.download(
            url=task.url,
            quality=quality,
            single_song=task.single_song,
        )

        # 下载成功后发送文件
        if result.success and result.file_paths:
            # 检查文件是否已经发送过（来自缓存的已发送文件）
            if getattr(result, "files_sent", False):
                logger.info(f"文件已发送过，跳过重复发送: {task.task_id}")
                await self._send_notification(
                    task.unified_msg_origin,
                    f"√ 下载完成（文件已存在）\n> 任务ID: {task.task_id}\n> 文件已在之前发送过",
                )
            else:
                try:
                    await self._send_notification(
                        task.unified_msg_origin,
                        f"√ 下载完成！\n> 任务ID: {task.task_id}\n> 正在发送文件...",
                    )
                    await self._plugin.file_manager.send_downloaded_files(
                        task.unified_msg_origin, result
                    )
                    # 标记文件已发送并更新缓存
                    result.files_sent = True
                    self._update_cache_sent_status(task.url, task.quality, task.single_song)
                except Exception as e:
                    logger.error(f"发送文件失败: {e}")
                    result.files_sent = False
                    result.send_error = str(e)

        return result

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
        """
        任务完成回调

        注意：文件发送已在 execute_download 中完成，此回调仅用于状态汇报
        """
        if not task.result:
            return

        result: DownloadResult = task.result

        try:
            if result.success:
                # 检查文件是否已发送
                files_sent = getattr(result, "files_sent", False)
                send_error = getattr(result, "send_error", None)

                if files_sent:
                    await self._send_notification(
                        task.unified_msg_origin,
                        f"✓ 任务完成\n> 任务ID: {task.task_id}\n> 耗时: {task.process_time:.1f}s",
                    )
                elif send_error:
                    await self._send_notification(
                        task.unified_msg_origin,
                        f"! 下载成功但文件发送失败\n> 任务ID: {task.task_id}\n> 原因: {send_error}\n> 文件已保存到服务器",
                    )
                else:
                    # 没有文件需要发送（如下载结果为空）
                    await self._send_notification(
                        task.unified_msg_origin,
                        f"√ 下载完成\n> 任务ID: {task.task_id}\n> 耗时: {task.process_time:.1f}s",
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

    def _update_cache_sent_status(self, url: str, quality: str, single_song: bool) -> None:
        """更新缓存中的发送状态"""
        try:
            from ..services import DownloadQuality
            quality_enum = DownloadQuality(quality)

            # 获取现有缓存条目并更新发送状态
            docker_service = self._plugin.docker_service
            cache = docker_service._load_cache()
            key = docker_service._cache_key(url, quality_enum, single_song)

            if key in cache:
                cache[key]["files_sent"] = True
                docker_service._save_cache(cache)
                logger.debug(f"已更新缓存发送状态: {url}")
            else:
                logger.debug(f"缓存中未找到条目: {url}")
        except Exception as e:
            logger.warning(f"更新缓存发送状态失败: {e}")

    async def _send_notification(self, unified_msg_origin: str, message: str) -> None:
        """发送主动消息通知"""
        message_chain = MessageChain(chain=[Comp.Plain(message)])
        await self._plugin.context.send_message(unified_msg_origin, message_chain)
