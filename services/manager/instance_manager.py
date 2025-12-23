"""
实例管理器。
负责管理 wrapper 账户实例的生命周期。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from enum import Enum

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .wrapper_proxy import WrapperProxy, WrapperProxyConfig, create_instance_id


class InstanceStatus(Enum):
    """实例状态。"""
    INITIALIZING = "initializing"
    ACTIVE = "active"
    FAILED = "failed"
    STOPPED = "stopped"


@dataclass
class WrapperInstance:
    """实例数据（Wrapper）。"""
    instance_id: str
    username: str
    region: str
    status: InstanceStatus = InstanceStatus.INITIALIZING
    created_at: datetime = field(default_factory=datetime.now)
    last_used: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    no_restart: bool = False  # 为 True 时不自动重启

    # 指向 wrapper 容器的代理
    proxy: Optional[WrapperProxy] = None

    def update_last_used(self):
        """更新最近使用时间。"""
        self.last_used = datetime.now()

    def is_active(self) -> bool:
        """检查实例是否可用。"""
        return self.status == InstanceStatus.ACTIVE and self.proxy is not None


class InstanceManager:
    """实例管理器（Wrapper）。"""

    def __init__(self, proxy_config: Optional[WrapperProxyConfig] = None):
        """初始化实例管理器。"""
        self.proxy_config = proxy_config or WrapperProxyConfig()
        self._instances: Dict[str, WrapperInstance] = {}
        self._username_to_id: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def add_instance(
        self,
        username: str,
        password: str,
        region: str = "us",
    ) -> Tuple[bool, str, Optional[WrapperInstance]]:
        """添加新的 wrapper 实例。"""
        async with self._lock:
            # 生成实例 ID
            instance_id = create_instance_id(username)

            # 检查是否已存在
            if instance_id in self._instances:
                existing = self._instances[instance_id]
                return False, f"账户 {username} 已存在", existing

            try:
                # 创建 wrapper 代理
                proxy = WrapperProxy(
                    instance_id=instance_id,
                    username=username,
                    region=region,
                    config=self.proxy_config,
                )

                # 启动代理
                await proxy.start()

                # 创建实例
                instance = WrapperInstance(
                    instance_id=instance_id,
                    username=username,
                    region=region,
                    status=InstanceStatus.ACTIVE,
                    proxy=proxy,
                )

                # 保存实例
                self._instances[instance_id] = instance
                self._username_to_id[username] = instance_id

                logger.info(f"Added instance: {username} ({instance_id})")
                return True, f"成功添加账户 {username}", instance

            except Exception as e:
                logger.error(f"Failed to add instance {username}: {e}")
                return False, f"添加账户失败: {str(e)}", None

    async def remove_instance(self, instance_id: str) -> Tuple[bool, str]:
        """移除 wrapper 实例。"""
        async with self._lock:
            if instance_id not in self._instances:
                return False, "实例不存在"

            try:
                instance = self._instances[instance_id]

                # 停止代理
                if instance.proxy:
                    await instance.proxy.stop()

                # 移除追踪记录
                del self._instances[instance_id]
                if instance.username in self._username_to_id:
                    del self._username_to_id[instance.username]

                logger.info(f"Removed instance: {instance.username} ({instance_id})")
                return True, f"成功移除账户 {instance.username}"

            except Exception as e:
                logger.error(f"Failed to remove instance {instance_id}: {e}")
                return False, f"移除账户失败: {str(e)}"

    def get_instance(self, instance_id: str) -> Optional[WrapperInstance]:
        """按 ID 获取实例。"""
        return self._instances.get(instance_id)

    def get_instance_by_username(self, username: str) -> Optional[WrapperInstance]:
        """按用户名获取实例。"""
        instance_id = self._username_to_id.get(username)
        if instance_id:
            return self._instances.get(instance_id)
        return None

    def list_instances(self) -> List[WrapperInstance]:
        """列出全部实例。"""
        return list(self._instances.values())

    def get_regions(self) -> List[str]:
        """获取可用地区列表。"""
        regions = set()
        for instance in self._instances.values():
            if instance.is_active():
                regions.add(instance.region)
        return list(regions)

    def get_client_count(self) -> int:
        """获取活跃实例数量。"""
        return sum(1 for inst in self._instances.values() if inst.is_active())

    async def health_check_all(self) -> Dict[str, bool]:
        """对所有实例执行健康检查。"""
        results = {}
        tasks = []

        for instance_id, instance in self._instances.items():
            if instance.proxy:
                task = instance.proxy.health_check()
                tasks.append((instance_id, task))

        # 并发执行健康检查
        for instance_id, task in tasks:
            try:
                healthy = await task
                results[instance_id] = healthy
            except Exception as e:
                logger.error(f"Health check failed for {instance_id}: {e}")
                results[instance_id] = False

        return results

    async def cleanup_inactive(self, max_idle_seconds: int = 3600):
        """清理长时间未使用的实例。"""
        now = datetime.now()
        to_remove = []

        for instance_id, instance in self._instances.items():
            idle_seconds = (now - instance.last_used).total_seconds()
            if idle_seconds > max_idle_seconds and not instance.no_restart:
                to_remove.append(instance_id)

        for instance_id in to_remove:
            logger.info(f"Cleaning up idle instance: {instance_id}")
            await self.remove_instance(instance_id)

    async def shutdown_all(self):
        """关闭所有实例。"""
        logger.info("Shutting down all instances...")
        instance_ids = list(self._instances.keys())

        for instance_id in instance_ids:
            await self.remove_instance(instance_id)

        logger.info("All instances shut down")


from typing import Tuple
