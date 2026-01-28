"""
Apple Music Downloader - AstrBot 插件
"""

from pathlib import Path
from typing import Optional

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register

from .core import PluginConfig
from .handlers import (
    QueueCallbacks,
    DownloadHandler,
    FileManager,
    QueueCommandsHandler,
    ServiceCommandsHandler,
    AccountHandler,
)
from .services import (
    DownloaderService,
    WrapperService,
    DownloadQueue,
)


@register(
    "astrbot_plugin_applemusicdownloader",
    "UE-DND",
    "Apple Music Downloader",
    "0.2.0",
    "https://github.com/UE-DND/apple-music-downloader",
)
class AppleMusicDownloader(Star):
    """
    Apple Music Downloader 插件

    基于 AppleMusicDecrypt 重写，使用原生 Python + gRPC 直接连接 wrapper-manager。
    通过远程 wrapper-manager 提供下载服务。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).parent

        # 解析配置为内部配置对象
        self.plugin_config = PluginConfig.from_astrbot_config(dict(config), plugin_dir=self.plugin_dir)

        # 服务实例（延迟初始化）
        self.wrapper_service: Optional[WrapperService] = None
        self.downloader_service: Optional[DownloaderService] = None

        # 队列配置
        queue_config = config.get("queue_config", {})
        self._queue = DownloadQueue(
            max_size=queue_config.get("max_queue_size", 20),
            task_timeout=queue_config.get("task_timeout", 600),
        )
        self._notify_progress = queue_config.get("notify_progress", True)
        self._notify_queue_position = queue_config.get("notify_queue_position", False)
        self._allow_cancel = queue_config.get("allow_cancel", True)
        self._max_tasks_per_user = queue_config.get("max_tasks_per_user", 3)

        # 清理配置
        self._cleanup_interval = 60 * 60  # 1 小时
        self._file_ttl = 24 * 60 * 60     # 24 小时

        # 初始化处理器
        self._callbacks = QueueCallbacks(self)
        self._download_handler = DownloadHandler(self)
        self.file_manager = FileManager(self)
        self._queue_commands = QueueCommandsHandler(self)
        self._service_commands = ServiceCommandsHandler(self)
        self._account_handler = AccountHandler(self)

    async def initialize(self):
        """插件初始化"""
        logger.info("Apple Music Downloader  插件初始化中...")

        # 初始化 Wrapper 服务
        self.wrapper_service = WrapperService(self.plugin_config)

        # 初始化下载器服务
        self.downloader_service = DownloaderService(
            config=self.plugin_config,
            wrapper_service=self.wrapper_service,
        )

        # 初始化下载器
        success, msg = await self.downloader_service.init()
        if success:
            logger.info(f"下载器服务初始化成功: {msg}")
        else:
            logger.warning(f"下载器服务初始化失败: {msg}")

        # 自动启动 Wrapper 服务
        if self.config.get("auto_start_wrapper", True):
            status = await self.wrapper_service.get_status()
            if not status.connected:
                start_success, start_msg = await self.wrapper_service.start()
                if start_success:
                    logger.info(f"Wrapper 服务已自动启动: {start_msg}")
                    # 重新初始化连接
                    await self.wrapper_service.init()
                else:
                    logger.warning(f"Wrapper 自动启动失败: {start_msg}")

        # 配置队列回调
        self._queue.set_download_function(self._callbacks.execute_download)
        self._queue.on_started(self._callbacks.on_task_start)
        self._queue.on_completed(self._callbacks.on_task_complete)
        self._queue.on_failed(self._callbacks.on_task_failed)

        # 启动队列处理器
        await self._queue.start()
        logger.info("下载队列处理器已启动")

        # 启动文件清理任务
        self.file_manager.start_cleanup_task()

        logger.info("Apple Music Downloader  插件初始化完成")

    async def terminate(self):
        """插件销毁"""
        logger.info("Apple Music Downloader  插件正在关闭...")

        # 停止队列处理器
        await self._queue.stop()
        logger.info("下载队列处理器已停止")

        # 停止文件清理任务
        await self.file_manager.stop_cleanup_task()

        # 关闭下载器服务
        if self.downloader_service:
            await self.downloader_service.close()

        # 关闭 Wrapper 服务
        if self.wrapper_service:
            await self.wrapper_service.close()

        logger.info("Apple Music Downloader  插件已关闭")


    @filter.command("am", alias={"applemusic", "apple"})
    async def download_music(
        self, event: AstrMessageEvent, url: str = "", quality: str = ""
    ):
        """下载 Apple Music 单曲

        用法: /am <链接> [音质]
        音质可选: alac(无损) / aac
        """
        async for result in self._download_handler.handle_download(event, url, quality):
            yield result


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

    @filter.command("am_help", alias={"am帮助", "am?"})
    async def show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        async for result in self._service_commands.handle_show_help(event):
            yield result


    @filter.command("am_clean", alias={"am清理"})
    async def clean_downloads(self, event: AstrMessageEvent, force: str = ""):
        """手动清理下载文件

        参数:
        force: 输入 "sudo" 可强制清理所有文件
        """
        async for result in self.file_manager.handle_clean_command(event, force):
            yield result


    @filter.command("am_login", alias={"am登录"})
    async def login_account(self, event: AstrMessageEvent, username: str = "", password: str = ""):
        """登录 Apple Music 账户

        用法:
          /am_login <用户名> <密码>  - 使用用户名密码登录
          /am_login                  - 显示帮助信息
        """
        async for result in self._account_handler.handle_login(event, username, password):
            yield result

    @filter.command("am_2fa", alias={"am验证"})
    async def verify_2fa(self, event: AstrMessageEvent, code: str = ""):
        """输入双因素验证码

        用法: /am_2fa <验证码>
        """
        async for result in self._account_handler.handle_2fa_code(event, code):
            yield result

    @filter.command("am_logout", alias={"am登出"})
    async def logout_account(self, event: AstrMessageEvent, username: str = ""):
        """登出 Apple Music 账户

        用法: /am_logout <用户名>
        """
        async for result in self._account_handler.handle_logout(event, username):
            yield result

    @filter.command("am_accounts", alias={"am账户", "am账号"})
    async def show_accounts(self, event: AstrMessageEvent):
        """查看已登录的账户"""
        async for result in self._account_handler.handle_accounts(event):
            yield result
