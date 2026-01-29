"""
文件管理器 - 文件发送与清理

"""

from __future__ import annotations
import os
import asyncio
import shutil
import time
from typing import TYPE_CHECKING, Tuple

from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api import logger
import astrbot.api.message_components as Comp

from ..services import DownloadResult

if TYPE_CHECKING:
    from ..main import AppleMusicDownloader


class FileManager:
    """文件发送与清理管理"""

    def __init__(self, plugin: "AppleMusicDownloader"):
        self._plugin = plugin
        self._cleanup_task: asyncio.Task = None

    @property
    def _cleanup_interval(self) -> int:
        return self._plugin._cleanup_interval

    @property
    def _file_ttl(self) -> int:
        return self._plugin._file_ttl

    def start_cleanup_task(self) -> None:
        """启动定时清理任务"""
        self._cleanup_task = asyncio.create_task(self._periodic_cleanup())
        logger.info("已启动定时清理任务（每小时检查，删除超过24小时的文件）")

    async def stop_cleanup_task(self) -> None:
        """停止定时清理任务"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            logger.info("定时清理任务已停止")

    async def send_downloaded_files(
        self, unified_msg_origin: str, result: DownloadResult
    ) -> None:
        """发送下载的文件"""
        file_config = self._plugin.plugin_config.file
        max_size = file_config.max_file_size_mb * 1024 * 1024

        # 发送封面
        if file_config.send_cover and result.cover_path:
            if os.path.exists(result.cover_path):
                try:
                    cover_chain = MessageChain(
                        chain=[Comp.Image.fromFileSystem(result.cover_path)]
                    )
                    await self._plugin.context.send_message(
                        unified_msg_origin, cover_chain
                    )
                except (RuntimeError, ValueError, OSError) as exc:
                    logger.warning(
                        "发送封面失败 stage=send_cover file_path=%s origin=%s exc_type=%s",
                        result.cover_path,
                        unified_msg_origin,
                        type(exc).__name__,
                        exc_info=True,
                    )

        # 发送音频文件
        for file_path in result.file_paths[:5]:
            if not os.path.exists(file_path):
                continue

            file_size = os.path.getsize(file_path)
            file_name = os.path.basename(file_path)

            if file_size > max_size:
                chain = MessageChain(
                    chain=[
                        Comp.Plain(
                            f"> {file_name}\n"
                            f"! 文件过大 ({file_size / 1024 / 1024:.1f}MB)，已保存到服务器"
                        )
                    ]
                )
                await self._plugin.context.send_message(unified_msg_origin, chain)
                continue

            try:
                file_chain = MessageChain(
                    chain=[Comp.File(file=file_path, name=file_name)]
                )
                await self._plugin.context.send_message(unified_msg_origin, file_chain)
            except (RuntimeError, ValueError, OSError) as exc:
                logger.warning(
                    "发送文件失败 stage=send_file file_name=%s file_path=%s origin=%s exc_type=%s",
                    file_name,
                    file_path,
                    unified_msg_origin,
                    type(exc).__name__,
                    exc_info=True,
                )
                try:
                    if file_path.endswith((".m4a", ".mp3")):
                        record_chain = MessageChain(
                            chain=[Comp.Record(file=file_path, url=file_path)]
                        )
                        await self._plugin.context.send_message(
                            unified_msg_origin, record_chain
                        )
                except (RuntimeError, ValueError, OSError) as record_exc:
                    logger.warning(
                        "发送音频记录失败 stage=send_record file_name=%s file_path=%s origin=%s exc_type=%s",
                        file_name,
                        file_path,
                        unified_msg_origin,
                        type(record_exc).__name__,
                        exc_info=True,
                    )
                    chain = MessageChain(
                        chain=[Comp.Plain(f"> {file_name} 发送失败，已保存到服务器")]
                    )
                    try:
                        await self._plugin.context.send_message(unified_msg_origin, chain)
                    except (RuntimeError, ValueError, OSError) as fallback_exc:
                        logger.warning(
                            "发送失败提示异常 stage=fallback file_name=%s origin=%s exc_type=%s",
                            file_name,
                            unified_msg_origin,
                            type(fallback_exc).__name__,
                            exc_info=True,
                        )

        if len(result.file_paths) > 5:
            chain = MessageChain(
                chain=[
                    Comp.Plain(f"> 还有 {len(result.file_paths) - 5} 个文件已保存到服务器")
                ]
            )
            await self._plugin.context.send_message(unified_msg_origin, chain)

    async def handle_clean_command(
        self, event: AstrMessageEvent, force: str = ""
    ):
        """处理清理命令"""
        is_force = force.lower() == "sudo"

        if is_force:
            yield event.plain_result("> 正在尝试强制清理...")

            if not self._plugin.downloader_service:
                yield event.plain_result("× 服务未初始化")
                return

            download_dirs = self._plugin.downloader_service.get_download_dirs()
            if not download_dirs:
                yield event.plain_result("√ 未找到下载目录配置")
                return

            success_count = 0
            fail_count = 0
            total_items_cleaned = 0

            for d in download_dirs:
                if not d.exists():
                    continue

                try:
                    items = list(d.iterdir())
                    items = [i for i in items if i.name != ".gitkeep"]

                    for item in items:
                        try:
                            if item.is_file() or item.is_symlink():
                                item.unlink()
                            elif item.is_dir():
                                shutil.rmtree(item)
                            total_items_cleaned += 1
                        except Exception as e:
                            fail_count += 1
                            logger.warning(f"强制清理失败 {item}: {e}")

                    success_count += 1
                except Exception as e:
                    fail_count += 1
                    logger.warning(f"强制清理目录失败 {d}: {e}")

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

        cleaned_count, error_count = await self.cleanup_downloads(force_all=True)

        msg = []
        if cleaned_count > 0:
            msg.append(f"√ 清理完成，共删除 {cleaned_count} 个项目")

        if error_count > 0:
            msg.append(f"有 {error_count} 个文件清理失败（可能被占用或权限不足）")
            msg.append("* 可尝试使用 /am_clean sudo 进行强制清理")

        if cleaned_count == 0 and error_count == 0:
            msg.append("√ 下载目录已为空，无需清理")

        yield event.plain_result("\n".join(msg))

    async def _periodic_cleanup(self) -> None:
        """定时清理后台任务"""
        while True:
            try:
                await asyncio.sleep(self._cleanup_interval)
                await self.cleanup_downloads()
            except asyncio.CancelledError:
                logger.info("定时清理任务被取消")
                break
            except Exception as e:
                logger.error(f"定时清理任务出错: {e}")
                await asyncio.sleep(60)

    async def cleanup_downloads(self, force_all: bool = False) -> Tuple[int, int]:
        """清理过期的下载文件"""
        if not self._plugin.downloader_service:
            logger.warning("下载服务未初始化，无法清理下载目录")
            return 0, 0

        try:
            download_dirs = self._plugin.downloader_service.get_download_dirs()
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
