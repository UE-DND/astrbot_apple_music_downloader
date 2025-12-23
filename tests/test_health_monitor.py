"""
健康监控单元测试。
覆盖健康检查、失败检测与自动恢复。
"""

import asyncio
import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import sys
from pathlib import Path

# 将项目根目录加入路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.manager import (
    HealthMonitor,
    HealthStatus,
    InstanceManager,
    WrapperInstance,
    WrapperProxy,
    WrapperProxyConfig,
    InstanceStatus,
)


@pytest.fixture
def instance_manager():
    """创建实例管理器夹具。"""
    return InstanceManager(WrapperProxyConfig())


@pytest.fixture
def health_monitor(instance_manager):
    """创建健康监控夹具。"""
    return HealthMonitor(
        instance_manager=instance_manager,
        check_interval=1,  # 测试用短间隔
        failure_threshold=2,
        recovery_enabled=True,
        max_recovery_attempts=3,
    )


@pytest.mark.asyncio
async def test_health_monitor_initialization(health_monitor):
    """测试健康监控初始化。"""
    assert health_monitor.check_interval == 1
    assert health_monitor.failure_threshold == 2
    assert health_monitor.recovery_enabled is True
    assert health_monitor.max_recovery_attempts == 3
    assert health_monitor._running is False


@pytest.mark.asyncio
async def test_health_monitor_start_stop(health_monitor):
    """测试健康监控启动与停止。"""
    # 启动监控
    await health_monitor.start()
    assert health_monitor._running is True
    assert health_monitor._monitor_task is not None

    # 停止监控
    await health_monitor.stop()
    assert health_monitor._running is False


@pytest.mark.asyncio
async def test_health_check_healthy_instance(instance_manager, health_monitor):
    """测试健康实例的健康检查。"""
    # 创建健康代理的模拟实例
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    # 模拟代理
    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=True)

    instance_manager._instances["test-instance"] = instance

    # 执行健康检查
    result = await health_monitor._check_instance_health(instance)

    assert result.instance_id == "test-instance"
    assert result.status == HealthStatus.HEALTHY
    assert result.consecutive_failures == 0
    assert result.error is None
    assert result.response_time_ms is not None


@pytest.mark.asyncio
async def test_health_check_unhealthy_instance(instance_manager, health_monitor):
    """测试不健康实例的健康检查。"""
    # 创建不健康代理的模拟实例
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    # 模拟返回 False 的代理
    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)

    instance_manager._instances["test-instance"] = instance

    # 执行健康检查
    result = await health_monitor._check_instance_health(instance)

    assert result.instance_id == "test-instance"
    assert result.status == HealthStatus.DEGRADED
    assert result.consecutive_failures == 1
    assert "Health check returned false" in result.error


@pytest.mark.asyncio
async def test_health_check_timeout(instance_manager, health_monitor):
    """测试健康检查超时。"""
    # 创建慢速代理的模拟实例
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    # 模拟超时代理
    async def slow_health_check():
        await asyncio.sleep(10)  # 超过超时阈值
        return True

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = slow_health_check

    instance_manager._instances["test-instance"] = instance

    # 执行健康检查
    result = await health_monitor._check_instance_health(instance)

    assert result.instance_id == "test-instance"
    assert result.status == HealthStatus.UNHEALTHY
    assert result.consecutive_failures == 1
    assert "timeout" in result.error.lower()


@pytest.mark.asyncio
async def test_consecutive_failure_tracking(instance_manager, health_monitor):
    """测试连续失败计数。"""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)

    instance_manager._instances["test-instance"] = instance

    # 执行多次健康检查
    for i in range(3):
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # 检查连续失败次数
    failures = health_monitor._get_consecutive_failures("test-instance")
    assert failures == 3


@pytest.mark.asyncio
async def test_recovery_trigger(instance_manager, health_monitor):
    """测试自动恢复触发。"""
    # 创建不健康实例
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(side_effect=asyncio.TimeoutError)
    instance.proxy.stop = AsyncMock()
    instance.proxy.start = AsyncMock()

    instance_manager._instances["test-instance"] = instance

    health_monitor._perform_recovery = AsyncMock(return_value=(True, "ok"))

    # 模拟恢复回调
    recovery_start_called = False
    recovery_complete_called = False

    async def on_recovery_start(action):
        nonlocal recovery_start_called
        recovery_start_called = True

    async def on_recovery_complete(instance_id, success, message):
        nonlocal recovery_complete_called
        recovery_complete_called = True

    health_monitor.set_recovery_start_callback(on_recovery_start)
    health_monitor.set_recovery_complete_callback(on_recovery_complete)

    # 触发失败
    for i in range(3):
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # 等待恢复
    await asyncio.sleep(0.1)

    # 检查回调被调用
    assert recovery_start_called
    assert recovery_complete_called


