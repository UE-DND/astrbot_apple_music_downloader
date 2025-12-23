"""
健康监控器。
监控实例健康并自动恢复。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Callable, Awaitable
from enum import Enum

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .instance_manager import InstanceManager, WrapperInstance, InstanceStatus


class HealthStatus(Enum):
    """实例健康状态。"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"  # 部分异常但仍可用
    UNHEALTHY = "unhealthy"  # 不可用，需要恢复
    RECOVERING = "recovering"  # 恢复中


@dataclass
class HealthCheckResult:
    """健康检查结果。"""
    instance_id: str
    status: HealthStatus
    timestamp: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    response_time_ms: Optional[float] = None
    consecutive_failures: int = 0


@dataclass
class RecoveryAction:
    """恢复动作。"""
    instance_id: str
    action_type: str  # 重启 / 重建 / 告警
    reason: str
    timestamp: datetime = field(default_factory=datetime.now)


class HealthMonitor:
    """实例健康监控与自动恢复。"""

    def __init__(
        self,
        instance_manager: InstanceManager,
        check_interval: int = 30,
        failure_threshold: int = 3,
        recovery_enabled: bool = True,
        max_recovery_attempts: int = 5,
    ):
        """初始化健康监控器。"""
        self.instance_manager = instance_manager
        self.check_interval = check_interval
        self.failure_threshold = failure_threshold
        self.recovery_enabled = recovery_enabled
        self.max_recovery_attempts = max_recovery_attempts

        # 健康记录
        self._health_history: Dict[str, List[HealthCheckResult]] = {}
        self._recovery_attempts: Dict[str, int] = {}
        self._last_recovery: Dict[str, datetime] = {}

        # 回调
        self._on_health_change: Optional[Callable[[str, HealthStatus, HealthStatus], Awaitable[None]]] = None
        self._on_recovery_start: Optional[Callable[[RecoveryAction], Awaitable[None]]] = None
        self._on_recovery_complete: Optional[Callable[[str, bool, str], Awaitable[None]]] = None

        # 控制
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None

        logger.info(f"Health monitor initialized (interval={check_interval}s, threshold={failure_threshold})")

    def set_health_change_callback(
        self,
        callback: Callable[[str, HealthStatus, HealthStatus], Awaitable[None]]
    ):
        """设置健康状态变化回调。"""
        self._on_health_change = callback

    def set_recovery_start_callback(
        self,
        callback: Callable[[RecoveryAction], Awaitable[None]]
    ):
        """设置恢复开始回调。"""
        self._on_recovery_start = callback

    def set_recovery_complete_callback(
        self,
        callback: Callable[[str, bool, str], Awaitable[None]]
    ):
        """设置恢复完成回调。"""
        self._on_recovery_complete = callback

    async def start(self):
        """启动健康监控。"""
        if self._running:
            logger.warning("Health monitor already running")
            return

        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("Health monitor started")

    async def stop(self):
        """停止健康监控。"""
        if not self._running:
            return

        self._running = False

        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        logger.info("Health monitor stopped")

    async def _monitor_loop(self):
        """主监控循环。"""
        while self._running:
            try:
                await self._perform_health_checks()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in health monitor loop: {e}", exc_info=True)
                await asyncio.sleep(self.check_interval)

    async def _perform_health_checks(self):
        """对所有实例执行健康检查。"""
        instances = self.instance_manager.list_instances()

        if not instances:
            return

        logger.debug(f"Performing health checks on {len(instances)} instances")

        # 并发执行检查
        tasks = []
        for instance in instances:
            if instance.status != InstanceStatus.STOPPED:
                task = self._check_instance_health(instance)
                tasks.append(task)

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # 处理结果
            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"Health check exception: {result}")
                elif result:
                    await self._process_health_result(result)

    async def _check_instance_health(self, instance: WrapperInstance) -> HealthCheckResult:
        """检查单个实例健康状态。"""
        instance_id = instance.instance_id
        start_time = datetime.now()

        try:
        # 执行健康检查
            if not instance.proxy:
                return HealthCheckResult(
                    instance_id=instance_id,
                    status=HealthStatus.UNHEALTHY,
                    error="No proxy available",
                )

        # 检查代理响应
            healthy = await asyncio.wait_for(
                instance.proxy.health_check(),
                timeout=5.0
            )

        # 计算响应时间
            response_time = (datetime.now() - start_time).total_seconds() * 1000

            if healthy:
                return HealthCheckResult(
                    instance_id=instance_id,
                    status=HealthStatus.HEALTHY,
                    response_time_ms=response_time,
                    consecutive_failures=0,
                )
            else:
                # 获取历史失败记录
                prev_failures = self._get_consecutive_failures(instance_id)

                return HealthCheckResult(
                    instance_id=instance_id,
                    status=HealthStatus.DEGRADED,
                    error="Health check returned false",
                    response_time_ms=response_time,
                    consecutive_failures=prev_failures + 1,
                )

        except asyncio.TimeoutError:
            prev_failures = self._get_consecutive_failures(instance_id)
            return HealthCheckResult(
                instance_id=instance_id,
                status=HealthStatus.UNHEALTHY,
                error="Health check timeout",
                consecutive_failures=prev_failures + 1,
            )

        except Exception as e:
            prev_failures = self._get_consecutive_failures(instance_id)
            return HealthCheckResult(
                instance_id=instance_id,
                status=HealthStatus.UNHEALTHY,
                error=str(e),
                consecutive_failures=prev_failures + 1,
            )

    def _get_consecutive_failures(self, instance_id: str) -> int:
        """获取实例连续失败次数。"""
        history = self._health_history.get(instance_id, [])
        if not history:
            return 0

        # 从末尾统计连续失败次数
        failures = 0
        for result in reversed(history):
            if result.status == HealthStatus.HEALTHY:
                break
            failures += 1

        return failures

    async def _process_health_result(self, result: HealthCheckResult):
        """处理健康检查结果并触发恢复。"""
        instance_id = result.instance_id

        # 记录结果
        if instance_id not in self._health_history:
            self._health_history[instance_id] = []

        self._health_history[instance_id].append(result)

        # 仅保留最近 100 条
        if len(self._health_history[instance_id]) > 100:
            self._health_history[instance_id] = self._health_history[instance_id][-100:]

        # 判断是否需要恢复
        if result.status == HealthStatus.UNHEALTHY:
            if result.consecutive_failures >= self.failure_threshold:
                await self._trigger_recovery(result)

        # 记录健康状态
        if result.status != HealthStatus.HEALTHY:
            logger.warning(
                f"Instance {instance_id} health: {result.status.value} "
                f"(failures: {result.consecutive_failures}, error: {result.error})"
            )

    async def _trigger_recovery(self, result: HealthCheckResult):
        """触发异常实例恢复。"""
        instance_id = result.instance_id

        if not self.recovery_enabled:
            logger.warning(f"Recovery disabled, skipping instance {instance_id}")
            return

        # 检查恢复次数
        attempts = self._recovery_attempts.get(instance_id, 0)
        if attempts >= self.max_recovery_attempts:
            logger.error(
                f"Instance {instance_id} exceeded max recovery attempts ({self.max_recovery_attempts}), "
                f"marking as failed"
            )
            instance = self.instance_manager.get_instance(instance_id)
            if instance:
                instance.status = InstanceStatus.FAILED
                instance.no_restart = True
            return

        # 检查是否刚恢复过（指数退避）
        if instance_id in self._last_recovery:
            last_recovery = self._last_recovery[instance_id]
            min_interval = timedelta(seconds=30 * (2 ** attempts))  # 指数退避

            if datetime.now() - last_recovery < min_interval:
                logger.debug(f"Skipping recovery for {instance_id}, too soon after last attempt")
                return

        # 创建恢复动作
        action = RecoveryAction(
            instance_id=instance_id,
            action_type="restart",
            reason=f"Consecutive failures: {result.consecutive_failures}, error: {result.error}",
        )

        # 通知恢复开始
        if self._on_recovery_start:
            try:
                await self._on_recovery_start(action)
            except Exception as e:
                logger.error(f"Error in recovery start callback: {e}")

        # 执行恢复
        logger.info(f"Starting recovery for instance {instance_id} (attempt {attempts + 1}/{self.max_recovery_attempts})")
        success, message = await self._perform_recovery(instance_id)

        # 更新跟踪状态
        self._recovery_attempts[instance_id] = attempts + 1
        self._last_recovery[instance_id] = datetime.now()

        # 通知恢复完成
        if self._on_recovery_complete:
            try:
                await self._on_recovery_complete(instance_id, success, message)
            except Exception as e:
                logger.error(f"Error in recovery complete callback: {e}")

        if success:
            logger.info(f"Recovery successful for instance {instance_id}: {message}")
            # 重置失败计数
            self._recovery_attempts[instance_id] = 0
        else:
            logger.error(f"Recovery failed for instance {instance_id}: {message}")

    async def _perform_recovery(self, instance_id: str) -> tuple[bool, str]:
        """执行实例恢复动作。"""
        instance = self.instance_manager.get_instance(instance_id)
        if not instance:
            return False, "Instance not found"

        try:
            # 标记为恢复中
            old_status = instance.status
            instance.status = InstanceStatus.INITIALIZING

            # 停止代理
            if instance.proxy:
                try:
                    await instance.proxy.stop()
                except Exception as e:
                    logger.warning(f"Error stopping proxy during recovery: {e}")

            # 重启代理
            if instance.proxy:
                await asyncio.sleep(1)  # 短暂延时
                await instance.proxy.start()

                # 复核健康状态
                await asyncio.sleep(2)
                healthy = await instance.proxy.health_check()

                if healthy:
                    instance.status = InstanceStatus.ACTIVE
                    return True, "Instance restarted successfully"
                else:
                    instance.status = old_status
                    return False, "Instance restart failed health check"
            else:
                instance.status = old_status
                return False, "No proxy to restart"

        except Exception as e:
            logger.error(f"Error during recovery: {e}", exc_info=True)
            instance.status = InstanceStatus.FAILED
            return False, f"Recovery exception: {str(e)}"

    def get_health_status(self, instance_id: str) -> Optional[HealthStatus]:
        """获取实例当前健康状态。"""
        history = self._health_history.get(instance_id)
        if not history:
            return None

        return history[-1].status

    def get_health_metrics(self, instance_id: str) -> Dict:
        """获取实例健康指标。"""
        history = self._health_history.get(instance_id, [])

        if not history:
            return {
                "total_checks": 0,
                "healthy_count": 0,
                "unhealthy_count": 0,
                "avg_response_time_ms": 0,
                "consecutive_failures": 0,
                "recovery_attempts": 0,
            }

        healthy_count = sum(1 for r in history if r.status == HealthStatus.HEALTHY)
        unhealthy_count = sum(1 for r in history if r.status == HealthStatus.UNHEALTHY)

        # 计算平均响应时间
        response_times = [r.response_time_ms for r in history if r.response_time_ms is not None]
        avg_response_time = sum(response_times) / len(response_times) if response_times else 0

        return {
            "total_checks": len(history),
            "healthy_count": healthy_count,
            "unhealthy_count": unhealthy_count,
            "avg_response_time_ms": round(avg_response_time, 2),
            "consecutive_failures": history[-1].consecutive_failures,
            "recovery_attempts": self._recovery_attempts.get(instance_id, 0),
            "last_check": history[-1].timestamp.isoformat(),
            "last_status": history[-1].status.value,
        }

    def get_all_metrics(self) -> Dict[str, Dict]:
        """获取所有实例健康指标。"""
        return {
            instance_id: self.get_health_metrics(instance_id)
            for instance_id in self._health_history.keys()
        }
