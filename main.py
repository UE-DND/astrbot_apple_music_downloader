"""
Apple Music Downloader - AstrBot 插件
"""

import os
import asyncio
import shutil
import time
from pathlib import Path
from typing import Optional

from astrbot.api.event import filter, AstrMessageEvent, MessageEventResult
from astrbot.api.star import Context, Star, register
from astrbot.api import logger, AstrBotConfig
import astrbot.api.message_components as Comp

from .services import (
    DockerService,
    DownloadQuality,
    DownloadResult,
    URLParser,
    MetadataFetcher,
)


@register(
    "astrbot_plugin_applemusicdownloader",
    "UE-DND",
    "Apple Music Downloader",
    "0.0.1",
    "https://github.com/UE-DND/apple-music-downloader",
)
class AppleMusicDownloader(Star):
    """Apple Music 下载器插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).parent
        self.docker_service: Optional[DockerService] = None

        # 并发锁，限制同时只能有一个下载任务
        self._download_lock = asyncio.Lock()
        # 等待队列计数
        self._waiting_count = 0
        self._current_downloader: Optional[str] = None

        self._cleanup_task: Optional[asyncio.Task] = None
        self._cleanup_interval = 60 * 60  # 每小时检查一次
        self._file_ttl = 24 * 60 * 60  # 文件保留时间：24小时

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

        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.info("已启动定时清理任务（每小时检查，删除超过24小时的文件）")

        logger.info("Apple Music Downloader 插件初始化完成")

    async def terminate(self):
        """插件销毁"""
        logger.info("Apple Music Downloader 插件正在关闭...")

        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("定时清理任务已停止")

    async def _periodic_cleanup(self):
        """定时清理下载文件的后台任务"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)

                await self._cleanup_downloads()

            except asyncio.CancelledError:
                logger.info("定时清理任务被取消")
                break
            except Exception as e:
                logger.error(f"定时清理任务出错: {e}")
                await asyncio.sleep(60)

    async def _cleanup_downloads(self, force_all: bool = False):
        """清理过期的下载文件

        Args:
            force_all: 如果为 True，则删除所有文件（用于手动清理）；
                       否则只删除超过 24 小时的文件
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
                        # 获取文件/目录的修改时间
                        mtime = item.stat().st_mtime
                        age = now - mtime

                        # 如果不是强制删除，且文件未过期，则跳过
                        if not force_all and age < self._file_ttl:
                            skipped_count += 1
                            continue

                        # 删除过期文件/目录
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

    @filter.command("am", alias={"applemusic", "apple"})
    async def download_music(
        self, event: AstrMessageEvent, url: str, quality: str = ""
    ):
        dl_config = self.config.get("downloader_config", {})
        default_quality = dl_config.get("default_quality", "alac")

        parsed = URLParser.parse(url)
        if not parsed or parsed.get("type") != "song":
            yield event.plain_result(
                "× 仅支持 Apple Music 单曲链接\n"
                "请使用包含 '?i=' 参数的单曲分享链接或 /song/ 路径的链接"
            )
            return

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
        download_quality = DownloadQuality(quality_str)

        quality_display = {
            "alac": "无损 ALAC",
            "aac": "高品质 AAC",
            "atmos": "杜比全景声",
        }.get(quality_str, quality_str)

        storefront = parsed.get("storefront")
        if not storefront:
            storefront = dl_config.get("storefront", "cn")

        song_name = None
        if parsed.get("song_id"):
            song_name = await MetadataFetcher.get_song_info(
                parsed["song_id"], storefront
            )

        sender_name = event.get_sender_name()
        sender_id = event.get_sender_id()

        if self._download_lock.locked():
            self._waiting_count += 1
            wait_position = self._waiting_count
            yield event.plain_result(
                f"○ 当前有下载任务正在进行中\n"
                f"> 正在下载: {self._current_downloader or '未知用户'}\n"
                f"# 等待队列位置: 第 {wait_position} 位\n"
                f"* 请稍候，您的任务将自动开始..."
            )

        async with self._download_lock:
            if self._waiting_count > 0:
                self._waiting_count -= 1

            self._current_downloader = sender_name or sender_id

            try:
                yield event.plain_result(
                    f"♪ 开始下载单曲{f'【{song_name}】' if song_name else ''}\n"
                    f"> 音质: {quality_display}\n"
                    f"○ 请稍候..."
                )

                result = await self.docker_service.download(
                    url=url, quality=download_quality, single_song=True
                )

                if result.success:
                    success_msg = f"√ 下载完成！\n> 文件将在稍后发送，需要等待一段时间"
                    yield event.plain_result(success_msg)

                    await self._send_downloaded_files(event, result)
                else:
                    yield event.plain_result(
                        f"× 下载失败\n原因: {result.error or result.message}"
                    )

            except Exception as e:
                logger.error(f"下载过程出错: {e}")
                yield event.plain_result(f"× 下载出错: {str(e)}")
            finally:
                self._current_downloader = None

    async def _send_downloaded_files(
        self, event: AstrMessageEvent, result: DownloadResult
    ):
        """发送下载的文件"""
        max_size = self.config.get("max_file_size_mb", 50) * 1024 * 1024

        if self.config.get("send_cover", True) and result.cover_path:
            if os.path.exists(result.cover_path):
                try:
                    await event.send(event.image_result(result.cover_path))
                except Exception as e:
                    logger.warning(f"发送封面失败: {e}")

        for file_path in result.file_paths[:5]:
            if not os.path.exists(file_path):
                continue

            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)

            # 超过阈值的文件不直接发送，防止超时或报错
            if file_size > max_size:
                await event.send(
                    event.plain_result(
                        f"> {file_name}\n"
                        f"! 文件过大 ({file_size / 1024 / 1024:.1f}MB)，已保存到服务器"
                    )
                )
                continue

            try:
                file_comp = Comp.File(file=file_path, name=file_name)
                await event.send(event.chain_result([file_comp]))
            except Exception as e:
                logger.warning(f"发送文件失败 {file_name}: {e}")
                try:
                    if file_path.endswith((".m4a", ".mp3")):
                        record = Comp.Record(file=file_path, url=file_path)
                        await event.send(event.chain_result([record]))
                except Exception:
                    await event.send(
                        event.plain_result(f"> {file_name} 发送失败，已保存到服务器")
                    )

        if len(result.file_paths) > 5:
            await event.send(
                event.plain_result(
                    f"> 还有 {len(result.file_paths) - 5} 个文件已保存到服务器"
                )
            )

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

        status_lines = [
            "* Apple Music Downloader 服务状态",
            "─" * 30,
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
  /am 单曲链接 音质
  音质可选: alac(无损) / aac / atmos(杜比)

> 示例:
  /am https://music.apple.com/cn/album/xxx/123?i=456
  /am https://...?i=456 atmos

> 服务管理:
  /am_status  - 查看服务状态
  /am_start   - 启动服务
  /am_stop    - 停止服务
  /am_build   - 构建镜像
  /am_clean   - 手动清理下载文件

* 支持的链接类型:
  • 仅支持单曲链接 (带 ?i= 参数)

! 注意:
  • 首次使用需要构建 Docker 镜像
  • 下载文件每24小时自动清理
  • 一次只能进行一个下载任务"""

        yield event.plain_result(help_text)

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
                yield event.plain_result(f"部分清理失败，请检查日志")
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
