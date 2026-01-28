"""
Wrapper 服务管理。
负责连接远程 wrapper-manager。
"""

from dataclasses import dataclass
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

@dataclass
class WrapperStatus:
    """服务状态（Wrapper）。"""
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
            self.url = config.wrapper.url
            self.secure = config.wrapper.secure
            self.plugin_dir = config.plugin_dir or Path(".")
            self._debug_mode = config.debug_mode
        else:
            # 旧版参数形式（保留 url/secure 直连）
            self.url = config
            self.secure = secure
            self.plugin_dir = plugin_dir or Path(".")
            self._debug_mode = False

        # 日志注入
        self.logger = logger or get_logger()

        self._manager: Optional[WrapperManager] = None
        self._connected = False

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
            return await self._connect_to_manager()
        except Exception as e:
            self.logger.error(f"Failed to initialize wrapper service: {e}")
            return False, f"初始化失败: {str(e)}"

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
        return True, "远程模式无需启动服务"

    async def stop(self) -> Tuple[bool, str]:
        """停止 Wrapper 服务。"""
        await self.close()
        return True, "已断开远程连接"

    async def get_status(self) -> WrapperStatus:
        """获取 Wrapper 服务状态。"""
        status = WrapperStatus(
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
        # 关闭 gRPC 客户端
        if self._manager:
            await self._manager.close()
            self._manager = None

        self._connected = False

