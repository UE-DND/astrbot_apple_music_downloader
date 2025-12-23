"""
核心模块单元测试。
覆盖 WrapperProxy、InstanceManager 与 Dispatcher。
"""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

import sys
from pathlib import Path

# 将项目根目录加入路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from services.manager import (
    WrapperProxy,
    WrapperProxyConfig,
    InstanceManager,
    WrapperInstance,
    DecryptDispatcher,
    DecryptTask,
    InstanceStatus,
)


@pytest.fixture
def wrapper_proxy_config():
    """创建 wrapper 代理配置。"""
    return WrapperProxyConfig(
        host="127.0.0.1",
        decrypt_port=10020,
        m3u8_port=20020,
        timeout=5,
    )


@pytest.fixture
def wrapper_proxy(wrapper_proxy_config):
    """创建 wrapper 代理实例。"""
    return WrapperProxy(
        instance_id="test-instance",
        username="test@example.com",
        region="us",
        config=wrapper_proxy_config,
    )


@pytest.mark.asyncio
async def test_wrapper_proxy_initialization(wrapper_proxy):
    """测试 wrapper 代理初始化。"""
    assert wrapper_proxy.instance_id == "test-instance"
    assert wrapper_proxy.username == "test@example.com"
    assert wrapper_proxy.region == "us"
    assert wrapper_proxy._session is None


@pytest.mark.asyncio
async def test_wrapper_proxy_start_stop(wrapper_proxy):
    """测试 wrapper 代理启动与停止。"""
    # 启动
    await wrapper_proxy.start()
    assert wrapper_proxy._session is not None

    # 停止
    await wrapper_proxy.stop()
    assert wrapper_proxy._session is None


@pytest.mark.asyncio
async def test_wrapper_proxy_health_check_success(wrapper_proxy):
    """测试健康检查成功。"""
    # 模拟成功的 HTTP 响应
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.__aenter__.return_value = mock_response
        mock_response.__aexit__.return_value = None
        mock_get.return_value = mock_response

        await wrapper_proxy.start()
        healthy = await wrapper_proxy.health_check()
        await wrapper_proxy.stop()

        assert healthy is True


@pytest.mark.asyncio
async def test_wrapper_proxy_health_check_failure(wrapper_proxy):
    """测试健康检查失败。"""
    # 模拟失败的 HTTP 响应
    with patch('aiohttp.ClientSession.get') as mock_get:
        mock_get.side_effect = Exception("Connection refused")

        await wrapper_proxy.start()
        healthy = await wrapper_proxy.health_check()
        await wrapper_proxy.stop()

        assert healthy is False


@pytest.mark.asyncio
async def test_wrapper_proxy_decrypt_success(wrapper_proxy):
    """测试解密成功。"""
    # 模拟成功的 socket 响应
    with patch("asyncio.open_connection", new_callable=AsyncMock) as mock_open:
        reader = AsyncMock()
        reader.readexactly = AsyncMock(return_value=b"decrypted_data")
        writer = AsyncMock()
        writer.write = MagicMock()
        writer.drain = AsyncMock()
        writer.close = MagicMock()
        writer.wait_closed = AsyncMock()
        mock_open.return_value = (reader, writer)

        await wrapper_proxy.start()
        success, data, error = await wrapper_proxy.decrypt(
            adam_id="123456",
            key="test_key",
            sample=b"encrypted_sample",
            sample_index=0,
        )
        await wrapper_proxy.stop()

        assert success is True
        assert data == b"decrypted_data"
        assert error is None


@pytest.mark.asyncio
async def test_wrapper_proxy_decrypt_failure(wrapper_proxy):
    """测试解密失败。"""
    # 模拟连接失败
    with patch(
        "asyncio.open_connection",
        new_callable=AsyncMock,
        side_effect=ConnectionRefusedError,
    ):

        await wrapper_proxy.start()
        success, data, error = await wrapper_proxy.decrypt(
            adam_id="123456",
            key="test_key",
            sample=b"encrypted_sample",
            sample_index=0,
        )
        await wrapper_proxy.stop()

        assert success is False
        assert data == b""
        assert "Connection refused" in error


@pytest.fixture
def instance_manager():
    """创建实例管理器。"""
    return InstanceManager(WrapperProxyConfig())


@pytest.mark.asyncio
async def test_instance_manager_initialization(instance_manager):
    """测试实例管理器初始化。"""
    assert len(instance_manager._instances) == 0
    assert len(instance_manager._username_to_id) == 0


@pytest.mark.asyncio
async def test_add_instance_success(instance_manager):
    """测试成功添加实例。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        success, message, instance = await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        assert success is True
        assert instance is not None
        assert instance.username == "test@example.com"
        assert instance.region == "us"
        assert instance.status == InstanceStatus.ACTIVE


@pytest.mark.asyncio
async def test_add_instance_duplicate(instance_manager):
    """测试添加重复实例。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加第一个实例
        await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        # 尝试添加重复实例
        success, message, instance = await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        assert success is False
        assert "已存在" in message


@pytest.mark.asyncio
async def test_remove_instance(instance_manager):
    """测试移除实例。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        with patch.object(WrapperProxy, 'stop', new_callable=AsyncMock):
            # 添加实例
            success, _, instance = await instance_manager.add_instance(
                username="test@example.com",
                password="password123",
                region="us",
            )
            instance_id = instance.instance_id

            # 移除实例
            success, message = await instance_manager.remove_instance(instance_id)

            assert success is True
            assert len(instance_manager._instances) == 0


@pytest.mark.asyncio
async def test_get_instance(instance_manager):
    """测试按 ID 获取实例。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加实例
        _, _, instance = await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )
        instance_id = instance.instance_id

        # 获取实例
        retrieved = instance_manager.get_instance(instance_id)

        assert retrieved is not None
        assert retrieved.instance_id == instance_id


