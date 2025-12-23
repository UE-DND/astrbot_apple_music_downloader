"""
解密调度器。
按策略路由解密任务到合适实例。
"""

import asyncio
import random
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .instance_manager import InstanceManager, WrapperInstance


@dataclass
class DecryptTask:
    """解密任务。"""
    adam_id: str
    key: str
    sample: bytes
    sample_index: int
    created_at: datetime = datetime.now()


@dataclass
class DecryptResult:
    """解密结果。"""
    success: bool
    data: bytes
    error: Optional[str] = None
    instance_id: Optional[str] = None


class DecryptDispatcher:
    """解密任务调度器。"""

    def __init__(self, instance_manager: InstanceManager):
        """初始化调度器。"""
        self.instance_manager = instance_manager
        self._lock = asyncio.Lock()

    async def dispatch(self, task: DecryptTask) -> DecryptResult:
        """分发解密任务。"""
        # 选择实例
        instance = await self._select_instance(task.adam_id)

        if not instance:
            logger.error("No available wrapper instance")
            return DecryptResult(
                success=False,
                data=b"",
                error="没有可用的 wrapper 实例"
            )

        if not instance.proxy:
            logger.error(f"Instance {instance.instance_id} has no proxy")
            return DecryptResult(
                success=False,
                data=b"",
                error="实例代理未初始化"
            )

        # 更新实例最近使用时间
        instance.update_last_used()

        # 执行解密
        try:
            success, decrypted_data, error = await instance.proxy.decrypt(
                adam_id=task.adam_id,
                key=task.key,
                sample=task.sample,
                sample_index=task.sample_index
            )

            if success:
                logger.debug(
                    f"Decrypt success: {task.adam_id}[{task.sample_index}] "
                    f"via {instance.instance_id}"
                )
            else:
                logger.warning(
                    f"Decrypt failed: {task.adam_id}[{task.sample_index}] "
                    f"via {instance.instance_id}: {error}"
                )

            return DecryptResult(
                success=success,
                data=decrypted_data,
                error=error,
                instance_id=instance.instance_id
            )

        except Exception as e:
            logger.error(f"Decrypt exception: {e}")
            return DecryptResult(
                success=False,
                data=b"",
                error=str(e),
                instance_id=instance.instance_id
            )

    async def _select_instance(self, adam_id: str) -> Optional[WrapperInstance]:
        """选择最合适的解密实例。"""
        async with self._lock:
            instances = self.instance_manager.list_instances()

            # 过滤可用实例
            active_instances = [inst for inst in instances if inst.is_active()]

            if not active_instances:
                return None

            # 策略 1：复用最近处理过该 adam_id 的实例
            for instance in active_instances:
                if instance.proxy and instance.proxy.get_last_adam_id() == adam_id:
                    logger.debug(
                        f"Reusing instance {instance.instance_id} for {adam_id} (sticky)"
                    )
                    return instance

            # 策略 2：选择空闲实例（无最近 adam_id）
            idle_instances = [
                inst for inst in active_instances
                if inst.proxy and inst.proxy.get_last_adam_id() == ""
            ]

            if idle_instances:
                # 可按地区匹配（待实现）
                selected = random.choice(idle_instances)
                logger.debug(
                    f"Selected idle instance {selected.instance_id} for {adam_id}"
                )
                return selected

            # 策略 3：在全部可用实例中随机选择（地区过滤待实现）
            selected = random.choice(active_instances)
            logger.debug(
                f"Selected random instance {selected.instance_id} for {adam_id}"
            )
            return selected

    def _check_region_availability(
        self,
        adam_id: str,
        region: str
    ) -> bool:
        """检查歌曲在地区内的可用性。"""
        # TODO：实现真实的地区可用性校验
        # 目前假定所有地区均可用
        return True

    async def get_statistics(self) -> dict:
        """获取调度器统计信息。"""
        instances = self.instance_manager.list_instances()
        active_count = sum(1 for inst in instances if inst.is_active())

        return {
            "total_instances": len(instances),
            "active_instances": active_count,
            "idle_instances": sum(
                1 for inst in instances
                if inst.is_active() and inst.proxy and inst.proxy.get_last_adam_id() == ""
            ),
            "busy_instances": sum(
                1 for inst in instances
                if inst.is_active() and inst.proxy and inst.proxy.get_last_adam_id() != ""
            ),
        }