@pytest.mark.asyncio
async def test_recovery_exponential_backoff(instance_manager, health_monitor):
    """测试恢复重试指数退避。"""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)
    instance.proxy.stop = AsyncMock()
    instance.proxy.start = AsyncMock()

    instance_manager._instances["test-instance"] = instance

    # 第一次恢复
    health_monitor._recovery_attempts["test-instance"] = 1
    health_monitor._last_recovery["test-instance"] = datetime.now() - timedelta(seconds=1)

    # 立即触发恢复（应被退避阻止）
    result = await health_monitor._check_instance_health(instance)
    result.consecutive_failures = 3  # 强制触发阈值

    await health_monitor._trigger_recovery(result)

    # 退避期间不应增加次数
    assert health_monitor._recovery_attempts["test-instance"] == 1


@pytest.mark.asyncio
async def test_max_recovery_attempts(instance_manager, health_monitor):
    """测试最大恢复次数限制。"""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=False)
    instance.proxy.stop = AsyncMock()
    instance.proxy.start = AsyncMock()

    instance_manager._instances["test-instance"] = instance

    # 设置为最大次数
    health_monitor._recovery_attempts["test-instance"] = 3  # 最大恢复次数

    result = await health_monitor._check_instance_health(instance)
    result.consecutive_failures = 3

    await health_monitor._trigger_recovery(result)

    # 实例应标记为失败
    assert instance.status == InstanceStatus.FAILED
    assert instance.no_restart is True


@pytest.mark.asyncio
async def test_health_metrics(instance_manager, health_monitor):
    """测试健康指标采集。"""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=True)

    instance_manager._instances["test-instance"] = instance

    # 执行多次检查
    for _ in range(5):
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # 获取指标
    metrics = health_monitor.get_health_metrics("test-instance")

    assert metrics["total_checks"] == 5
    assert metrics["healthy_count"] == 5
    assert metrics["unhealthy_count"] == 0
    assert metrics["avg_response_time_ms"] > 0
    assert metrics["consecutive_failures"] == 0


@pytest.mark.asyncio
async def test_get_all_metrics(instance_manager, health_monitor):
    """测试获取全部实例指标。"""
    # 创建多个实例
    for i in range(3):
        instance = WrapperInstance(
            instance_id=f"test-instance-{i}",
            username=f"test{i}@example.com",
            region="us",
            status=InstanceStatus.ACTIVE,
        )
        instance.proxy = AsyncMock(spec=WrapperProxy)
        instance.proxy.health_check = AsyncMock(return_value=True)
        instance_manager._instances[f"test-instance-{i}"] = instance

        # 执行健康检查
        result = await health_monitor._check_instance_health(instance)
        await health_monitor._process_health_result(result)

    # 获取全部指标
    all_metrics = health_monitor.get_all_metrics()

    assert len(all_metrics) == 3
    assert "test-instance-0" in all_metrics
    assert "test-instance-1" in all_metrics
    assert "test-instance-2" in all_metrics


@pytest.mark.asyncio
async def test_health_status_persistence(instance_manager, health_monitor):
    """测试健康状态跨检查的持久性。"""
    instance = WrapperInstance(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        status=InstanceStatus.ACTIVE,
    )

    instance.proxy = AsyncMock(spec=WrapperProxy)
    instance.proxy.health_check = AsyncMock(return_value=True)

    instance_manager._instances["test-instance"] = instance

    # 执行第一次检查
    result1 = await health_monitor._check_instance_health(instance)
    await health_monitor._process_health_result(result1)

    # 获取状态
    status = health_monitor.get_health_status("test-instance")
    assert status == HealthStatus.HEALTHY

    # 执行第二次检查
    result2 = await health_monitor._check_instance_health(instance)
    await health_monitor._process_health_result(result2)

    # 状态应保持健康
    status = health_monitor.get_health_status("test-instance")
    assert status == HealthStatus.HEALTHY


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