@pytest.mark.asyncio
async def test_get_instance_by_username(instance_manager):
    """测试按用户名获取实例。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加实例
        await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        # 按用户名获取实例
        retrieved = instance_manager.get_instance_by_username("test@example.com")

        assert retrieved is not None
        assert retrieved.username == "test@example.com"


@pytest.mark.asyncio
async def test_list_instances(instance_manager):
    """测试列出所有实例。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加多个实例
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # 列出实例
        instances = instance_manager.list_instances()

        assert len(instances) == 3


@pytest.mark.asyncio
async def test_get_regions(instance_manager):
    """测试获取可用地区。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加不同地区的实例
        await instance_manager.add_instance(
            username="test1@example.com",
            password="password123",
            region="us",
        )
        await instance_manager.add_instance(
            username="test2@example.com",
            password="password123",
            region="cn",
        )

        # 获取地区
        regions = instance_manager.get_regions()

        assert "us" in regions
        assert "cn" in regions


@pytest.mark.asyncio
async def test_get_client_count(instance_manager):
    """测试获取活跃客户端数量。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加实例
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # 获取数量
        count = instance_manager.get_client_count()

        assert count == 3


@pytest.mark.asyncio
async def test_health_check_all(instance_manager):
    """测试所有实例健康检查。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        with patch.object(WrapperProxy, 'health_check', new_callable=AsyncMock, return_value=True):
            # 添加实例
            for i in range(3):
                await instance_manager.add_instance(
                    username=f"test{i}@example.com",
                    password="password123",
                    region="us",
                )

            # 执行全部健康检查
            results = await instance_manager.health_check_all()

            assert len(results) == 3
            assert all(healthy for healthy in results.values())


@pytest.fixture
def dispatcher(instance_manager):
    """创建解密调度器。"""
    return DecryptDispatcher(instance_manager)


@pytest.mark.asyncio
async def test_dispatcher_initialization(dispatcher):
    """测试调度器初始化。"""
    assert dispatcher.instance_manager is not None


@pytest.mark.asyncio
async def test_dispatcher_select_instance_no_instances(dispatcher):
    """测试无实例时的选择逻辑。"""
    instance = await dispatcher._select_instance("123456")
    assert instance is None


@pytest.mark.asyncio
async def test_dispatcher_select_instance_with_instances(instance_manager, dispatcher):
    """测试有可用实例时的选择逻辑。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加实例
        await instance_manager.add_instance(
            username="test@example.com",
            password="password123",
            region="us",
        )

        # 选择实例
        instance = await dispatcher._select_instance("123456")

        assert instance is not None
        assert instance.username == "test@example.com"


@pytest.mark.asyncio
async def test_dispatcher_sticky_routing(instance_manager, dispatcher):
    """测试相同 adam_id 的粘性路由。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加多个实例
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # 对同一 adam_id 选择两次实例
        instance1 = await dispatcher._select_instance("123456")
        instance1.proxy.set_last_adam_id("123456")
        instance2 = await dispatcher._select_instance("123456")

        # 应为同一实例（粘性路由）
        assert instance1 is not None
        assert instance2 is not None
        assert instance1.instance_id == instance2.instance_id


@pytest.mark.asyncio
async def test_dispatcher_load_balancing(instance_manager, dispatcher):
    """测试多实例负载均衡。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        # 添加多个实例
        for i in range(3):
            await instance_manager.add_instance(
                username=f"test{i}@example.com",
                password="password123",
                region="us",
            )

        # 为不同 adam_id 选择实例
        instances = []
        for i in range(10):
            instance = await dispatcher._select_instance(f"adam-{i}")
            instances.append(instance)

        # 验证使用了不同实例
        instance_ids = {inst.instance_id for inst in instances if inst}
        assert len(instance_ids) > 1  # 应使用多个实例


@pytest.mark.asyncio
async def test_dispatcher_dispatch_success(instance_manager, dispatcher):
    """测试任务分发成功。"""
    with patch.object(WrapperProxy, 'start', new_callable=AsyncMock):
        with patch.object(WrapperProxy, 'decrypt', new_callable=AsyncMock) as mock_decrypt:
            mock_decrypt.return_value = (True, b"decrypted_data", None)

            # 添加实例
            await instance_manager.add_instance(
                username="test@example.com",
                password="password123",
                region="us",
            )

            # 创建任务
            task = DecryptTask(
                adam_id="123456",
                key="test_key",
                sample=b"encrypted_sample",
                sample_index=0,
            )

            # 分发
            result = await dispatcher.dispatch(task)

            assert result.success is True
            assert result.data == b"decrypted_data"
            assert result.error is None


@pytest.mark.asyncio
async def test_dispatcher_dispatch_no_instances(dispatcher):
    """测试无实例时的任务分发。"""
    # 创建任务
    task = DecryptTask(
        adam_id="123456",
        key="test_key",
        sample=b"encrypted_sample",
        sample_index=0,
    )

    # 分发
    result = await dispatcher.dispatch(task)

    assert result.success is False
    assert "没有可用的 wrapper 实例" in result.error


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
