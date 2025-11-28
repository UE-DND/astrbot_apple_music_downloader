"""
Wrapper Service Management

Manages the connection to wrapper-manager service for decryption.
Supports three modes: Docker (wrapper-manager), QEMU, and Remote.
Features automatic download, build, and version management for Docker mode.
"""

import asyncio
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional, Tuple, Union

from astrbot.api import logger

from ..core.grpc import WrapperManager, WrapperManagerException
from ..core.config import PluginConfig
from .wrapper_manager_docker import WrapperManagerDockerManager


class WrapperMode(Enum):
    """Wrapper connection mode."""
    DOCKER = "docker"      # wrapper-manager in Docker (recommended)
    QEMU = "qemu"         # legacy QEMU mode
    REMOTE = "remote"     # remote wrapper-manager service


@dataclass
class WrapperStatus:
    """Wrapper service status."""
    mode: WrapperMode
    connected: bool = False
    url: str = ""
    regions: list = None
    error: Optional[str] = None
    ready: bool = False
    client_count: int = 0
    # Docker-specific status
    wrapper_version: Optional[str] = None
    update_available: bool = False

    def __post_init__(self):
        if self.regions is None:
            self.regions = []


class WrapperService:
    """
    Wrapper service manager.

    Provides unified interface for different wrapper connection modes:
    - Docker: Run wrapper-manager in Docker (recommended, supports multi-instance and runtime login)
    - QEMU: Run wrapper in a local QEMU instance (legacy)
    - Remote: Connect to a remote wrapper-manager service
    """

    DOCKER_CONTAINER_NAME = "apple-music-wrapper-manager"
    DOCKER_IMAGE_NAME = "apple-music-wrapper-manager"

    def __init__(
        self,
        config: Union[PluginConfig, str],
        url: str = "127.0.0.1:18923",
        secure: bool = False,
        plugin_dir: Optional[Path] = None,
        docker_config: dict = None,
        qemu_config: dict = None
    ):
        """
        Initialize the wrapper service.

        Args:
            config: Either a PluginConfig object or a mode string (docker, qemu, remote)
            url: Wrapper-manager service URL (host:port for gRPC) - only used if config is str
            secure: Use TLS for gRPC connection - only used if config is str
            plugin_dir: Plugin directory path
            docker_config: Docker-specific configuration - only used if config is str
            qemu_config: QEMU-specific configuration - only used if config is str
        """
        # Support both PluginConfig object and individual parameters
        if isinstance(config, PluginConfig):
            # Extract configuration from PluginConfig
            self.mode = WrapperMode(config.wrapper.mode)
            self.url = config.wrapper.url
            self.secure = config.wrapper.secure
            self.plugin_dir = config.plugin_dir or Path(".")
            self.docker_config = {
                "docker_host": config.docker.docker_host,
                "container_name": config.docker.container_name,
                "image_name": config.docker.image_name,
                "grpc_port": config.docker.grpc_port,
            }
            self.qemu_config = {
                "enable_hw_accel": config.qemu.enable_hw_accel,
                "hw_accelerator": config.qemu.hw_accelerator,
                "memory_size": config.qemu.memory_size,
                "cpu_model": config.qemu.cpu_model,
                "show_window": config.qemu.show_window,
            }
            self._debug_mode = config.debug_mode
        else:
            # Legacy mode: individual parameters
            self.mode = WrapperMode(config)
            self.url = url
            self.secure = secure
            self.plugin_dir = plugin_dir or Path(".")
            self.docker_config = docker_config or {}
            self.qemu_config = qemu_config or {}
            self._debug_mode = False

        self._manager: Optional[WrapperManager] = None
        self._qemu_process: Optional[asyncio.subprocess.Process] = None
        self._connected = False

        # Docker manager for auto-deployment (wrapper-manager)
        self._docker_manager: Optional[WrapperManagerDockerManager] = None
        if self.mode == WrapperMode.DOCKER:
            self._docker_manager = WrapperManagerDockerManager(
                assets_dir=self.plugin_dir / "assets",
                use_proxy=self.docker_config.get("use_proxy", True),
                proxy_url=self.docker_config.get("proxy_url"),
            )

    @property
    def is_connected(self) -> bool:
        """Check if connected to wrapper service."""
        return self._connected and self._manager is not None

    @property
    def manager(self) -> Optional[WrapperManager]:
        """Get the WrapperManager instance."""
        return self._manager

    async def init(self) -> Tuple[bool, str]:
        """
        Initialize and connect to the wrapper service.

        Returns:
            Tuple of (success, message)
        """
        try:
            match self.mode:
                case WrapperMode.DOCKER:
                    return await self._init_docker()
                case WrapperMode.QEMU:
                    return await self._init_qemu()
                case WrapperMode.REMOTE:
                    return await self._init_remote()
        except Exception as e:
            logger.error(f"Failed to initialize wrapper service: {e}")
            return False, f"初始化失败: {str(e)}"

    async def _init_docker(self) -> Tuple[bool, str]:
        """Initialize Docker mode with wrapper-manager auto-deployment."""
        # Check if Docker is available
        if not await self._check_docker_available():
            return False, "Docker 不可用"

        # Check deploy mode: auto or manual
        auto_deploy = self.docker_config.get("auto_deploy", "auto")
        debug_mode = self.docker_config.get("debug_mode", False)

        # In debug/developer mode, force manual deploy
        if debug_mode:
            auto_deploy = "manual"

        image_name = self.docker_config.get("image_name", self.DOCKER_IMAGE_NAME)
        image_exists = await self._check_image_exists(image_name)

        if auto_deploy == "manual":
            # Manual mode: only check if image exists, don't auto-build
            if not image_exists:
                logger.warning("Docker 镜像不存在，请先运行 setup.sh 初始化")
                return False, "Docker 镜像不存在，请先运行 setup.sh 初始化"

            # Check if container is running
            if not await self._is_docker_container_running():
                logger.info("启动 wrapper-manager 容器...")
                success, msg = await self.start()
                if not success:
                    return False, msg

            # Connect to the service
            return await self._connect_to_manager()

        # Auto mode: auto-setup if needed
        if self._docker_manager:
            auto_update = self.docker_config.get("auto_update", True)

            if auto_update:
                logger.info("检查 wrapper-manager 更新...")
                update_available, latest_commit = await self._docker_manager.check_for_updates()

                if update_available and latest_commit:
                    logger.info(f"发现新版本: {latest_commit}")

            # Auto-setup if source not cloned or image doesn't exist
            if not self._docker_manager.is_source_cloned() or not image_exists:
                logger.info("开始自动部署 wrapper-manager...")
                success, msg = await self._docker_manager.auto_setup(image_name)
                if not success:
                    return False, f"自动部署失败: {msg}"
                logger.info(f"自动部署完成: {msg}")

        # Check if container is running
        if not await self._is_docker_container_running():
            success, msg = await self.start()
            if not success:
                return False, msg

        # Connect to the service
        return await self._connect_to_manager()

    async def _init_qemu(self) -> Tuple[bool, str]:
        """Initialize QEMU local instance mode."""
        # Check dependencies
        if not self._check_qemu_available():
            return False, "QEMU 不可用，请安装 qemu-system-x86_64"

        # Start QEMU instance if not running
        if not self._qemu_process:
            success, msg = await self.start()
            if not success:
                return False, msg

        # Connect to the service
        return await self._connect_to_manager()

    async def _init_remote(self) -> Tuple[bool, str]:
        """Initialize remote connection mode."""
        return await self._connect_to_manager()

    async def _connect_to_manager(self) -> Tuple[bool, str]:
        """Connect to the wrapper manager service."""
        try:
            self._manager = WrapperManager()
            await self._manager.init(self.url, self.secure)

            # Test connection
            status = await self._manager.status()
            self._connected = True

            regions = status.regions if status else []
            client_count = status.client_count if status else 0
            ready = status.ready if status else False

            logger.info(
                f"Connected to wrapper-manager at {self.url}, "
                f"regions: {regions}, clients: {client_count}, ready: {ready}"
            )

            if not ready:
                return True, f"已连接到 Wrapper-Manager (等待就绪，当前 {client_count} 个账户)"

            return True, f"已连接到 Wrapper-Manager ({len(regions)} 个地区, {client_count} 个账户)"

        except WrapperManagerException as e:
            self._connected = False
            logger.error(f"Failed to connect to wrapper-manager: {e}")
            return False, f"连接失败: {str(e)}"
        except Exception as e:
            self._connected = False
            logger.error(f"Unexpected error connecting to wrapper-manager: {e}")
            return False, f"连接失败: {str(e)}"

    async def start(self) -> Tuple[bool, str]:
        """
        Start the wrapper service.

        Returns:
            Tuple of (success, message)
        """
        match self.mode:
            case WrapperMode.DOCKER:
                return await self._start_docker()
            case WrapperMode.QEMU:
                return await self._start_qemu()
            case WrapperMode.REMOTE:
                return True, "远程模式无需启动服务"

    async def _start_docker(self) -> Tuple[bool, str]:
        """Start Docker container with wrapper-manager."""
        try:
            container_name = self.docker_config.get("container_name", self.DOCKER_CONTAINER_NAME)
            image_name = self.docker_config.get("image_name", self.DOCKER_IMAGE_NAME)
            grpc_port = self.docker_config.get("grpc_port", 18923)

            # Stop and remove existing container first (more aggressive cleanup)
            logger.info(f"清理已存在的容器 {container_name}...")
            await self._run_command(["docker", "stop", container_name], timeout=10)
            await self._run_command(["docker", "rm", "-f", container_name], timeout=10)

            # Check if port is in use by another process
            port_check = await self._run_command(
                ["docker", "ps", "--filter", f"publish={grpc_port}", "--format", "{{.Names}}"],
                timeout=5
            )
            if port_check[0] == 0 and port_check[1].strip():
                other_container = port_check[1].strip()
                return False, f"端口 {grpc_port} 已被其他容器占用: {other_container}"

            # Check if image exists, build if not
            if not await self._check_image_exists(image_name):
                if self._docker_manager:
                    success, msg = await self._docker_manager.auto_setup(image_name)
                    if not success:
                        return False, f"镜像构建失败: {msg}"
                else:
                    return False, f"Docker 镜像不存在: {image_name}"

            use_mirror = self.docker_config.get("use_proxy", True)

            # Prepare volume mounts for persistent data - MUST use absolute path
            manager_dir = (self.plugin_dir / "assets" / "wrapper-manager").resolve()
            volumes = []
            if manager_dir.exists():
                data_dir = manager_dir / "data"
                data_dir.mkdir(parents=True, exist_ok=True)
                # Convert to absolute path string for Docker
                volumes.extend(["-v", f"{data_dir.resolve()}:/root/data"])

            # Build command args
            cmd_args = ["--host", "0.0.0.0", "--port", "8080"]
            if use_mirror:
                cmd_args.extend(["--mirror", "true"])

            # Start container with port mapping (host_port:container_port)
            cmd = [
                "docker", "run", "-d",
                "--name", container_name,
                "-p", f"{grpc_port}:8080",
                *volumes,
                image_name,
                *cmd_args
            ]

            code, stdout, stderr = await self._run_command(cmd, timeout=30)

            if code != 0:
                return False, f"启动 Docker 容器失败: {stderr}"

            # Wait for service to be ready
            logger.info("等待 wrapper-manager 初始化...")
            await asyncio.sleep(5)

            # Verify container is running
            if await self._is_docker_container_running():
                return True, "Docker 容器启动成功"

            return False, "Docker 容器启动后未能正常运行"

        except Exception as e:
            logger.error(f"Failed to start Docker container: {e}")
            return False, f"启动失败: {str(e)}"

    async def _start_qemu(self) -> Tuple[bool, str]:
        """Start QEMU local instance."""
        try:
            # Get QEMU configuration
            enable_hw_accel = self.qemu_config.get("enable_hw_accel", False)
            hw_accelerator = self.qemu_config.get("hw_accelerator", "")
            memory_size = self.qemu_config.get("memory_size", "512M")
            cpu_model = self.qemu_config.get("cpu_model", "Cascadelake-Server-v5")
            show_window = self.qemu_config.get("show_window", False)

            # Build QEMU command
            qemu_binary = "qemu-system-x86_64"
            assets_path = self.plugin_dir / "assets"
            rom_path = assets_path / "wrapper.rom"

            if not rom_path.exists():
                return False, f"QEMU ROM 文件不存在: {rom_path}"

            cmd = [
                qemu_binary,
                "-m", memory_size,
                "-cpu", cpu_model,
                "-bios", str(rom_path),
                "-device", "virtio-net-pci,netdev=net0",
                "-netdev", f"user,id=net0,hostfwd=tcp::{self.url.split(':')[-1]}-:8080",
            ]

            # Hardware acceleration
            if enable_hw_accel and hw_accelerator:
                cmd.extend(["-accel", hw_accelerator])

            # Display settings
            if not show_window:
                cmd.extend(["-display", "none"])

            # Start QEMU process
            self._qemu_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            # Wait for service to be ready
            await asyncio.sleep(5)

            if self._qemu_process.returncode is not None:
                _, stderr = await self._qemu_process.communicate()
                return False, f"QEMU 启动失败: {stderr.decode()}"

            return True, "QEMU 实例启动成功"

        except FileNotFoundError:
            return False, "QEMU 未安装或不在 PATH 中"
        except Exception as e:
            logger.error(f"Failed to start QEMU: {e}")
            return False, f"QEMU 启动失败: {str(e)}"

    async def stop(self) -> Tuple[bool, str]:
        """
        Stop the wrapper service.

        Returns:
            Tuple of (success, message)
        """
        match self.mode:
            case WrapperMode.DOCKER:
                return await self._stop_docker()
            case WrapperMode.QEMU:
                return await self._stop_qemu()
            case WrapperMode.REMOTE:
                self._connected = False
                return True, "已断开远程连接"

    async def _stop_docker(self) -> Tuple[bool, str]:
        """Stop Docker container."""
        try:
            container_name = self.docker_config.get("container_name", self.DOCKER_CONTAINER_NAME)
            code, _, stderr = await self._run_command(
                ["docker", "stop", container_name]
            )

            self._connected = False

            if code == 0:
                return True, "Docker 容器已停止"
            return False, f"停止失败: {stderr}"

        except Exception as e:
            return False, f"停止失败: {str(e)}"

    async def _stop_qemu(self) -> Tuple[bool, str]:
        """Stop QEMU instance."""
        try:
            if self._qemu_process:
                self._qemu_process.terminate()
                await self._qemu_process.wait()
                self._qemu_process = None

            self._connected = False
            return True, "QEMU 实例已停止"

        except Exception as e:
            return False, f"停止失败: {str(e)}"

    async def get_status(self) -> WrapperStatus:
        """
        Get wrapper service status.

        Returns:
            WrapperStatus object
        """
        status = WrapperStatus(
            mode=self.mode,
            url=self.url,
            connected=self._connected
        )

        # Add Docker-specific status
        if self.mode == WrapperMode.DOCKER and self._docker_manager:
            docker_status = self._docker_manager.get_status()
            status.wrapper_version = docker_status.get("commit_hash")

            # Check for updates
            update_available, _ = await self._docker_manager.check_for_updates()
            status.update_available = update_available

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

    async def update_wrapper(self, force: bool = False) -> Tuple[bool, str]:
        """
        Update wrapper-manager to the latest version.

        Args:
            force: Force update even if already up-to-date

        Returns:
            Tuple of (success, message)
        """
        if self.mode != WrapperMode.DOCKER:
            return False, "仅 Docker 模式支持自动更新"

        if not self._docker_manager:
            return False, "Docker 管理器未初始化"

        # Stop container first
        await self.stop()

        # Perform update
        image_name = self.docker_config.get("image_name", self.DOCKER_IMAGE_NAME)
        success, msg = await self._docker_manager.auto_setup(
            image_name=image_name,
            force_update=force
        )

        if success:
            # Restart container
            start_success, start_msg = await self.start()
            if start_success:
                return True, f"{msg}\n容器已重新启动"
            return True, f"{msg}\n警告: 容器启动失败: {start_msg}"

        return False, msg

    async def get_manager(self) -> Optional[WrapperManager]:
        """
        Get the WrapperManager instance.

        Returns:
            WrapperManager if connected, None otherwise
        """
        if not self._connected:
            success, _ = await self._connect_to_manager()
            if not success:
                return None

        return self._manager

    async def close(self):
        """Close the wrapper service connection."""
        if self._manager:
            await self._manager.close()
            self._manager = None
        self._connected = False

    # ==================== Helper Methods ====================

    async def _check_docker_available(self) -> bool:
        """Check if Docker is available."""
        code, _, _ = await self._run_command(["docker", "info"], timeout=10)
        return code == 0

    async def _check_image_exists(self, image_name: str) -> bool:
        """Check if Docker image exists."""
        code, stdout, _ = await self._run_command(
            ["docker", "images", "-q", image_name]
        )
        return code == 0 and bool(stdout.strip())

    async def _is_docker_container_running(self) -> bool:
        """Check if Docker container is running."""
        container_name = self.docker_config.get("container_name", self.DOCKER_CONTAINER_NAME)
        code, stdout, _ = await self._run_command([
            "docker", "ps",
            "--filter", f"name={container_name}",
            "--format", "{{.Names}}"
        ])
        return container_name in stdout

    def _check_qemu_available(self) -> bool:
        """Check if QEMU is available."""
        try:
            result = subprocess.run(
                ["qemu-system-x86_64", "--version"],
                capture_output=True,
                timeout=5
            )
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    async def _run_command(
        self,
        cmd: list,
        timeout: int = 60,
        env: dict = None
    ) -> Tuple[int, str, str]:
        """Run a shell command."""
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
