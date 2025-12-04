"""
Apple Music Downloader - AstrBot 插件
"""

import os
import asyncio
import shutil
import time
from pathlib import Path
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult, MessageChain
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp
from astrbot.core.utils.session_waiter import session_waiter, SessionController

from .services import (
    DockerService,
    DownloadQuality,
    DownloadResult,
    URLParser,
    MetadataFetcher,
    DownloadQueue,
    DownloadTask,
    TaskStatus,
    TaskPriority,
)


@register(
    "astrbot_plugin_applemusicdownloader",
    "UE-DND",
    "Apple Music Downloader",
    "0.1.2",
    "https://github.com/UE-DND/apple-music-downloader",
)
class AppleMusicDownloader(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).parent
        self.docker_service: Optional[DockerService] = None

        # 队列配置
        queue_config = config.get("queue_config", {})
        self._queue = DownloadQueue(
            max_queue_size=queue_config.get("max_queue_size", 20),
            task_timeout=queue_config.get("task_timeout", 600),
            queue_timeout=queue_config.get("queue_timeout", 300),
        )
        self._notify_progress = queue_config.get("notify_progress", True)
        self._notify_queue_position = queue_config.get("notify_queue_position", False)
        self._allow_cancel = queue_config.get("allow_cancel", True)
        self._max_tasks_per_user = queue_config.get("max_tasks_per_user", 3)

        # 定时清理任务
        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_interval = 60 * 60  # 每小时检查一次
        self._file_ttl = 24 * 60 * 60  # 文件保留时间：24小时

    async def initialize(self):
        """插件初始化"""
        logger.info("Apple Music Downloader 插件初始化中...")

        # 初始化 Docker 服务
        self.docker_service = DockerService(str(self.plugin_dir), dict(self.config))

        if await self.docker_service.check_docker_available():
            logger.info("Docker 服务可用")

            if self.config.get("auto_start_wrapper", True):
                status = await self.docker_service.get_service_status()
                if not status.wrapper_running and status.wrapper_image_exists:
                    success, msg = await self.docker_service.start_wrapper()
                    if success:
                        logger.info("Wrapper 服务已自动启动")
                    else:
                        logger.warning(f"Wrapper 自动启动失败: {msg}")
        else:
            logger.warning("Docker 不可用，部分功能可能受限")

        # 配置队列回调
        self._queue.set_download_handler(self._execute_download)
        self._queue.on_task_start(self._on_task_start)
        self._queue.on_task_complete(self._on_task_complete)
        self._queue.on_task_failed(self._on_task_failed)
        if self._notify_queue_position:
            self._queue.on_queue_position_changed(self._on_queue_position_changed)

        # 启动队列处理器
        await self._queue.start_processor()
        logger.info("下载队列处理器已启动")

        # 启动定时清理任务
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.info("已启动定时清理任务（每小时检查，删除超过24小时的文件）")

        logger.info("Apple Music Downloader 插件初始化完成")

    async def terminate(self):
        """插件销毁"""
        logger.info("Apple Music Downloader 插件正在关闭...")

        # 停止队列处理器
        await self._queue.stop_processor()
        logger.info("下载队列处理器已停止")

        # 停止定时清理
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("定时清理任务已停止")

    # ==================== 队列回调 ====================

    async def _execute_download(self, task: DownloadTask) -> DownloadResult:
        """执行下载任务（队列处理器调用）"""
        quality = DownloadQuality(task.quality)
        return await self.docker_service.download(
            url=task.url,
            quality=quality,
            single_song=task.single_song,
        )

    async def _on_task_start(self, task: DownloadTask) -> None:
        """任务开始回调 - 主动通知用户"""
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

    async def _on_task_complete(self, task: DownloadTask) -> None:
        """任务完成回调 - 发送下载文件"""
        if not task.result:
            return

        result: DownloadResult = task.result

        try:
            if result.success:
                # 发送成功消息
                await self._send_notification(
                    task.unified_msg_origin,
                    f"√ 下载完成！\n> 任务ID: {task.task_id}\n> 耗时: {task.process_time:.1f}s\n> 文件将在稍后发送...",
                )

                # 发送文件
                await self._send_downloaded_files_by_umo(
                    task.unified_msg_origin, result
                )
            else:
                await self._send_notification(
                    task.unified_msg_origin,
                    f"× 下载失败\n> 任务ID: {task.task_id}\n> 原因: {result.error or result.message}",
                )
        except Exception as e:
            logger.error(f"发送任务完成通知失败: {e}")

    async def _on_task_failed(self, task: DownloadTask) -> None:
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

    async def _on_queue_position_changed(self, task: DownloadTask) -> None:
        """队列位置变化回调"""
        if not self._notify_queue_position:
            return

        try:
            position = self._queue.get_position(task.task_id)
            if position > 0:
                message = f"○ 队列更新\n> 任务ID: {task.task_id}\n> 当前位置: 第 {position} 位"
                await self._send_notification(task.unified_msg_origin, message)
        except Exception as e:
            logger.warning(f"发送位置变化通知失败: {e}")

    async def _send_notification(self, unified_msg_origin: str, message: str) -> None:
        """发送主动消息通知"""
        message_chain = MessageChain(chain=[Comp.Plain(message)])
        await self.context.send_message(unified_msg_origin, message_chain)

    # ==================== 下载 ====================

    @filter.command("am", alias={"applemusic", "apple"})
    async def download_music(
        self, event: AstrMessageEvent, url: str = "", quality: str = ""
    ):
        """下载 Apple Music 单曲

        用法: /am <链接> [音质]
        音质可选: alac(无损) / aac / atmos(杜比)
        """
        # 如果没有提供 URL，进入交互模式
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

            # 用于存储会话状态
            session_data = {"state": "url", "parsed_url": None, "parsed_data": None}

            @session_waiter(timeout=60, record_history_chains=False)
            async def interactive_session(
                controller: SessionController, evt: AstrMessageEvent
            ):
                user_input = evt.message_str.strip()

                # 检查取消命令
                if user_input.lower() in ("取消", "cancel", "exit", "quit"):
                    await evt.send(evt.plain_result("已取消下载"))
                    controller.stop()
                    return

                # 状态机：等待 URL
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

                    # URL 有效，保存并进入音质选择
                    session_data["parsed_url"] = user_input
                    session_data["parsed_data"] = parsed
                    session_data["state"] = "quality"

                    await evt.send(
                        evt.plain_result(
                            "√ 链接有效\n\n请选择音质:\n"
                            "  1. alac - 无损 (默认)\n"
                            "  2. aac - 高品质 AAC\n"
                            "  3. atmos - 杜比全景声\n\n"
                            "发送数字或音质名称，或发送空格使用默认"
                        )
                    )
                    controller.keep(timeout=30, reset_timeout=True)
                    return

                # 状态机：选择音质
                if session_data["state"] == "quality":
                    quality_map = {
                        "": "alac",
                        " ": "alac",
                        "1": "alac",
                        "2": "aac",
                        "3": "atmos",
                        "alac": "alac",
                        "无损": "alac",
                        "aac": "aac",
                        "atmos": "atmos",
                        "杜比": "atmos",
                    }

                    selected_quality = quality_map.get(user_input.lower(), "alac")

                    # 调用下载流程
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

        # 直接下载（提供 URL）
        parsed = URLParser.parse(url)
        if not parsed or parsed.get("type") != "song":
            yield event.plain_result(
                "× 仅支持 Apple Music 单曲链接\n"
                "请使用包含 '?i=' 参数的单曲分享链接或 /song/ 路径的链接"
            )
            return

        # 处理音质参数
        dl_config = self.config.get("downloader_config", {})
        default_quality = dl_config.get("default_quality", "alac")

        quality_map = {
            "": default_quality,
            "alac": "alac",
            "无损": "alac",
            "lossless": "alac",
            "aac": "aac",
            "atmos": "atmos",
            "杜比": "atmos",
            "dolby": "atmos",
        }
        quality_str = quality_map.get(quality.lower(), default_quality)

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
            "atmos": "杜比全景声",
        }.get(quality_str, quality_str)

        # 获取歌曲信息
        storefront = parsed.get("storefront")
        if not storefront:
            dl_config = self.config.get("downloader_config", {})
            storefront = dl_config.get("storefront", "cn")

        song_name = None
        if parsed.get("song_id"):
            song_name = await MetadataFetcher.get_song_info(
                parsed["song_id"], storefront
            )

        sender_name = event.get_sender_name()
        sender_id = event.get_sender_id()

        # 检查用户任务数限制
        user_tasks = self._queue.get_user_tasks(sender_id)
        pending_tasks = [t for t in user_tasks if t.status == TaskStatus.PENDING]
        if len(pending_tasks) >= self._max_tasks_per_user:
            await event.send(
                event.plain_result(
                    f"× 您已有 {len(pending_tasks)} 个任务在排队\n"
                    f"每用户最多 {self._max_tasks_per_user} 个排队任务\n"
                    f"请等待现有任务完成，或使用 /am_cancel 取消任务"
                )
            )
            return

        # 加入队列
        success, msg, task = await self._queue.enqueue(
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

        # 构建响应消息
        position = self._queue.get_position(task.task_id)
        song_info = f"【{song_name}】" if song_name else ""

        if position == 1 and not self._queue.is_processing:
            # 队列为空，即将开始
            await event.send(
                event.plain_result(
                    f"♪ 下载任务已创建{song_info}\n"
                    f"> 音质: {quality_display}\n"
                    f"> 任务ID: {task.task_id}\n"
                    f"○ 即将开始下载..."
                )
            )
        else:
            # 需要排队
            await event.send(
                event.plain_result(
                    f"○ 已加入下载队列{song_info}\n"
                    f"> 音质: {quality_display}\n"
                    f"> 任务ID: {task.task_id}\n"
                    f"# 队列位置: 第 {position} 位\n"
                    f"* 请耐心等待，下载开始时会通知您"
                )
            )

    # ==================== 队列管理命令 ====================

    @filter.command("am_queue", alias={"am队列", "amq"})
    async def show_queue(self, event: AstrMessageEvent):
        """查看下载队列状态"""
        status = self._queue.format_queue_status()
        yield event.plain_result(status)

    @filter.command("am_cancel", alias={"am取消"})
    async def cancel_task(self, event: AstrMessageEvent, task_id: str = ""):
        """取消下载任务

        用法:
          /am_cancel <任务ID>  - 取消指定任务
          /am_cancel all       - 取消所有自己的任务
        """
        if not self._allow_cancel:
            yield event.plain_result("× 管理员已禁用任务取消功能")
            return

        sender_id = event.get_sender_id()

        if not task_id:
            # 显示用户的任务列表
            user_tasks = self._queue.get_user_tasks(sender_id)
            pending = [t for t in user_tasks if t.status == TaskStatus.PENDING]

            if not pending:
                yield event.plain_result("○ 您没有等待中的任务")
                return

            lines = ["您的等待任务:", ""]
            for task in pending:
                song_info = f"《{task.song_name}》" if task.song_name else ""
                position = self._queue.get_position(task.task_id)
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
            count, msg = await self._queue.cancel_user_tasks(sender_id)
            yield event.plain_result(f"{'√' if count > 0 else '○'} {msg}")
            return

        success, msg = await self._queue.cancel_task(task_id, sender_id)
        yield event.plain_result(f"{'√' if success else '×'} {msg}")

    @filter.command("am_mytasks", alias={"am我的任务", "amt"})
    async def show_my_tasks(self, event: AstrMessageEvent):
        """查看我的下载任务"""
        sender_id = event.get_sender_id()
        tasks = self._queue.get_user_tasks(sender_id)

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
                pos = self._queue.get_position(task.task_id)
                position = f" (队列位置: {pos})"

            lines.append(f"{status_icon} {task.task_id}: {song_info}{position}")

        yield event.plain_result("\n".join(lines))

    # ==================== 服务管理命令 ====================

    @filter.command("am_status", alias={"am状态"})
    async def check_status(self, event: AstrMessageEvent):
        """查看服务状态"""
        if not self.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        status = await self.docker_service.get_service_status()

        if status.error:
            yield event.plain_result(f"× 服务异常: {status.error}")
            return

        # 队列统计
        queue_stats = self._queue.get_stats()

        status_lines = [
            "* Apple Music Downloader 服务状态",
            "─" * 30,
            "",
            "【Docker 服务】",
            f"> Wrapper 镜像: {'√ 已构建' if status.wrapper_image_exists else '× 未构建'}",
            f"> 下载器镜像: {'√ 已构建' if status.downloader_image_exists else '× 未构建'}",
            f"> Wrapper 服务: {'√ 运行中' if status.wrapper_running else '- 未运行'}",
        ]

        if status.wrapper_running:
            status_lines.extend(
                [
                    f"> 解密端口: {'√ 正常' if status.decrypt_port_listening else '! 未就绪'}",
                    f"> M3U8端口: {'√ 正常' if status.m3u8_port_listening else '! 未就绪'}",
                ]
            )

        status_lines.extend(
            [
                "",
                "【下载队列】",
                f"> 队列容量: {queue_stats.pending_tasks}/{self._queue._max_queue_size}",
                f"> 正在处理: {'是' if self._queue.is_processing else '否'}",
                f"> 累计完成: {queue_stats.completed_tasks}",
                f"> 累计失败: {queue_stats.failed_tasks}",
            ]
        )

        if queue_stats.total_tasks > 0:
            status_lines.extend(
                [
                    f"> 平均等待: {queue_stats.avg_wait_time:.1f}s",
                    f"> 平均耗时: {queue_stats.avg_process_time:.1f}s",
                ]
            )

        yield event.plain_result("\n".join(status_lines))

    @filter.command("am_start", alias={"am启动"})
    async def start_service(self, event: AstrMessageEvent):
        """启动 Wrapper 服务"""
        if not self.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        yield event.plain_result("... 正在启动服务...")

        success, msg = await self.docker_service.start_wrapper()

        if success:
            yield event.plain_result(f"√ {msg}")
        else:
            yield event.plain_result(f"× {msg}")

    @filter.command("am_stop", alias={"am停止"})
    async def stop_service(self, event: AstrMessageEvent):
        """停止 Wrapper 服务"""
        if not self.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        success, msg = await self.docker_service.stop_wrapper()

        if success:
            yield event.plain_result(f"√ {msg}")
        else:
            yield event.plain_result(f"× {msg}")

    @filter.command("am_build", alias={"am构建"})
    async def build_images(self, event: AstrMessageEvent, target: str = "all"):
        """构建 Docker 镜像

        用法: /am_build [目标]
        目标: all / wrapper / downloader
        """
        if not self.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        target = target.lower()

        if target in ("all", "wrapper"):
            yield event.plain_result("> 正在构建 Wrapper 镜像（可能需要几分钟）...")
            success, msg = await self.docker_service.build_wrapper_image()
            yield event.plain_result(f"{'√' if success else '×'} Wrapper: {msg}")

        if target in ("all", "downloader"):
            yield event.plain_result("> 正在构建下载器镜像（首次可能需要5-10分钟）...")
            success, msg = await self.docker_service.build_downloader_image()
            yield event.plain_result(f"{'√' if success else '×'} 下载器: {msg}")

    @filter.command("am_help", alias={"am帮助", "am?"})
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """♪ Apple Music Downloader 使用帮助
                        > 下载指令:
                        /am                  - 交互式下载
                        /am <链接> [音质]     - 直接下载
                        音质可选: alac(无损) / aac / atmos(杜比)

                        > 示例:
                        /am https://music.apple.com/cn/album/xxx/123?i=456
                        /am https://...?i=456 atmos

                        > 队列管理:
                        /am_queue    - 查看下载队列
                        /am_mytasks  - 查看我的任务
                        /am_cancel   - 取消下载任务

                        > 服务管理:
                        /am_status  - 查看服务状态
                        /am_start   - 启动服务
                        /am_stop    - 停止服务
                        /am_build   - 构建镜像
                        /am_clean   - 手动清理下载文件

                        * 支持的链接类型:
                        • 仅支持单曲链接 (带 ?i= 参数或 /song/ 路径)
                    """

        yield event.plain_result(help_text)

    # ==================== 文件管理 ====================

    @filter.command("am_clean", alias={"am清理"})
    async def clean_downloads(self, event: AstrMessageEvent, force: str = ""):
        """手动清理下载文件

        参数:
        force: 输入 "sudo" 可强制使用 Docker 清理
        """
        is_force = force.lower() == "sudo"

        if is_force:
            yield event.plain_result("> 正在尝试强制清理...")

            if not self.docker_service:
                yield event.plain_result("× 服务未初始化")
                return

            download_dirs = self.docker_service.get_download_dirs()
            if not download_dirs:
                yield event.plain_result("√ 未找到下载目录配置")
                return

            success_count = 0
            fail_count = 0
            total_items_cleaned = 0

            for d in download_dirs:
                if not d.exists():
                    continue

                success, msg, count = await self.docker_service.force_clean(d)
                if success:
                    success_count += 1
                    total_items_cleaned += count
                else:
                    fail_count += 1
                    logger.warning(f"强制清理失败 {d}: {msg}")

            if fail_count == 0:
                if total_items_cleaned > 0:
                    yield event.plain_result(
                        f"√ 强制清理完成，共删除 {total_items_cleaned} 个项目"
                    )
                else:
                    yield event.plain_result("√ 强制清理完成，目录已为空")
            else:
                yield event.plain_result("部分清理失败，请检查日志")
            return

        yield event.plain_result("> 正在清理下载文件...")

        cleaned_count, error_count = await self._cleanup_downloads(force_all=True)

        msg = []
        if cleaned_count > 0:
            msg.append(f"√ 清理完成，共删除 {cleaned_count} 个项目")

        if error_count > 0:
            msg.append(f"有 {error_count} 个文件清理失败（可能被占用或权限不足）")
            msg.append("* 可尝试使用 /am_clean sudo 进行强制清理")

        if cleaned_count == 0 and error_count == 0:
            msg.append("√ 下载目录已为空，无需清理")

        yield event.plain_result("\n".join(msg))

    # ==================== 文件发送 ====================

    async def _send_downloaded_files_by_umo(
        self, unified_msg_origin: str, result: DownloadResult
    ) -> None:
        """通过 unified_msg_origin 发送下载的文件"""
        max_size = self.config.get("max_file_size_mb", 50) * 1024 * 1024

        # 发送封面
        if self.config.get("send_cover", True) and result.cover_path:
            if os.path.exists(result.cover_path):
                try:
                    cover_chain = MessageChain(chain=[Comp.Image.fromFileSystem(result.cover_path)])
                    await self.context.send_message(unified_msg_origin, cover_chain)
                except Exception as e:
                    logger.warning(f"发送封面失败: {e}")

        # 发送音频文件
        for file_path in result.file_paths[:5]:
            if not os.path.exists(file_path):
                continue

            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)

            if file_size > max_size:
                chain = MessageChain(chain=[Comp.Plain(
                    f"> {file_name}\n"
                    f"! 文件过大 ({file_size / 1024 / 1024:.1f}MB)，已保存到服务器"
                )])
                await self.context.send_message(unified_msg_origin, chain)
                continue

            try:
                file_chain = MessageChain(chain=[Comp.File(file=file_path, name=file_name)])
                await self.context.send_message(unified_msg_origin, file_chain)
            except Exception as e:
                logger.warning(f"发送文件失败 {file_name}: {e}")
                try:
                    if file_path.endswith((".m4a", ".mp3")):
                        record_chain = MessageChain(chain=[Comp.Record(file=file_path, url=file_path)])
                        await self.context.send_message(
                            unified_msg_origin, record_chain
                        )
                except Exception:
                    chain = MessageChain(chain=[Comp.Plain(
                        f"> {file_name} 发送失败，已保存到服务器"
                    )])
                    await self.context.send_message(unified_msg_origin, chain)

        if len(result.file_paths) > 5:
            chain = MessageChain(chain=[Comp.Plain(
                f"> 还有 {len(result.file_paths) - 5} 个文件已保存到服务器"
            )])
            await self.context.send_message(unified_msg_origin, chain)

    # ==================== 定时清理 ====================

    async def _periodic_cleanup(self):
        """定时清理下载文件的后台任务"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self._cleanup_downloads()

                # 清理队列历史索引
                self._queue.cleanup_old_tasks()

            except asyncio.CancelledError:
                logger.info("定时清理任务被取消")
                break
            except Exception as e:
                logger.error(f"定时清理任务出错: {e}")
                await asyncio.sleep(60)

    async def _cleanup_downloads(self, force_all: bool = False):
        """
        清理过期的下载文件
        """
        if not self.docker_service:
            logger.warning("Docker 服务未初始化，无法清理下载目录")
            return 0, 0

        try:
            download_dirs = self.docker_service.get_download_dirs()
        except Exception as e:
            logger.error(f"获取下载目录失败: {e}")
            return 0, 0

        if not download_dirs:
            logger.info("未找到下载目录配置，无需清理")
            return 0, 0

        cleaned_count = 0
        error_count = 0
        skipped_count = 0
        now = time.time()

        for downloads_dir in download_dirs:
            try:
                if not downloads_dir.exists():
                    logger.debug(f"下载目录不存在，跳过: {downloads_dir}")
                    continue

                items = list(downloads_dir.iterdir())
                items = [i for i in items if i.name != ".gitkeep"]

                if not items:
                    continue

                for item in items:
                    try:
                        mtime = item.stat().st_mtime
                        age = now - mtime

                        if not force_all and age < self._file_ttl:
                            skipped_count += 1
                            continue

                        if item.is_file() or item.is_symlink():
                            item.unlink()
                        elif item.is_dir():
                            shutil.rmtree(item)
                        cleaned_count += 1
                    except PermissionError:
                        error_count += 1
                        logger.warning(f"权限不足，无法清理 {item}")
                    except Exception as e:
                        error_count += 1
                        logger.warning(f"清理文件失败 {item}: {e}")
            except Exception as e:
                logger.warning(f"清理目录 {downloads_dir} 时出错: {e}")
                continue

        if cleaned_count > 0:
            logger.info(f"定时清理完成，共清理 {cleaned_count} 个过期文件/文件夹")
        elif error_count > 0:
            logger.warning(f"清理结束，但有 {error_count} 个文件清理失败")
        elif skipped_count > 0:
            logger.debug(f"清理检查完成，{skipped_count} 个文件未过期，暂不清理")
        else:
            logger.debug("下载目录已为空，无需清理")

        return cleaned_count, error_count
