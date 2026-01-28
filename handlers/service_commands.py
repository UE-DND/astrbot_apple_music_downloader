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
        if not self._plugin.downloader_service:
            yield event.plain_result("× 服务未初始化")
            return

        status = await self._plugin.downloader_service.get_status()
        queue_stats = self._plugin._queue.get_stats()

        status_lines = [
            "* Apple Music Downloader 服务状态",
            "─" * 30,
            "",
            "【Wrapper 服务】",
            "> 模式: remote",
            f"> 地址: {status.wrapper_url}",
            f"> 状态: {'√ 已连接' if status.wrapper_connected else '× 未连接'}",
        ]

        if status.wrapper_connected and status.wrapper_regions:
            status_lines.append(f"> 可用地区: {', '.join(status.wrapper_regions)}")

        if status.error:
            status_lines.append(f"> 错误: {status.error}")

        status_lines.extend([
            "",
            "【API 客户端】",
            f"> 状态: {'√ 就绪' if status.api_available else '× 未就绪'}",
        ])

        status_lines.extend([
            "",
            "【下载队列】",
            f"> 队列容量: {queue_stats.pending_tasks}/{self._plugin._queue.max_size}",
            f"> 正在处理: {'是' if self._plugin._queue.is_running else '否'}",
            f"> 累计完成: {queue_stats.completed_tasks}",
            f"> 累计失败: {queue_stats.failed_tasks}",
        ])

        if queue_stats.total_tasks > 0:
            status_lines.extend([
                f"> 平均等待: {queue_stats.avg_wait_time:.1f}s",
                f"> 平均耗时: {queue_stats.avg_process_time:.1f}s",
            ])

        yield event.plain_result("\n".join(status_lines))

    async def handle_start_service(self, event: AstrMessageEvent):
        """启动 Wrapper 服务"""
        if not self._plugin.wrapper_service:
            yield event.plain_result("× 服务未初始化")
            return

        yield event.plain_result("... 正在启动服务...")

        success, msg = await self._plugin.wrapper_service.start()

        if success:
            # 重新连接
            connect_success, connect_msg = await self._plugin.wrapper_service.init()
            if connect_success:
                yield event.plain_result(f"√ {msg}\n√ {connect_msg}")
            else:
                yield event.plain_result(f"√ {msg}\n× 连接失败: {connect_msg}")
        else:
            yield event.plain_result(f"× {msg}")

    async def handle_stop_service(self, event: AstrMessageEvent):
        """停止 Wrapper 服务"""
        if not self._plugin.wrapper_service:
            yield event.plain_result("× 服务未初始化")
            return

        success, msg = await self._plugin.wrapper_service.stop()

        if success:
            yield event.plain_result(f"√ {msg}")
        else:
            yield event.plain_result(f"× {msg}")

    async def handle_show_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        help_text = """♪ Apple Music Downloader  使用帮助

> 账户管理:
/am_login <用户名> <密码>  - 登录 Apple Music 账户
/am_2fa <验证码>           - 输入双因素验证码
/am_logout <用户名>        - 登出账户
/am_accounts              - 查看已登录账户

> 下载指令:
/am                  - 交互式下载
/am <链接> [音质]     - 直接下载
音质可选: alac(无损) / aac

> 示例:
/am https://music.apple.com/cn/album/xxx/123?i=456
/am https://...?i=456 aac

> 队列管理:
/am_queue    - 查看下载队列
/am_mytasks  - 查看我的任务
/am_cancel   - 取消下载任务

> 服务管理:
/am_status  - 查看服务状态
/am_start   - 启动服务
/am_stop    - 停止服务
/am_clean   - 手动清理下载文件

* 支持的链接类型:
• 仅支持单曲链接 (带 ?i= 参数或 /song/ 路径)

* Wrapper 连接模式:
• remote - 远程服务模式（连接远程 wrapper-manager）

*  新特性:
• 支持运行时添加账户（无需重启服务）
• 支持双因素身份验证 (2FA)
• 多账户管理，自动区域检测"""

        yield event.plain_result(help_text)
