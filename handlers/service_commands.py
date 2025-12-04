"""
服务管理命令处理器
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from astrbot.api.event import AstrMessageEvent

if TYPE_CHECKING:
    from ..main import AppleMusicDownloader


class ServiceCommandsHandler:
    """服务管理命令处理"""

    def __init__(self, plugin: "AppleMusicDownloader"):
        self._plugin = plugin

    async def handle_check_status(self, event: AstrMessageEvent):
        """查看服务状态"""
        if not self._plugin.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        status = await self._plugin.docker_service.get_service_status()

        if status.error:
            yield event.plain_result(f"× 服务异常: {status.error}")
            return

        queue_stats = self._plugin._queue.get_stats()

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
                f"> 队列容量: {queue_stats.pending_tasks}/{self._plugin._queue._max_queue_size}",
                f"> 正在处理: {'是' if self._plugin._queue.is_processing else '否'}",
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

    async def handle_start_service(self, event: AstrMessageEvent):
        """启动 Wrapper 服务"""
        if not self._plugin.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        yield event.plain_result("... 正在启动服务...")

        success, msg = await self._plugin.docker_service.start_wrapper()

        if success:
            yield event.plain_result(f"√ {msg}")
        else:
            yield event.plain_result(f"× {msg}")

    async def handle_stop_service(self, event: AstrMessageEvent):
        """停止 Wrapper 服务"""
        if not self._plugin.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        success, msg = await self._plugin.docker_service.stop_wrapper()

        if success:
            yield event.plain_result(f"√ {msg}")
        else:
            yield event.plain_result(f"× {msg}")

    async def handle_build_images(self, event: AstrMessageEvent, target: str = "all"):
        """构建 Docker 镜像"""
        if not self._plugin.docker_service:
            yield event.plain_result("× 服务未初始化")
            return

        target = target.lower()

        if target in ("all", "wrapper"):
            yield event.plain_result("> 正在构建 Wrapper 镜像（可能需要几分钟）...")
            success, msg = await self._plugin.docker_service.build_wrapper_image()
            yield event.plain_result(f"{'√' if success else '×'} Wrapper: {msg}")

        if target in ("all", "downloader"):
            yield event.plain_result("> 正在构建下载器镜像（首次可能需要5-10分钟）...")
            success, msg = await self._plugin.docker_service.build_downloader_image()
            yield event.plain_result(f"{'√' if success else '×'} 下载器: {msg}")

    async def handle_show_help(self, event: AstrMessageEvent):
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
• 仅支持单曲链接 (带 ?i= 参数或 /song/ 路径)"""

        yield event.plain_result(help_text)
