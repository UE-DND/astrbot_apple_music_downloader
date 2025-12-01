"""
Apple Music Downloader - Docker 服务管理模块

负责管理 Docker 容器的启动、停止和下载任务执行
"""

import asyncio
import os
import platform
import re
import shutil
import time
import json
import yaml
from pathlib import Path
from typing import Optional, Tuple, List, Dict, Any
from dataclasses import dataclass, field
from enum import Enum

from astrbot.api import logger


class DownloadQuality(Enum):
    ALAC = "alac"  # 无损
    AAC = "aac"  # 高品质 AAC
    ATMOS = "atmos"  # 杜比全景声


@dataclass
class DownloadResult:
    """下载结果"""

    success: bool
    message: str
    file_paths: List[str] = field(default_factory=list)
    cover_path: Optional[str] = None
    track_info: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@dataclass
class ServiceStatus:
    """服务状态"""

    wrapper_running: bool = False
    downloader_image_exists: bool = False
    wrapper_image_exists: bool = False
    decrypt_port_listening: bool = False
    m3u8_port_listening: bool = False
    error: Optional[str] = None


class ConfigGenerator:
    """配置文件生成器 - 将插件配置同步到下载器 config.yaml"""

    # 默认配置模板
    DEFAULT_CONFIG = {
        "media-user-token": "",
        "authorization-token": "your-authorization-token",
        "language": "",
        "lrc-type": "lyrics",
        "lrc-format": "lrc",
        "embed-lrc": True,
        "save-lrc-file": False,
        "save-artist-cover": False,
        "save-animated-artwork": False,
        "emby-animated-artwork": False,
        "embed-cover": True,
        "cover-size": "5000x5000",
        "cover-format": "jpg",
        "alac-save-folder": "AM-DL downloads",
        "atmos-save-folder": "AM-DL-Atmos downloads",
        "aac-save-folder": "AM-DL-AAC downloads",
        "max-memory-limit": 256,
        "decrypt-m3u8-port": "127.0.0.1:10020",
        "get-m3u8-port": "127.0.0.1:20020",
        "get-m3u8-from-device": True,
        "get-m3u8-mode": "hires",
        "aac-type": "aac-lc",
        "alac-max": 192000,
        "atmos-max": 2768,
        "limit-max": 200,
        "album-folder-format": "{AlbumName}",
        "playlist-folder-format": "{PlaylistName}",
        "song-file-format": "{SongNumer}. {SongName}",
        "artist-folder-format": "{UrlArtistName}",
        "explicit-choice": "[E]",
        "clean-choice": "[C]",
        "apple-master-choice": "[M]",
        "use-songinfo-for-playlist": False,
        "dl-albumcover-for-playlist": False,
        "mv-audio-type": "atmos",
        "mv-max": 2160,
        "storefront": "cn",
        "convert-after-download": True,
        "convert-format": "flac",
        "convert-keep-original": False,
        "convert-skip-if-source-matches": True,
        "ffmpeg-path": "ffmpeg",
        "convert-extra-args": "",
        "convert-warn-lossy-to-lossless": True,
        "convert-skip-lossy-to-lossless": True,
    }

    @classmethod
    def generate_config(cls, plugin_config: dict) -> dict:
        """根据插件配置生成下载器配置"""
        config = cls.DEFAULT_CONFIG.copy()

        dl_config = plugin_config.get("downloader_config", {})

        config_mapping = {
            "media_user_token": "media-user-token",
            "storefront": "storefront",
            "alac_max": "alac-max",
            "atmos_max": "atmos-max",
            "aac_type": "aac-type",
            "embed_lrc": "embed-lrc",
            "save_lrc_file": "save-lrc-file",
            "lrc_type": "lrc-type",
            "embed_cover": "embed-cover",
            "cover_size": "cover-size",
            "cover_format": "cover-format",
            "convert_after_download": "convert-after-download",
            "convert_format": "convert-format",
            "convert_keep_original": "convert-keep-original",
            "ffmpeg_path": "ffmpeg-path",
            "album_folder_format": "album-folder-format",
            "song_file_format": "song-file-format",
            "artist_folder_format": "artist-folder-format",
        }

        for plugin_key, config_key in config_mapping.items():
            if plugin_key in dl_config and dl_config[plugin_key] is not None:
                value = dl_config[plugin_key]
                if value != "":
                    config[config_key] = value
        wrapper_ports = plugin_config.get("wrapper_ports", {})
        decrypt_port = wrapper_ports.get("decrypt_port", 10020)
        m3u8_port = wrapper_ports.get("m3u8_port", 20020)
        config["decrypt-m3u8-port"] = f"127.0.0.1:{decrypt_port}"
        config["get-m3u8-port"] = f"127.0.0.1:{m3u8_port}"

        return config

    @classmethod
    def save_config(cls, config: dict, path: Path) -> bool:
        """保存配置到文件"""
        try:
            # 强制给特定字段加引号，适配下游工具要求
            FORCE_QUOTE_KEYS = {
                "media-user-token",
                "authorization-token",
                "language",
                "lrc-type",
                "lrc-format",
                "decrypt-m3u8-port",
                "get-m3u8-port",
                "album-folder-format",
                "playlist-folder-format",
                "song-file-format",
                "artist-folder-format",
                "explicit-choice",
                "clean-choice",
                "apple-master-choice",
                "storefront",
                "convert-format",
                "ffmpeg-path",
                "convert-extra-args",
            }

            class QuotedStr(str):
                pass

            def quoted_str_presenter(dumper, data):
                return dumper.represent_scalar("tag:yaml.org,2002:str", data, style='"')

            class CustomDumper(yaml.SafeDumper):
                pass

            CustomDumper.add_representer(QuotedStr, quoted_str_presenter)

            def process_config(data):
                if isinstance(data, dict):
                    new_data = {}
                    for k, v in data.items():
                        if k in FORCE_QUOTE_KEYS and isinstance(v, str):
                            new_data[k] = QuotedStr(v)
                        else:
                            new_data[k] = process_config(v)
                    return new_data
                elif isinstance(data, list):
                    return [process_config(item) for item in data]
                return data

            processed_config = process_config(config)

            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(
                    processed_config,
                    f,
                    Dumper=CustomDumper,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            return True
        except Exception as e:
            logger.error(f"保存配置文件失败: {e}")
            return False


class DockerService:
    """Docker 服务管理类"""

    WRAPPER_CONTAINER_NAME = "apple-music-wrapper"
    WRAPPER_IMAGE_NAME = "apple-music-wrapper"
    DOWNLOADER_IMAGE_NAME = "apple-music-downloader"

    def __init__(self, plugin_dir: str, config: dict):
        self.plugin_dir = Path(plugin_dir)
        self.config = config
        self.downloader_path = self._resolve_downloader_path()
        self.docker_host = config.get("docker_host", "")
        self.decrypt_port = config.get("wrapper_ports", {}).get("decrypt_port", 10020)
        self.m3u8_port = config.get("wrapper_ports", {}).get("m3u8_port", 20020)
        self.cache_ttl = 7 * 24 * 3600  # 7 天

        self.debug_mode = config.get("debug_mode", False)
        self.docker_log_lines = config.get("docker_log_lines", 100)

        self._sync_config()

    def _resolve_downloader_path(self) -> Path:
        """解析下载器目录路径"""
        dl_path = self.config.get("downloader_path", "apple-music-downloader")
        if os.path.isabs(dl_path):
            return Path(dl_path)
        return self.plugin_dir / dl_path

    @property
    def _bash_path(self) -> Optional[str]:
        """返回可用的 bash 路径，不存在时返回 None"""
        return shutil.which("bash")

    @property
    def _cache_file(self) -> Path:
        return self.downloader_path / ".download_cache.json"

    @property
    def _cache_ttl(self) -> float:
        return float(self.cache_ttl)

    def _load_downloader_config(self) -> dict:
        """读取下载器配置文件，失败时使用默认配置生成结果"""
        config_path = self.downloader_path / "config.yaml"
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
                    if isinstance(config, dict):
                        return config
                    logger.warning("下载器配置格式异常，使用默认配置")
            except Exception as e:
                logger.warning(f"读取下载器配置失败，将使用默认配置: {e}")
        return ConfigGenerator.generate_config(self.config)

    def _resolve_save_path(self, folder: str) -> Path:
        """根据配置值解析保存目录（支持相对路径和绝对路径）"""
        if os.path.isabs(folder):
            return Path(folder)
        return self.downloader_path / folder

    def get_save_paths(self) -> Dict[str, Path]:
        """获取不同音质对应的下载目录"""
        config = self._load_downloader_config()
        return {
            "alac": self._resolve_save_path(
                str(
                    config.get(
                        "alac-save-folder",
                        ConfigGenerator.DEFAULT_CONFIG["alac-save-folder"],
                    )
                )
            ),
            "aac": self._resolve_save_path(
                str(
                    config.get(
                        "aac-save-folder",
                        ConfigGenerator.DEFAULT_CONFIG["aac-save-folder"],
                    )
                )
            ),
            "atmos": self._resolve_save_path(
                str(
                    config.get(
                        "atmos-save-folder",
                        ConfigGenerator.DEFAULT_CONFIG["atmos-save-folder"],
                    )
                )
            ),
        }

    def get_download_dirs(
        self, quality: Optional[DownloadQuality] = None
    ) -> List[Path]:
        """返回当前配置下的下载目录，按需要可指定音质"""
        save_paths = self.get_save_paths()
        if quality:
            return [save_paths.get(quality.value, list(save_paths.values())[0])]
        seen = set()
        dirs: List[Path] = []
        for path in save_paths.values():
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            dirs.append(path)
        return dirs

    def _sync_config(self):
        """同步插件配置到下载器 config.yaml"""
        config_path = self.downloader_path / "config.yaml"

        # 生成新配置
        new_config = ConfigGenerator.generate_config(self.config)

        # 如果配置文件存在，尝试保留未在插件配置中的字段
        if config_path.exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    existing_config = yaml.safe_load(f) or {}
                # 合并：新配置优先，但保留旧配置中的未知字段
                for key, value in existing_config.items():
                    if key not in new_config:
                        new_config[key] = value
            except Exception as e:
                logger.warning(f"读取现有配置失败: {e}")

        # 保存配置
        ConfigGenerator.save_config(new_config, config_path)
        logger.debug("下载器配置已同步")

    def _get_docker_env(self) -> dict:
        """获取 Docker 命令的环境变量"""
        env = os.environ.copy()
        if self.docker_host:
            env["DOCKER_HOST"] = self.docker_host
        return env

    # ===================== 缓存工具 =====================
    def _cache_key(self, url: str, quality: DownloadQuality, single_song: bool) -> str:
        return f"{url}||{quality.value}||{int(single_song)}"

    def _load_cache(self) -> dict:
        now = time.time()
        cache = {}
        if self._cache_file.exists():
            try:
                with open(self._cache_file, "r", encoding="utf-8") as f:
                    cache = json.load(f) or {}
            except Exception:
                cache = {}

        changed = False
        pruned = {}
        for key, entry in cache.items():
            ts = entry.get("ts")
            if ts is None:
                entry["ts"] = now
                changed = True
            elif now - ts > self._cache_ttl:
                changed = True
                continue  # 过期，跳过
            pruned[key] = entry

        if changed:
            self._save_cache(pruned)
        return pruned

    def _save_cache(self, cache: dict):
        try:
            with open(self._cache_file, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存下载缓存失败: {e}")

    def _get_cached_download(
        self, url: str, quality: DownloadQuality, single_song: bool
    ) -> Optional[DownloadResult]:
        cache = self._load_cache()
        key = self._cache_key(url, quality, single_song)
        entry = cache.get(key)
        if not entry:
            return None

        files = entry.get("files", [])
        cover = entry.get("cover")
        missing = [p for p in files if not os.path.exists(p)]
        if cover and not os.path.exists(cover):
            cover = None
        if missing:
            cache.pop(key, None)
            self._save_cache(cache)
            return None

        if files:
            return DownloadResult(
                success=True,
                message="使用已存在的下载文件",
                file_paths=files,
                cover_path=cover,
            )
        return None

    def _record_download_cache(
        self,
        url: str,
        quality: DownloadQuality,
        single_song: bool,
        files: List[str],
        cover: Optional[str],
    ):
        cache = self._load_cache()
        cache[self._cache_key(url, quality, single_song)] = {
            "files": files,
            "cover": cover,
            "ts": time.time(),
        }
        self._save_cache(cache)

    async def _run_command(
        self,
        cmd: List[str],
        timeout: int = 60,
        capture_output: bool = True,
        env: Optional[Dict[str, str]] = None,
        log_prefix: str = "",
    ) -> Tuple[int, str, str]:
        """执行命令并返回结果"""
        try:
            env_vars = env or self._get_docker_env()

            if self.debug_mode:
                cmd_str = " ".join(cmd)
                logger.info(f"[DEBUG]{log_prefix} 执行命令: {cmd_str}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE if capture_output else None,
                stderr=asyncio.subprocess.PIPE if capture_output else None,
                env=env_vars,
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout
            )

            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

            if self.debug_mode:
                logger.info(f"[DEBUG]{log_prefix} 返回码: {process.returncode}")
                if stdout_str:
                    stdout_lines = stdout_str.strip().split("\n")
                    if len(stdout_lines) > self.docker_log_lines:
                        logger.info(
                            f"[DEBUG]{log_prefix} stdout (显示最后 {self.docker_log_lines} 行):"
                        )
                        for line in stdout_lines[-self.docker_log_lines :]:
                            logger.info(f"  | {line}")
                    else:
                        logger.info(f"[DEBUG]{log_prefix} stdout:")
                        for line in stdout_lines:
                            logger.info(f"  | {line}")
                if stderr_str:
                    stderr_lines = stderr_str.strip().split("\n")
                    if len(stderr_lines) > self.docker_log_lines:
                        logger.info(
                            f"[DEBUG]{log_prefix} stderr (显示最后 {self.docker_log_lines} 行):"
                        )
                        for line in stderr_lines[-self.docker_log_lines :]:
                            logger.warning(f"  | {line}")
                    else:
                        logger.info(f"[DEBUG]{log_prefix} stderr:")
                        for line in stderr_lines:
                            logger.warning(f"  | {line}")

            return process.returncode, stdout_str, stderr_str

        except asyncio.TimeoutError:
            if process:
                process.kill()
            if self.debug_mode:
                logger.error(f"[DEBUG]{log_prefix} 命令超时 ({timeout}秒)")
            return -1, "", "命令执行超时"
        except Exception as e:
            if self.debug_mode:
                logger.error(f"[DEBUG]{log_prefix} 命令执行异常: {e}")
            return -1, "", str(e)

    async def check_docker_available(self) -> bool:
        """检查 Docker 是否可用"""
        code, _, _ = await self._run_command(["docker", "info"], timeout=10)
        return code == 0

    async def get_service_status(self) -> ServiceStatus:
        """获取服务状态"""
        status = ServiceStatus()

        if not await self.check_docker_available():
            status.error = "Docker 不可用"
            return status

        code, stdout, _ = await self._run_command(
            ["docker", "images", "-q", self.WRAPPER_IMAGE_NAME]
        )
        status.wrapper_image_exists = bool(stdout.strip())

        code, stdout, _ = await self._run_command(
            ["docker", "images", "-q", self.DOWNLOADER_IMAGE_NAME]
        )
        status.downloader_image_exists = bool(stdout.strip())

        code, stdout, _ = await self._run_command(
            [
                "docker",
                "ps",
                "--filter",
                f"name={self.WRAPPER_CONTAINER_NAME}",
                "--format",
                "{{.Names}}",
            ]
        )
        status.wrapper_running = self.WRAPPER_CONTAINER_NAME in stdout

        if status.wrapper_running:
            code, stdout, _ = await self._run_command(
                ["docker", "logs", "--tail", "50", self.WRAPPER_CONTAINER_NAME]
            )
            port_decrypt = str(self.decrypt_port)
            port_m3u8 = str(self.m3u8_port)
            status.decrypt_port_listening = (
                "listening" in stdout and port_decrypt in stdout
            )
            status.m3u8_port_listening = "listening" in stdout and port_m3u8 in stdout

        return status

    async def build_wrapper_image(self) -> Tuple[bool, str]:
        """构建 Wrapper 镜像"""
        wrapper_dir = self.downloader_path / "wrapper"
        if not wrapper_dir.exists():
            return False, f"Wrapper 目录不存在: {wrapper_dir}"

        logger.info("开始构建 Wrapper 镜像...")
        code, stdout, stderr = await self._run_command(
            ["docker", "build", "--tag", self.WRAPPER_IMAGE_NAME, str(wrapper_dir)],
            timeout=600,
        )

        if code == 0:
            return True, "Wrapper 镜像构建成功"
        return False, f"构建失败: {stderr}"

    async def build_downloader_image(self) -> Tuple[bool, str]:
        """构建下载器镜像"""
        if not self.downloader_path.exists():
            return False, f"下载器目录不存在: {self.downloader_path}"

        dockerfile = self.downloader_path / "Dockerfile.downloader"
        if not dockerfile.exists():
            return False, f"Dockerfile 不存在: {dockerfile}"

        logger.info("开始构建下载器镜像（首次可能需要几分钟）...")
        code, stdout, stderr = await self._run_command(
            [
                "docker",
                "build",
                "-f",
                str(dockerfile),
                "-t",
                self.DOWNLOADER_IMAGE_NAME,
                str(self.downloader_path),
            ],
            timeout=900,
        )

        if code == 0:
            return True, "下载器镜像构建成功"
        return False, f"构建失败: {stderr}"

    async def start_wrapper(self) -> Tuple[bool, str]:
        """启动 Wrapper 服务"""
        status = await self.get_service_status()

        if status.wrapper_running:
            return True, "Wrapper 服务已在运行"

        if not status.wrapper_image_exists:
            success, msg = await self.build_wrapper_image()
            if not success:
                return False, msg

        await self._run_command(["docker", "rm", "-f", self.WRAPPER_CONTAINER_NAME])

        wrapper_dir = self.downloader_path / "wrapper"
        rootfs_data = wrapper_dir / "rootfs" / "data"

        cmd = [
            "docker",
            "run",
            "-d",
            "--name",
            self.WRAPPER_CONTAINER_NAME,
            "-v",
            f"{rootfs_data}:/app/rootfs/data",
            "-p",
            f"{self.decrypt_port}:10020",
            "-p",
            f"{self.m3u8_port}:20020",
            "-e",
            "args=-H 0.0.0.0",
            self.WRAPPER_IMAGE_NAME,
        ]

        code, stdout, stderr = await self._run_command(cmd, timeout=30)

        if code != 0:
            return False, f"启动失败: {stderr}"

        await asyncio.sleep(3)

        status = await self.get_service_status()
        if status.wrapper_running:
            return True, "Wrapper 服务启动成功"
        return False, "Wrapper 启动后未能正常运行"

    async def stop_wrapper(self) -> Tuple[bool, str]:
        """停止 Wrapper 服务"""
        code, _, stderr = await self._run_command(
            ["docker", "stop", self.WRAPPER_CONTAINER_NAME]
        )
        if code == 0:
            return True, "Wrapper 服务已停止"
        return False, f"停止失败: {stderr}"

    async def ensure_services_ready(self) -> Tuple[bool, str]:
        """确保所有服务就绪"""
        status = await self.get_service_status()

        if status.error:
            return False, status.error

        if not status.downloader_image_exists:
            success, msg = await self.build_downloader_image()
            if not success:
                return False, msg

        if not status.wrapper_running:
            if self.config.get("auto_start_wrapper", True):
                success, msg = await self.start_wrapper()
                if not success:
                    return False, msg
            else:
                return False, "Wrapper 服务未运行，请手动启动或启用自动启动"

        return True, "服务就绪"

    async def download(
        self,
        url: str,
        quality: DownloadQuality = DownloadQuality.ALAC,
        single_song: bool = False,
    ) -> DownloadResult:
        """执行下载任务（通过 start.sh 调用容器）"""
        config_file = self.downloader_path / "config.yaml"
        if not config_file.exists():
            return DownloadResult(
                success=False, message="配置文件不存在", error=f"找不到 {config_file}"
            )

        # Windows 下依赖 Git Bash 运行脚本
        if platform.system().lower() == "windows" and not self._bash_path:
            return DownloadResult(
                success=False,
                message="下载失败",
                error="未检测到 bash。Windows 环境请安装 Git Bash 并将 bash 添加到 PATH，或在 WSL/Ubuntu 环境下运行。",
            )

        save_paths = self.get_save_paths()
        downloads_dir = save_paths.get(quality.value, list(save_paths.values())[0])
        downloads_dir.mkdir(parents=True, exist_ok=True)

        cached = self._get_cached_download(url, quality, single_song)
        if cached:
            if self.debug_mode:
                logger.info(f"[DEBUG] 使用缓存文件: {cached.file_paths}")
            return cached

        cmd = [
            self._bash_path or "bash",
            str(self.downloader_path / "start.sh"),
            "--non-interactive",
            "--use-saved",
            "download",
        ]

        if quality == DownloadQuality.ATMOS:
            cmd.append("--atmos")
        elif quality == DownloadQuality.AAC:
            cmd.append("--aac")

        if single_song:
            cmd.append("--song")

        cmd.append(url)

        timeout = self.config.get("download_timeout", 600)
        logger.info(f"开始下载: {url}")

        env_vars = self._get_docker_env()
        env_vars["DECRYPT_PORT"] = str(self.decrypt_port)
        env_vars["M3U8_PORT"] = str(self.m3u8_port)
        env_vars["AMDL_NON_INTERACTIVE"] = "1"
        env_vars["AMDL_USE_SAVED"] = "1"
        env_vars["WRAPPER_HOST"] = self.WRAPPER_CONTAINER_NAME

        if self.debug_mode:
            logger.info(f"[DEBUG] 下载参数:")
            logger.info(f"  URL: {url}")
            logger.info(f"  音质: {quality.value}")
            logger.info(f"  单曲模式: {single_song}")
            logger.info(f"  超时: {timeout}秒")
            logger.info(f"  解密端口: {self.decrypt_port}")
            logger.info(f"  M3U8端口: {self.m3u8_port}")
            logger.info(f"  下载目录: {downloads_dir}")

        if self.debug_mode:
            code, stdout, stderr = await self._run_command_with_realtime_log(
                cmd, timeout=timeout, env=env_vars
            )
        else:
            code, stdout, stderr = await self._run_command(
                cmd, timeout=timeout, env=env_vars, log_prefix=" [下载]"
            )

        if code != 0:
            error_msg = stderr or stdout or "下载失败"
            if (
                "缺少已保存的 Apple Music 凭证" in error_msg
                or "未找到凭证" in error_msg
            ):
                error_msg = "未检测到已登录凭证，请先在服务器上运行 ./apple-music-downloader/start.sh start 交互式登录一次。"
            elif "Unavailable" in error_msg or "Unavailable" in stdout:
                error_msg = "该曲目不可用（可能需要订阅或地区限制）"
            elif "Separator is not found" in error_msg or "chunk exceed" in error_msg:
                error_msg = "音频流解析错误，可能是 Wrapper 服务异常，请尝试重启服务 (/am stop 然后 /am start)"
            elif (
                "connection refused" in error_msg.lower()
                or "connect:" in error_msg.lower()
            ):
                error_msg = "无法连接到 Wrapper 服务，请检查服务是否运行 (/am status)"

            if self.debug_mode:
                logger.error(f"[DEBUG] 下载失败，返回码: {code}")
                logger.error(f"[DEBUG] 原始错误: {stderr or stdout}")
                logger.error(f"[DEBUG] 处理后错误: {error_msg}")
                await self._log_wrapper_status()

            return DownloadResult(success=False, message="下载失败", error=error_msg)

        downloaded_files = self._find_recent_files(downloads_dir)
        cover_path = self._find_cover([downloads_dir])

        if self.debug_mode:
            logger.info(f"[DEBUG] 找到的文件: {downloaded_files}")
            logger.info(f"[DEBUG] 封面文件: {cover_path}")

        if not downloaded_files:
            if self.debug_mode:
                logger.warning(f"[DEBUG] 未找到下载文件，检查目录内容:")
                await self._log_directory_contents(downloads_dir)

            return DownloadResult(
                success=False,
                message="下载完成但未找到文件",
                error="文件可能已存在或下载路径配置错误",
            )

        self._record_download_cache(
            url, quality, single_song, downloaded_files, cover_path
        )

        return DownloadResult(
            success=True,
            message=f"下载成功，共 {len(downloaded_files)} 个文件",
            file_paths=downloaded_files,
            cover_path=cover_path,
        )

    async def _run_command_with_realtime_log(
        self, cmd: List[str], timeout: int = 60, env: Optional[Dict[str, str]] = None
    ) -> Tuple[int, str, str]:
        """执行命令并实时输出日志"""
        try:
            env_vars = env or self._get_docker_env()
            cmd_str = " ".join(cmd)
            logger.info(f"[DEBUG][实时] 执行命令: {cmd_str}")

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_vars,
                limit=1024 * 1024,
            )

            stdout_lines = []
            stderr_lines = []

            async def read_stream(stream, lines_list, prefix):
                buffer = ""
                while True:
                    try:
                        chunk = await stream.read(4096)
                        if not chunk:
                            if buffer:
                                lines_list.append(buffer)
                                logger.info(f"[DEBUG]{prefix} {buffer}")
                            break

                        decoded = chunk.decode("utf-8", errors="replace")
                        buffer += decoded

                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.rstrip("\r")
                            if line:
                                lines_list.append(line)
                                logger.info(f"[DEBUG]{prefix} {line}")
                    except Exception as e:
                        logger.warning(f"[DEBUG]{prefix} 读取流错误: {e}")
                        break

            try:
                await asyncio.wait_for(
                    asyncio.gather(
                        read_stream(process.stdout, stdout_lines, "[stdout]"),
                        read_stream(process.stderr, stderr_lines, "[stderr]"),
                    ),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                process.kill()
                logger.error(f"[DEBUG][实时] 命令超时 ({timeout}秒)，已终止进程")
                return -1, "\n".join(stdout_lines), "命令执行超时"

            await process.wait()

            logger.info(f"[DEBUG][实时] 命令完成，返回码: {process.returncode}")

            return process.returncode, "\n".join(stdout_lines), "\n".join(stderr_lines)

        except Exception as e:
            logger.error(f"[DEBUG][实时] 命令执行异常: {e}")
            return -1, "", str(e)

    async def _log_wrapper_status(self):
        """输出 Wrapper 容器的状态和日志"""
        logger.info(f"[DEBUG] ====== Wrapper 容器状态 ======")

        code, stdout, _ = await self._run_command(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                f"name={self.WRAPPER_CONTAINER_NAME}",
                "--format",
                "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
            ],
            log_prefix=" [Wrapper状态]",
        )
        if stdout:
            logger.info(f"[DEBUG] Wrapper 容器状态:\n{stdout}")

        code, stdout, _ = await self._run_command(
            [
                "docker",
                "logs",
                "--tail",
                str(self.docker_log_lines),
                self.WRAPPER_CONTAINER_NAME,
            ],
            log_prefix=" [Wrapper日志]",
        )
        if stdout:
            logger.info(
                f"[DEBUG] Wrapper 最近 {self.docker_log_lines} 行日志:\n{stdout}"
            )

    async def _log_directory_contents(self, directory: Path):
        """输出目录内容"""
        logger.info(f"[DEBUG] 目录内容: {directory}")
        try:
            for root, dirs, files in os.walk(directory):
                level = root.replace(str(directory), "").count(os.sep)
                indent = "  " * level
                logger.info(f"  {indent}{os.path.basename(root)}/")
                sub_indent = "  " * (level + 1)
                for file in files:
                    file_path = os.path.join(root, file)
                    try:
                        size = os.path.getsize(file_path)
                        mtime = os.path.getmtime(file_path)
                        age = time.time() - mtime
                        logger.info(
                            f"  {sub_indent}{file} ({size} bytes, {age:.0f}s ago)"
                        )
                    except:
                        logger.info(f"  {sub_indent}{file}")
        except Exception as e:
            logger.error(f"[DEBUG] 读取目录失败: {e}")

    def _find_recent_files(
        self,
        directory: Path,
        extensions: tuple = (".m4a", ".flac", ".mp3", ".opus", ".wav"),
        max_age_seconds: int = 300,
    ) -> List[str]:
        """查找最近下载的文件"""
        current_time = time.time()
        recent_files = []

        for root, _, files in os.walk(directory):
            for file in files:
                if file.lower().endswith(extensions):
                    file_path = os.path.join(root, file)
                    try:
                        mtime = os.path.getmtime(file_path)
                        if current_time - mtime < max_age_seconds:
                            recent_files.append(file_path)
                    except OSError:
                        continue

        return sorted(recent_files, key=os.path.getmtime, reverse=True)

    def _find_cover(self, directories: List[Path]) -> Optional[str]:
        """在指定的下载目录中查找最近的封面文件"""
        current_time = time.time()

        for directory in directories:
            if not directory.exists():
                continue
            for root, _, files in os.walk(directory):
                for file in files:
                    if file.lower() in ("cover.jpg", "cover.png", "folder.jpg"):
                        file_path = os.path.join(root, file)
                        try:
                            mtime = os.path.getmtime(file_path)
                            if current_time - mtime < 300:
                                return file_path
                        except OSError:
                            continue
        return None

    async def force_clean(self, directory: Path) -> Tuple[bool, str, int]:
        """强制清理下载目录"""
        if not await self.check_docker_available():
            return False, "Docker 不可用", 0

        directory = directory.resolve()
        if not directory.exists():
            return True, "目录不存在", 0

        try:
            items = [x for x in directory.iterdir() if x.name != ".gitkeep"]
            count = len(items)
            if count == 0:
                return True, "目录为空", 0
        except Exception:
            count = 0

        mount_path = "/clean_target"

        cmd = [
            "docker",
            "run",
            "--rm",
            "-v",
            f"{directory}:{mount_path}",
            "alpine",
            "sh",
            "-c",
            f"rm -rf {mount_path}/*",
        ]

        code, stdout, stderr = await self._run_command(cmd, timeout=30)

        if code == 0:
            return True, "清理成功", count
        else:
            return False, f"清理失败: {stderr or stdout}", 0


class URLParser:
    """Apple Music URL 解析器"""

    PATTERNS = {
        "album": re.compile(
            r"^https://(?:beta\.)?music\.apple\.com/(\w{2})/album/[^/]+/(\d+)(?:\?.*)?$"
        ),
        "song": re.compile(
            r"^https://(?:beta\.)?music\.apple\.com/(\w{2})/album/[^/]+/(\d+)\?i=(\d+)(?:[&#].*)?$"
        ),
        "song_direct": re.compile(
            r"^https://(?:beta\.)?music\.apple\.com/(\w{2})/song/[^/]+/(\d+)(?:[?#].*)?$"
        ),
        "playlist": re.compile(
            r"^https://(?:beta\.)?music\.apple\.com/(\w{2})/playlist/[^/]+/(pl\.[\w-]+)(?:\?.*)?$"
        ),
        "artist": re.compile(
            r"^https://(?:beta\.)?music\.apple\.com/(\w{2})/artist/[^/]+/(\d+)(?:\?.*)?$"
        ),
        "music_video": re.compile(
            r"^https://(?:beta\.)?music\.apple\.com/(\w{2})/music-video/[^/]+/(\d+)(?:\?.*)?$"
        ),
    }

    @classmethod
    def parse(cls, url: str) -> Optional[Dict[str, str]]:
        """解析 URL 并返回类型和 ID"""
        url = url.strip()

        song_match = cls.PATTERNS["song"].match(url)
        if song_match:
            return {
                "type": "song",
                "storefront": song_match.group(1),
                "album_id": song_match.group(2),
                "song_id": song_match.group(3),
            }

        song_direct_match = cls.PATTERNS["song_direct"].match(url)
        if song_direct_match:
            return {
                "type": "song",
                "storefront": song_direct_match.group(1),
                "song_id": song_direct_match.group(2),
            }

        for url_type, pattern in cls.PATTERNS.items():
            if url_type in ("song", "song_direct"):
                continue
            match = pattern.match(url)
            if match:
                result = {
                    "type": url_type,
                    "storefront": match.group(1),
                    "id": match.group(2),
                }
                return result

        return None

    @classmethod
    def is_valid_url(cls, url: str) -> bool:
        """检查是否是有效的 Apple Music URL"""
        return cls.parse(url) is not None

    @classmethod
    def get_type_display(cls, url_type: str) -> str:
        """获取类型的中文显示名"""
        type_names = {
            "album": "专辑",
            "song": "单曲",
            "playlist": "播放列表",
            "artist": "艺术家",
            "music_video": "MV",
        }
        return type_names.get(url_type, url_type)


class MetadataFetcher:
    """元数据获取器"""

    @staticmethod
    async def get_song_info(song_id: str, storefront: str = "us") -> Optional[str]:
        """获取歌曲信息 (标题 - 艺术家)"""
        import aiohttp

        # iTunes API URL
        url = f"https://itunes.apple.com/lookup?id={song_id}&country={storefront}&lang=zh_cn"

        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
                }
                async with session.get(url, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        # 允许非 application/json 类型的响应被解析（iTunes API 有时返回 text/javascript）
                        data = await response.json(content_type=None)

                        if data.get("resultCount", 0) > 0:
                            track = data["results"][0]
                            name = track.get("trackName", "Unknown")
                            artist = track.get("artistName", "Unknown")
                            return f"{name} - {artist}"

        except Exception as e:
            logger.warning(f"获取歌曲信息失败: {e}")

        return None
