"""
下载命令处理器

"""

from __future__ import annotations
from typing import TYPE_CHECKING, Any

from astrbot.api.event import AstrMessageEvent
from astrbot.api import logger
from astrbot.core.utils.session_waiter import session_waiter, SessionController

from ..services import URLParser, TaskStatus

if TYPE_CHECKING:
    from ..main import AppleMusicDownloader


class DownloadHandler:
    """下载命令处理"""

    def __init__(self, plugin: "AppleMusicDownloader"):
        self._plugin = plugin

    async def handle_download(
        self, event: AstrMessageEvent, url: str = "", quality: str = ""
    ):
        """处理下载命令"""
        # 交互模式
        if not url:
            yield event.plain_result(
                "♪ Apple Music 下载器\n"
                "─" * 20 + "\n"
                "请发送 Apple Music 单曲链接\n"
                "支持格式:\n"
                "  • 带 ?i= 参数的分享链接\n"
                "  • /song/ 路径的直接链接\n\n"
                "发送 '取消' 退出"
            )

            session_data: dict[str, Any] = {
                "state": "url",
                "parsed_url": None,
                "parsed_data": None,
            }

            @session_waiter(timeout=60, record_history_chains=False)
            async def interactive_session(
                controller: SessionController, evt: AstrMessageEvent
            ):
                user_input = evt.message_str.strip()

                if user_input.lower() in ("取消", "cancel", "exit", "quit"):
                    await evt.send(evt.plain_result("已取消下载"))
                    controller.stop()
                    return

                if session_data["state"] == "url":
                    parsed = URLParser.parse(user_input)
                    if not parsed or parsed.get("type") != "song":
                        await evt.send(
                            evt.plain_result(
                                "× 无效的链接\n请发送 Apple Music 单曲链接\n或发送 '取消' 退出"
                            )
                        )
                        controller.keep(timeout=60, reset_timeout=True)
                        return

                    session_data["parsed_url"] = user_input
                    session_data["parsed_data"] = parsed
                    session_data["state"] = "quality"

                    await evt.send(
                        evt.plain_result(
                            "√ 链接有效\n\n请选择音质:\n"
                            "  1. alac - 无损 (默认)\n"
                            "  2. aac - 高品质 AAC\n\n"
                            "发送数字或音质名称，或发送空格使用默认"
                        )
                    )
                    controller.keep(timeout=30, reset_timeout=True)
                    return

                if session_data["state"] == "quality":
                    quality_map = {
                        "": "alac",
                        " ": "alac",
                        "1": "alac",
                        "2": "aac",
                        "alac": "alac",
                        "无损": "alac",
                        "aac": "aac",
                    }

                    quality_key = user_input.lower()
                    if quality_key not in quality_map:
                        await evt.send(
                            evt.plain_result("× 仅支持 alac / aac 音质，请重新输入")
                        )
                        controller.keep(timeout=30, reset_timeout=True)
                        return

                    selected_quality = quality_map[quality_key]

                    await self._process_download(
                        evt,
                        session_data["parsed_url"],
                        selected_quality,
                        session_data["parsed_data"],
                    )
                    controller.stop()

            try:
                await interactive_session(event)
            except TimeoutError:
                yield event.plain_result("○ 等待超时，已退出")
            except Exception as e:
                logger.error(f"交互式下载出错: {e}")
                yield event.plain_result(f"× 出错了: {e}")
            finally:
                event.stop_event()
            return

        # 直接下载
        parsed = URLParser.parse(url)
        if not parsed or parsed.get("type") != "song":
            yield event.plain_result(
                "× 仅支持 Apple Music 单曲链接\n"
                "请使用包含 '?i=' 参数的单曲分享链接或 /song/ 路径的链接"
            )
            return

        # 从新配置系统获取默认音质
        default_quality = self._plugin.plugin_config.download.default_quality
        if default_quality not in {"alac", "aac"}:
            logger.warning(f"不支持的默认音质配置: {default_quality}，已回退为 alac")
            default_quality = "alac"

        quality_map = {
            "": default_quality,
            "alac": "alac",
            "无损": "alac",
            "lossless": "alac",
            "aac": "aac",
        }
        quality_key = quality.lower()
        if quality_key and quality_key not in quality_map:
            yield event.plain_result("× 仅支持 alac / aac 音质")
            return
        quality_str = quality_map.get(quality_key, default_quality)

        await self._process_download(event, url, quality_str, parsed)

    async def _process_download(
        self,
        event: AstrMessageEvent,
        url: str,
        quality_str: str,
        parsed: dict,
    ) -> None:
        """处理下载请求"""
        quality_display = {
            "alac": "无损 ALAC",
            "aac": "高品质 AAC",
        }.get(quality_str, quality_str)

        storefront = parsed.get("storefront")
        if not storefront:
            storefront = self._plugin.plugin_config.region.storefront

        song_name = None
        song_id = parsed.get("id")
        if song_id and self._plugin.downloader_service:
            try:
                metadata = await self._plugin.downloader_service.get_song_metadata(url)
                if metadata:
                    song_name = f"{metadata.get('title', '')} - {metadata.get('artist', '')}"
            except Exception as e:
                logger.warning(f"获取歌曲信息失败: {e}")

        sender_name = event.get_sender_name()
        sender_id = event.get_sender_id()

        # 检查用户任务数限制
        user_tasks = self._plugin._queue.get_user_tasks(sender_id)
        pending_tasks = [t for t in user_tasks if t.status == TaskStatus.PENDING]
        if len(pending_tasks) >= self._plugin._max_tasks_per_user:
            await event.send(
                event.plain_result(
                    f"× 您已有 {len(pending_tasks)} 个任务在排队\n"
                    f"每用户最多 {self._plugin._max_tasks_per_user} 个排队任务\n"
                    f"请等待现有任务完成，或使用 /am_cancel 取消任务"
                )
            )
            return

        # 加入队列
        success, msg, task = await self._plugin._queue.enqueue(
            url=url,
            quality=quality_str,
            user_id=sender_id,
            user_name=sender_name or sender_id,
            unified_msg_origin=event.unified_msg_origin,
            song_name=song_name,
            quality_display=quality_display,
        )

        if not success:
            await event.send(event.plain_result(f"× {msg}"))
            return

        position = self._plugin._queue.get_position(task.task_id)
        song_info = f"【{song_name}】" if song_name else ""

        if position == 1 and self._plugin._queue.current_task is None:
            await event.send(
                event.plain_result(
                    f"♪ 下载任务已创建{song_info}\n"
                    f"> 音质: {quality_display}\n"
                    f"> 任务ID: {task.task_id}\n"
                    f"○ 即将开始下载..."
                )
            )
        else:
            await event.send(
                event.plain_result(
                    f"○ 已加入下载队列{song_info}\n"
                    f"> 音质: {quality_display}\n"
                    f"> 任务ID: {task.task_id}\n"
                    f"# 队列位置: 第 {position} 位\n"
                    f"* 请耐心等待，下载开始时会通知您"
                )
            )
