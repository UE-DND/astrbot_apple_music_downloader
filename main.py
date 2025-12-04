"""
Apple Music Downloader - AstrBot 插件
"""

from pathlib import Path
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig

from .services import DockerService, DownloadQueue
from .handlers import (
    QueueCallbacks,
    DownloadHandler,
    FileManager,
    QueueCommandsHandler,
    ServiceCommandsHandler,
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

        # 清理配置
        self._cleanup_interval = 60 * 60
        self._file_ttl = 24 * 60 * 60

        # 初始化处理器
        self._callbacks = QueueCallbacks(self)
        self._download_handler = DownloadHandler(self)
        self.file_manager = FileManager(self)
        self._queue_commands = QueueCommandsHandler(self)
        self._service_commands = ServiceCommandsHandler(self)

    async def initialize(self):
        """插件初始化"""
        logger.info("Apple Music Downloader 插件初始化中...")

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
        self._queue.set_download_handler(self._callbacks.execute_download)
        self._queue.on_task_start(self._callbacks.on_task_start)
        self._queue.on_task_complete(self._callbacks.on_task_complete)
        self._queue.on_task_failed(self._callbacks.on_task_failed)
        if self._notify_queue_position:
            self._queue.on_queue_position_changed(
                self._callbacks.on_queue_position_changed
            )

        await self._queue.start_processor()
        logger.info("下载队列处理器已启动")

        self.file_manager.start_cleanup_task()

        logger.info("Apple Music Downloader 插件初始化完成")

    async def terminate(self):
        """插件销毁"""
        logger.info("Apple Music Downloader 插件正在关闭...")

        await self._queue.stop_processor()
        logger.info("下载队列处理器已停止")

        await self.file_manager.stop_cleanup_task()

    # ==================== 下载命令 ====================

    @filter.command("am", alias={"applemusic", "apple"})
    async def download_music(
        self, event: AstrMessageEvent, url: str = "", quality: str = ""
    ):
        """下载 Apple Music 单曲

        用法: /am <链接> [音质]
        音质可选: alac(无损) / aac / atmos(杜比)
        """
        async for result in self._download_handler.handle_download(event, url, quality):
            yield result

    # ==================== 队列管理命令 ====================

    @filter.command("am_queue", alias={"am队列", "amq"})
    async def show_queue(self, event: AstrMessageEvent):
        """查看下载队列状态"""
        async for result in self._queue_commands.handle_show_queue(event):
            yield result

    @filter.command("am_cancel", alias={"am取消"})
    async def cancel_task(self, event: AstrMessageEvent, task_id: str = ""):
        """取消下载任务

        用法:
          /am_cancel <任务ID>  - 取消指定任务
          /am_cancel all       - 取消所有自己的任务
        """
        async for result in self._queue_commands.handle_cancel_task(event, task_id):
            yield result

    @filter.command("am_mytasks", alias={"am我的任务", "amt"})
    async def show_my_tasks(self, event: AstrMessageEvent):
        """查看我的下载任务"""
        async for result in self._queue_commands.handle_show_my_tasks(event):
            yield result

    # ==================== 服务管理命令 ====================

    @filter.command("am_status", alias={"am状态"})
    async def check_status(self, event: AstrMessageEvent):
        """查看服务状态"""
        async for result in self._service_commands.handle_check_status(event):
            yield result

    @filter.command("am_start", alias={"am启动"})
    async def start_service(self, event: AstrMessageEvent):
        """启动 Wrapper 服务"""
        async for result in self._service_commands.handle_start_service(event):
            yield result

    @filter.command("am_stop", alias={"am停止"})
    async def stop_service(self, event: AstrMessageEvent):
        """停止 Wrapper 服务"""
        async for result in self._service_commands.handle_stop_service(event):
            yield result

    @filter.command("am_build", alias={"am构建"})
    async def build_images(self, event: AstrMessageEvent, target: str = "all"):
        """构建 Docker 镜像

        用法: /am_build [目标]
        目标: all / wrapper / downloader
        """
        async for result in self._service_commands.handle_build_images(event, target):
            yield result

    @filter.command("am_help", alias={"am帮助", "am?"})
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        async for result in self._service_commands.handle_show_help(event):
            yield result

    # ==================== 文件管理命令 ====================

    @filter.command("am_clean", alias={"am清理"})
    async def clean_downloads(self, event: AstrMessageEvent, force: str = ""):
        """手动清理下载文件

        参数:
        force: 输入 "sudo" 可强制使用 Docker 清理
        """
        async for result in self.file_manager.handle_clean_command(event, force):
            yield result
