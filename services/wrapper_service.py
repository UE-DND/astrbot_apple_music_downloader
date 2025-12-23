"""
Wrapper 服务管理。
负责连接 wrapper-manager，支持原生与远程模式。
"""

import asyncio
import subprocess
import shutil
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, Union

from .logger import LoggerInterface, get_logger

# 尝试相对导入,失败则使用绝对导入(支持独立运行)
try:
    from ..core.grpc import WrapperManager, WrapperManagerException
    from ..core.config import PluginConfig
except ImportError:
    from core.grpc import WrapperManager, WrapperManagerException
    from core.config import PluginConfig

class WrapperMode(Enum):
    """连接模式（Wrapper）。"""
    NATIVE = "native"     # 原生 Python wrapper-manager（推荐）
    REMOTE = "remote"     # 远程 wrapper-manager 服务


@dataclass
class WrapperStatus:
    """服务状态（Wrapper）。"""
    mode: WrapperMode
    connected: bool = False
    url: str = ""
    regions: list = None
    error: Optional[str] = None
    ready: bool = False
    client_count: int = 0

    def __post_init__(self):
        if self.regions is None:
            self.regions = []


class WrapperService:
    """服务管理器（Wrapper）。"""

    def __init__(
        self,
        config: Union[PluginConfig, str],
        url: str = "127.0.0.1:18923",
        secure: bool = False,
        plugin_dir: Optional[Path] = None,
        logger: Optional[LoggerInterface] = None
    ):
        """初始化 Wrapper 服务。"""
        # 兼容 PluginConfig 与旧参数形式
        if isinstance(config, PluginConfig):
            # 从 PluginConfig 提取配置
            self.mode = WrapperMode(config.wrapper.mode)
            self.url = config.wrapper.url
            self.secure = config.wrapper.secure
            self.plugin_dir = config.plugin_dir or Path(".")
            self._debug_mode = config.debug_mode
        else:
            # 旧版参数形式
            self.mode = WrapperMode(config)
            self.url = url
            self.secure = secure
            self.plugin_dir = plugin_dir or Path(".")
            self._debug_mode = False

        # 日志注入
        self.logger = logger or get_logger()

        self._manager: Optional[WrapperManager] = None
        self._connected = False

        # 原生 Python wrapper-manager 服务端
        self._native_server = None

        # Docker wrapper 容器信息
        self._docker_container_name = "am-wrapper"
        self._docker_image_name = "am-wrapper"

    async def _ensure_docker_wrapper(self) -> Tuple[bool, str]:
        """确保 Docker wrapper 容器运行。"""
        # 检查Docker是否可用
        if not shutil.which("docker"):
            return False, "Docker未安装或不在PATH中"

        try:
            # 检查容器是否已在运行
            result = subprocess.run(
                ["docker", "ps", "-q", "-f", f"name={self._docker_container_name}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.stdout.strip():
                self.logger.info(f"Docker wrapper容器已在运行")
                return True, "容器已运行"

            # 检查是否有停止的容器
            result = subprocess.run(
                ["docker", "ps", "-aq", "-f", f"name={self._docker_container_name}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.stdout.strip():
                # 启动已存在的容器
                self.logger.info(f"启动已存在的Docker wrapper容器...")
                result = subprocess.run(
                    ["docker", "start", self._docker_container_name],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0:
                    await asyncio.sleep(2)  # 等待容器就绪
                    return True, "容器已启动"
                return False, f"启动容器失败: {result.stderr}"

            image_ok, image_msg = await self._ensure_docker_image()
            if not image_ok:
                return False, image_msg

            # 创建并启动容器
            self.logger.info(f"创建并启动Docker wrapper容器...")
            rootfs_dir = self.plugin_dir / "bin" / "rootfs"
            base_dir = "/data/data/com.apple.android.music/files"
            result = subprocess.run(
                [
                    "docker", "run", "-d",
                    "--name", self._docker_container_name,
                    "-p", "10020:10020",
                    "-p", "20020:20020",
                    "-p", "30020:30020",
                    "-v", f"{rootfs_dir / 'data'}:/app/rootfs/data",
                    "-v", f"{rootfs_dir / 'data'}:/data",
                    "-e", f"args=-H 0.0.0.0 -B {base_dir}",
                    self._docker_image_name
                ],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode != 0:
                return False, f"创建容器失败: {result.stderr}"

            # 等待容器就绪
            await asyncio.sleep(3)
            self.logger.info(f"Docker wrapper容器已启动")
            return True, "容器已创建并启动"

        except subprocess.TimeoutExpired:
            return False, "Docker操作超时"
        except Exception as e:
            return False, f"Docker操作失败: {str(e)}"

    async def _ensure_docker_image(self) -> Tuple[bool, str]:
        """确保 Docker wrapper 镜像存在。"""
        if not shutil.which("docker"):
            return False, "Docker未安装或不在PATH中"

        try:
            result = subprocess.run(
                ["docker", "images", "-q", self._docker_image_name],
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.stdout.strip():
                return True, "镜像已存在"

            self.logger.info("构建Docker wrapper镜像...")
            bin_dir = self.plugin_dir / "bin"
            dockerfile_path = bin_dir / "Dockerfile"
            rootfs_path = bin_dir / "rootfs"
            wrapper_path = bin_dir / "wrapper"
            if not dockerfile_path.exists():
                return False, f"Dockerfile不存在: {dockerfile_path}"
            if not rootfs_path.exists():
                return False, f"rootfs不存在: {rootfs_path}"
            if not wrapper_path.exists():
                return False, f"wrapper不存在: {wrapper_path}"

            build_cmd = [
                "docker", "build",
                "--progress=plain",
                "-t", self._docker_image_name,
                "-f", str(dockerfile_path),
                str(bin_dir)
            ]
            process = subprocess.Popen(
                build_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True
            )

            output_lines = []
            if process.stdout:
                for line in process.stdout:
                    line = line.rstrip()
                    if line:
                        output_lines.append(line)
                        if len(output_lines) > 200:
                            output_lines = output_lines[-200:]
                        self.logger.info(line)

            returncode = process.wait(timeout=120)
            if returncode != 0:
                tail = "\n".join(output_lines[-50:]) if output_lines else "构建失败，请查看日志"
                return False, f"构建镜像失败: {tail}"

            self.logger.info("Docker wrapper镜像构建完成")
            return True, "镜像已构建"
        except subprocess.TimeoutExpired:
            return False, "Docker操作超时"
        except Exception as e:
            return False, f"Docker操作失败: {str(e)}"

    async def _stop_docker_wrapper(self):
        """停止Docker wrapper容器。"""
        try:
            subprocess.run(
                ["docker", "stop", self._docker_container_name],
                capture_output=True,
                timeout=30
            )
            self.logger.info(f"Docker wrapper容器已停止")
        except Exception as e:
            self.logger.warning(f"停止Docker容器失败: {e}")

    async def login_docker_wrapper(self, username: str, password: str) -> Tuple[bool, str]:
        """在 Docker wrapper 中执行登录。"""
        if not shutil.which("docker"):
            return False, "Docker未安装或不在PATH中"

        try:
            image_ok, image_msg = await self._ensure_docker_image()
            if not image_ok:
                return False, image_msg

            # 先停止现有容器（如果有）
            subprocess.run(
                ["docker", "rm", "-f", self._docker_container_name],
                capture_output=True,
                timeout=10
            )

            # 执行登录
            self.logger.info(f"正在登录Docker wrapper...")
            rootfs_dir = self.plugin_dir / "bin" / "rootfs"
            files_dir = rootfs_dir / "data" / "data" / "com.apple.android.music" / "files"
            files_dir.mkdir(parents=True, exist_ok=True)
            base_dir = "/data/data/com.apple.android.music/files"

            code_file = files_dir / "code"

            result = subprocess.run(
                [
                    "docker", "run", "--rm",
                    "-v", f"{rootfs_dir / 'data'}:/app/rootfs/data",
                    "-v", f"{rootfs_dir / 'data'}:/data",
                    "-e", f"args=-L {username}:{password} -F -H 0.0.0.0 -B {base_dir}",
                    self._docker_image_name
                ],
                capture_output=True,
                text=True,
                timeout=120
            )

            combined_output = "\n".join(
                [text for text in (result.stdout, result.stderr) if text]
            )
            output_lower = combined_output.lower()

            # 检查是否需要2FA（部分 wrapper 会输出到 stdout）
            if "2fa" in output_lower or "verification" in output_lower or "验证码" in combined_output or "双因素" in combined_output:
                return False, f"需要2FA验证码，请将验证码写入: {code_file}"

            if result.returncode != 0:
                error_msg = result.stderr or result.stdout or "未知错误"
                return False, f"登录失败: {error_msg}"

            if not await self.check_docker_wrapper_logged_in():
                if combined_output:
                    tail = combined_output.strip().splitlines()[-10:]
                    return False, "登录完成但未生成凭据，请检查 rootfs/data 挂载与权限\n" + "\n".join(tail)
                return False, "登录完成但未生成凭据，请检查 rootfs/data 挂载与权限"

            self.logger.info("Docker wrapper登录成功")
            return True, "登录成功"

        except subprocess.TimeoutExpired:
            return False, "登录超时"
        except Exception as e:
            return False, f"登录失败: {str(e)}"

    async def check_docker_wrapper_logged_in(self) -> bool:
        """检查Docker wrapper是否已登录。"""
        # 检查凭据文件是否存在
        creds_dir = self.plugin_dir / "bin" / "rootfs" / "data" / "data" / "com.apple.android.music"
        files_dir = creds_dir / "files"
        adi_file = creds_dir / "adi.pb"
        mpl_db = files_dir / "mpl_db"
        if adi_file.exists():
            return True
        if mpl_db.exists():
            return True
        if files_dir.exists():
            return any(files_dir.iterdir())
        return False

    @property
    def is_connected(self) -> bool:
        """检查是否已连接 wrapper 服务。"""
        return self._connected and self._manager is not None

    @property
    def manager(self) -> Optional[WrapperManager]:
        """获取 WrapperManager 实例。"""
        return self._manager

    async def init(self) -> Tuple[bool, str]:
        """初始化并连接 wrapper 服务。"""
        # 已初始化则直接返回
        if self._connected and self._manager:
            return True, "服务已初始化"

        try:
            match self.mode:
                case WrapperMode.NATIVE:
                    return await self._init_native()
                case WrapperMode.REMOTE:
                    return await self._init_remote()
        except Exception as e:
            self.logger.error(f"Failed to initialize wrapper service: {e}")
            return False, f"初始化失败: {str(e)}"

    async def _init_native(self) -> Tuple[bool, str]:
        """初始化原生 wrapper-manager 模式。"""
        try:
            self.logger.info("启动原生 Python wrapper-manager 服务...")

            # 导入原生 wrapper-manager
            from .manager import (
                NativeWrapperManagerServer,
                WrapperProxyConfig
            )

            # 创建 wrapper 代理配置
            proxy_config = WrapperProxyConfig(
                host="127.0.0.1",
                decrypt_port=10020,
                m3u8_port=20020,
                account_port=30020,
                timeout=30
            )

            # 创建并启动原生服务
            # 从 URL 提取端口（格式：host:port）
            grpc_port = 18923
            if ":" in self.url:
                try:
                    grpc_port = int(self.url.split(":")[-1])
                except ValueError:
                    self.logger.warning(f"无法解析端口号，使用默认值 18923")

            self._native_server = NativeWrapperManagerServer(
                host="127.0.0.1",
                port=grpc_port,
                proxy_config=proxy_config
            )

            await self._native_server.start()
            self.logger.info(f"原生 wrapper-manager 服务已启动 (端口 {grpc_port})")

            # 等待服务就绪
            await asyncio.sleep(0.5)

            # 连接服务
            success, msg = await self._connect_to_manager()
            if not success:
                return False, msg

            # 检查Docker wrapper是否已登录
            if not await self.check_docker_wrapper_logged_in():
                self.logger.info("Docker wrapper未登录，跳过自动启动")
                self.logger.info("请使用 'python -m core login' 命令登录后再下载")
                return True, "服务初始化成功，需要登录Docker wrapper才能下载"

            # 启动Docker wrapper容器
            docker_success, docker_msg = await self._ensure_docker_wrapper()
            if not docker_success:
                self.logger.warning(f"Docker wrapper启动失败: {docker_msg}")
                # 继续运行，用户可以通过登录添加实例
                return True, f"服务初始化成功，但Docker wrapper未启动: {docker_msg}"

            # 等待容器完全启动
            await asyncio.sleep(2)

            # 检查容器是否仍在运行
            result = subprocess.run(
                ["docker", "ps", "-q", "-f", f"name={self._docker_container_name}"],
                capture_output=True,
                text=True,
                timeout=10
            )
            if not result.stdout.strip():
                self.logger.warning("Docker wrapper容器启动后立即退出，可能需要重新登录")
                return True, "服务初始化成功，但Docker wrapper未能保持运行"

            # 添加默认 wrapper 实例
            try:
                from .manager import WrapperInstance, InstanceStatus
                from .manager.wrapper_proxy import WrapperProxy

                instance_id = "default"

                # 创建 wrapper 代理
                proxy = WrapperProxy(
                    instance_id=instance_id,
                    username="docker-wrapper",
                    region="cn",
                    config=proxy_config
                )

                # 启动代理（创建 HTTP 会话）
                await proxy.start()

                # 创建 wrapper 实例
                instance = WrapperInstance(
                    instance_id=instance_id,
                    username="docker-wrapper",
                    region="cn",
                    status=InstanceStatus.ACTIVE,
                    proxy=proxy
                )

                # 直接写入实例管理器
                self._native_server.instance_manager._instances[instance_id] = instance
                self._native_server.instance_manager._username_to_id["docker-wrapper"] = instance_id

                self.logger.info(f"已自动添加 wrapper 实例: {instance_id} (active: {proxy.is_active})")
            except Exception as e:
                self.logger.warning(f"自动添加 wrapper 实例失败: {e}")

            return True, "服务初始化成功"

        except ImportError as e:
            self.logger.error(f"无法导入原生 wrapper-manager: {e}")
            return False, f"原生模式不可用: {str(e)}"
        except Exception as e:
            self.logger.error(f"启动原生 wrapper-manager 失败: {e}")
            return False, f"启动失败: {str(e)}"

    async def _init_remote(self) -> Tuple[bool, str]:
        """初始化远程连接模式。"""
        return await self._connect_to_manager()

    async def _connect_to_manager(self) -> Tuple[bool, str]:
        """连接 wrapper-manager 服务。"""
        try:
            self._manager = WrapperManager()
            await self._manager.init(self.url, self.secure)

            # 连接测试
            status = await self._manager.status()
            self._connected = True

            regions = status.regions if status else []
            client_count = status.client_count if status else 0
            ready = status.ready if status else False

            self.logger.info(
                f"Connected to wrapper-manager at {self.url}, "
                f"regions: {regions}, clients: {client_count}, ready: {ready}"
            )

            if not ready:
                return True, f"已连接到 Wrapper-Manager (等待就绪，当前 {client_count} 个账户)"

            return True, f"已连接到 Wrapper-Manager ({len(regions)} 个地区, {client_count} 个账户)"

        except WrapperManagerException as e:
            self._connected = False
            self.logger.error(f"Failed to connect to wrapper-manager: {e}")
            return False, f"连接失败: {str(e)}"
        except Exception as e:
            self._connected = False
            self.logger.error(f"Unexpected error connecting to wrapper-manager: {e}")
            return False, f"连接失败: {str(e)}"

    async def start(self) -> Tuple[bool, str]:
        """启动 Wrapper 服务。"""
        match self.mode:
            case WrapperMode.NATIVE:
                return True, "原生模式由 init() 自动启动"
            case WrapperMode.REMOTE:
                return True, "远程模式无需启动服务"

    async def stop(self) -> Tuple[bool, str]:
        """停止 Wrapper 服务。"""
        match self.mode:
            case WrapperMode.NATIVE:
                return await self._stop_native()
            case WrapperMode.REMOTE:
                self._connected = False
                return True, "已断开远程连接"

    async def _stop_native(self) -> Tuple[bool, str]:
        """停止原生 wrapper-manager。"""
        try:
            if self._native_server:
                self.logger.info("停止原生 wrapper-manager 服务...")
                await self._native_server.stop()
                self._native_server = None

            self._connected = False
            return True, "原生 wrapper-manager 已停止"

        except Exception as e:
            self.logger.error(f"停止原生服务失败: {e}")
            return False, f"停止失败: {str(e)}"

    async def get_status(self) -> WrapperStatus:
        """获取 Wrapper 服务状态。"""
        status = WrapperStatus(
            mode=self.mode,
            url=self.url,
            connected=self._connected
        )

        if self._connected and self._manager:
            try:
                manager_status = await self._manager.status()
                if manager_status:
                    status.regions = manager_status.regions or []
                    status.ready = manager_status.ready
                    status.client_count = manager_status.client_count
            except Exception as e:
                status.error = str(e)
                status.connected = False

        return status

    async def get_manager(self) -> Optional[WrapperManager]:
        """获取 WrapperManager 实例。"""
        if not self._connected:
            success, _ = await self._connect_to_manager()
            if not success:
                return None

        return self._manager

    async def close(self):
        """关闭 Wrapper 服务连接。"""
        # 原生服务运行中则停止
        if self.mode == WrapperMode.NATIVE and self._native_server:
            await self._native_server.stop()
            self._native_server = None

        # 关闭 gRPC 客户端
        if self._manager:
            await self._manager.close()
            self._manager = None

        self._connected = False


    async def _run_command(
        self,
        cmd: list,
        timeout: int = 60,
        env: dict = None
    ) -> Tuple[int, str, str]:
        """执行 shell 命令。"""
        try:
            import os
            env_vars = env or os.environ.copy()

            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env_vars
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            return (
                process.returncode,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace")
            )

        except asyncio.TimeoutError:
            if process:
                process.kill()
            return -1, "", "命令执行超时"
        except Exception as e:
            return -1, "", str(e)


    async def decrypt_all(
        self,
        adam_id: str,
        key: str,
        samples: list,
        progress_callback=None
    ) -> Tuple[bool, list, Optional[str]]:
        """使用单连接批量解密样本（快速模式）。"""
        if self.mode == WrapperMode.NATIVE and self._native_server:
            # 直接访问 WrapperProxy 提升速度
            try:
                # 获取默认实例代理
                instance = self._native_server.instance_manager._instances.get("default")
                if not instance or not instance.proxy:
                    return False, [], "No wrapper instance available"

                # 直接调用代理的 decrypt_all
                return await instance.proxy.decrypt_all(
                    adam_id, key, samples, progress_callback
                )
            except Exception as e:
                self.logger.error(f"Fast decrypt failed: {e}")
                return False, [], str(e)
        else:
            # 远程模式退回逐样本 gRPC 解密
            # 会变慢但保持兼容
            self.logger.warning("decrypt_all not available in remote mode, using slow path")
            return False, [], "Remote mode does not support fast decrypt"
