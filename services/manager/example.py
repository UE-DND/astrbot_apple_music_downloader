"""
原生 Wrapper 管理器示例与测试。
演示如何使用原生实现。
"""

import asyncio
from wrapper_manager_native import (
    NativeWrapperManagerServer,
    WrapperProxyConfig
)


async def example_server():
    """示例：启动原生 wrapper-manager 服务。"""
    print("=" * 60)
    print("Starting Native Wrapper Manager Server")
    print("=" * 60)

    # 创建代理配置
    proxy_config = WrapperProxyConfig(
        host="127.0.0.1",
        decrypt_port=10020,
        m3u8_port=20020,
        account_port=30020,
        timeout=30
    )

    # 创建服务
    server = NativeWrapperManagerServer(
        host="127.0.0.1",
        port=18923,
        proxy_config=proxy_config
    )

    try:
        # 启动服务
        await server.start()
        print("\n✅ Server started successfully!")
        print(f"   - gRPC endpoint: 127.0.0.1:18923")
        print(f"   - Wrapper proxy: 127.0.0.1:10020 (decrypt)")
        print(f"   - Wrapper proxy: 127.0.0.1:20020 (m3u8)")
        print("\nPress Ctrl+C to stop...")

        # 保持运行
        await server.wait_for_termination()

    except KeyboardInterrupt:
        print("\n\n⏹  Stopping server...")
        await server.stop()
        print("✅ Server stopped")


async def example_client():
    """示例：作为客户端连接服务。"""
    print("\n" + "=" * 60)
    print("Testing Client Connection")
    print("=" * 60)

    # 引入现有 gRPC 客户端
    from ...core.grpc import WrapperManager

    try:
        # 连接服务
        manager = WrapperManager(
            url="127.0.0.1:18923",
            secure=False
        )

        print("\n🔗 Connecting to wrapper-manager...")

        # 获取状态
        status = await manager.status()
        print(f"\n✅ Connected successfully!")
        print(f"   - Ready: {status.ready}")
        print(f"   - Status: {status.status}")
        print(f"   - Client count: {status.client_count}")
        print(f"   - Regions: {', '.join(status.regions) if status.regions else 'None'}")

        # 关闭连接
        await manager.close()

    except Exception as e:
        print(f"\n❌ Connection failed: {e}")


async def example_standalone_components():
    """示例：不依赖 gRPC 独立使用组件。"""
    print("\n" + "=" * 60)
    print("Testing Standalone Components")
    print("=" * 60)

    from .instance_manager import InstanceManager, WrapperProxyConfig
    from .dispatcher import DecryptDispatcher, DecryptTask

    # 创建实例管理器
    proxy_config = WrapperProxyConfig(
        host="127.0.0.1",
        decrypt_port=10020
    )
    instance_manager = InstanceManager(proxy_config)

    # 创建调度器
    dispatcher = DecryptDispatcher(instance_manager)

    print("\n📦 Components initialized")
    print(f"   - Instance manager: {instance_manager}")
    print(f"   - Dispatcher: {dispatcher}")

    # 示例：添加实例
    print("\n➕ Adding test instance...")
    success, msg, instance = await instance_manager.add_instance(
        username="test@example.com",
        password="password123",
        region="us"
    )

    if success:
        print(f"   ✅ {msg}")
        print(f"   - Instance ID: {instance.instance_id}")
        print(f"   - Status: {instance.status.value}")
    else:
        print(f"   ❌ {msg}")

    # 列出实例
    instances = instance_manager.list_instances()
    print(f"\n📊 Total instances: {len(instances)}")
    for inst in instances:
        print(f"   - {inst.username} ({inst.region}) - {inst.status.value}")

    # 清理
    print("\n🧹 Cleaning up...")
    await instance_manager.shutdown_all()
    print("   ✅ All instances shut down")


async def main():
    """示例主入口。"""
    print("""
╔══════════════════════════════════════════════════════════════╗
║  Native Wrapper Manager - Python Implementation             ║
║  Strategy: Hybrid (Rewrite manager, keep wrapper container) ║
╚══════════════════════════════════════════════════════════════╝
    """)

    # 选择示例
    print("\nAvailable examples:")
    print("1. Start gRPC server")
    print("2. Test client connection (requires server running)")
    print("3. Test standalone components")
    print("4. Run all tests")

    choice = input("\nEnter choice (1-4): ").strip()

    if choice == "1":
        await example_server()
    elif choice == "2":
        await example_client()
    elif choice == "3":
        await example_standalone_components()
    elif choice == "4":
        print("\n🧪 Running all tests...\n")
        # 后台启动服务
        from .wrapper_manager_native import NativeWrapperManagerServer
        proxy_config = WrapperProxyConfig()
        server = NativeWrapperManagerServer(proxy_config=proxy_config)

        await server.start()
        await asyncio.sleep(2)  # 等待服务启动

        # 执行测试
        await example_client()
        await example_standalone_components()

        # 停止服务
        await server.stop()
    else:
        print("Invalid choice")


if __name__ == "__main__":
    asyncio.run(main())
