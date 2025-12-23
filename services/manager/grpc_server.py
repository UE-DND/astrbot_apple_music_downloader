"""
原生 wrapper 管理器 gRPC 服务端。
以纯 Python 实现 WrapperManagerService。
"""

import asyncio
import grpc
from google.protobuf import empty_pb2
from typing import Optional

from ..logger import LoggerInterface, get_logger
logger = get_logger()

# 导入生成的 protobuf 代码
# 兼容相对/绝对导入（支持独立运行）
try:
    from ...core.grpc import manager_pb2, manager_pb2_grpc
except ImportError:
    from core.grpc import manager_pb2, manager_pb2_grpc

from .instance_manager import InstanceManager, WrapperInstance
from .dispatcher import DecryptDispatcher, DecryptTask
from .login_handler import LoginHandler, LoginState
from .wrapper_proxy import WrapperProxyConfig
from .health_monitor import HealthMonitor, HealthStatus, RecoveryAction


class NativeWrapperManagerServicer(manager_pb2_grpc.WrapperManagerServiceServicer):
    """服务实现（gRPC）。"""

    def __init__(
        self,
        instance_manager: InstanceManager,
        dispatcher: DecryptDispatcher,
        login_handler: LoginHandler
    ):
        """初始化 gRPC 服务实现。"""
        self.instance_manager = instance_manager
        self.dispatcher = dispatcher
        self.login_handler = login_handler
        self._ready = False

    def set_ready(self, ready: bool):
        """设置服务就绪状态。"""
        self._ready = ready

    async def Status(
        self,
        request: empty_pb2.Empty,
        context: grpc.aio.ServicerContext
    ) -> manager_pb2.StatusReply:
        """获取服务状态。"""
        try:
            regions = self.instance_manager.get_regions()
            client_count = self.instance_manager.get_client_count()

            return manager_pb2.StatusReply(
                header=manager_pb2.ReplyHeader(
                    code=0,
                    msg="SUCCESS"
                ),
                data=manager_pb2.StatusData(
                    status=client_count > 0,
                    regions=regions,
                    client_count=client_count,
                    ready=self._ready
                )
            )
        except Exception as e:
            logger.error(f"Status error: {e}")
            return manager_pb2.StatusReply(
                header=manager_pb2.ReplyHeader(
                    code=-1,
                    msg=str(e)
                )
            )

    async def Login(
        self,
        request_iterator,
        context: grpc.aio.ServicerContext
    ):
        """处理登录双向流。"""
        try:
            async for request in request_iterator:
                data = request.data

                if data.two_step_code:
                    # 处理 2FA
                    success, msg = await self.login_handler.provide_2fa_code(
                        username=data.username,
                        code=data.two_step_code
                    )

                    if success:
                        yield manager_pb2.LoginReply(
                            header=manager_pb2.ReplyHeader(code=0, msg=msg),
                            data=manager_pb2.LoginData(username=data.username)
                        )
                    else:
                        yield manager_pb2.LoginReply(
                            header=manager_pb2.ReplyHeader(code=-1, msg=msg),
                            data=manager_pb2.LoginData(username=data.username)
                        )

                else:
                    # 开始登录
                    success, msg, session_id = await self.login_handler.start_login(
                        username=data.username,
                        password=data.password
                    )

                    if success or "双因素" in msg or "2FA" in msg:
                        # 登录开始或需要 2FA
                        code = 1 if "2FA" in msg or "双因素" in msg else 0
                        yield manager_pb2.LoginReply(
                            header=manager_pb2.ReplyHeader(code=code, msg=msg),
                            data=manager_pb2.LoginData(username=data.username)
                        )
                    else:
                        # 登录失败
                        yield manager_pb2.LoginReply(
                            header=manager_pb2.ReplyHeader(code=-1, msg=msg),
                            data=manager_pb2.LoginData(username=data.username)
                        )

        except Exception as e:
            logger.error(f"Login error: {e}")
            context.abort(grpc.StatusCode.INTERNAL, str(e))

    async def Logout(
        self,
        request: manager_pb2.LogoutRequest,
        context: grpc.aio.ServicerContext
    ) -> manager_pb2.LogoutReply:
        """处理登出请求。"""
        try:
            username = request.data.username

            # 查找实例
            instance = self.instance_manager.get_instance_by_username(username)
            if not instance:
                return manager_pb2.LogoutReply(
                    header=manager_pb2.ReplyHeader(
                        code=-1,
                        msg="账户不存在"
                    ),
                    data=manager_pb2.LogoutData(username=username)
                )

            # 移除实例
            success, msg = await self.instance_manager.remove_instance(instance.instance_id)

            return manager_pb2.LogoutReply(
                header=manager_pb2.ReplyHeader(
                    code=0 if success else -1,
                    msg=msg
                ),
                data=manager_pb2.LogoutData(username=username)
            )

        except Exception as e:
            logger.error(f"Logout error: {e}")
            return manager_pb2.LogoutReply(
                header=manager_pb2.ReplyHeader(code=-1, msg=str(e)),
                data=manager_pb2.LogoutData(username=request.data.username)
            )

    async def Decrypt(
        self,
        request_iterator,
        context: grpc.aio.ServicerContext
    ):
        """处理解密双向流。"""
        try:
            async for request in request_iterator:
                data = request.data

                # 处理 KEEPALIVE（连接保活）
                if data.adam_id == "KEEPALIVE":
                    yield manager_pb2.DecryptReply(
                        header=manager_pb2.ReplyHeader(
                            code=0,
                            msg="SUCCESS"
                        ),
                        data=manager_pb2.DecryptData(
                            adam_id="KEEPALIVE",
                            key=data.key,
                            sample_index=data.sample_index,
                            sample=b""
                        )
                    )
                    continue

                # 创建解密任务
                task = DecryptTask(
                    adam_id=data.adam_id,
                    key=data.key,
                    sample=data.sample,
                    sample_index=data.sample_index
                )

                # 分发任务
                try:
                    logger.debug(f"[Decrypt] Dispatching task for {task.adam_id}[{task.sample_index}]")
                    result = await self.dispatcher.dispatch(task)

                    # 返回响应
                    yield manager_pb2.DecryptReply(
                        header=manager_pb2.ReplyHeader(
                            code=0 if result.success else -1,
                            msg=result.error or "SUCCESS"
                        ),
                        data=manager_pb2.DecryptData(
                            adam_id=data.adam_id,
                            key=data.key,
                            sample_index=data.sample_index,
                            sample=result.data
                        )
                    )
                except Exception as task_error:
                    logger.error(f"Decrypt task error for {data.adam_id}: {task_error}")
                    # 返回错误但保持流
                    yield manager_pb2.DecryptReply(
                        header=manager_pb2.ReplyHeader(
                            code=-1,
                            msg=str(task_error)
                        ),
                        data=manager_pb2.DecryptData(
                            adam_id=data.adam_id,
                            key=data.key,
                            sample_index=data.sample_index,
                            sample=b""
                        )
                    )

        except Exception as e:
            logger.error(f"Decrypt stream error: {e}", exc_info=True)
            # 不主动终止，交由流自然关闭

    async def M3U8(
        self,
        request: manager_pb2.M3U8Request,
        context: grpc.aio.ServicerContext
    ) -> manager_pb2.M3U8Reply:
        """获取歌曲 M3U8 链接。"""
        try:
            adam_id = request.data.adam_id

            # 选择实例
            instances = self.instance_manager.list_instances()
            active_instances = [inst for inst in instances if inst.is_active()]

            if not active_instances:
                return manager_pb2.M3U8Reply(
                    header=manager_pb2.ReplyHeader(
                        code=-1,
                        msg="没有可用的实例"
                    )
                )

            instance = active_instances[0]  # 使用首个可用实例

            # 获取 M3U8
            if instance.proxy:
                success, m3u8, error = await instance.proxy.get_m3u8(adam_id)

                return manager_pb2.M3U8Reply(
                    header=manager_pb2.ReplyHeader(
                        code=0 if success else -1,
                        msg=error or "SUCCESS"
                    ),
                    data=manager_pb2.M3U8DataResponse(
                        adam_id=adam_id,
                        m3u8=m3u8
                    )
                )

            return manager_pb2.M3U8Reply(
                header=manager_pb2.ReplyHeader(code=-1, msg="实例无效")
            )

        except Exception as e:
            logger.error(f"M3U8 error: {e}")
            return manager_pb2.M3U8Reply(
                header=manager_pb2.ReplyHeader(code=-1, msg=str(e))
            )

    async def Lyrics(
        self,
        request: manager_pb2.LyricsRequest,
        context: grpc.aio.ServicerContext
    ) -> manager_pb2.LyricsReply:
        """获取歌曲歌词。"""
        try:
            import aiohttp
            import re
            from functools import lru_cache

            data = request.data

            # 选择实例（优先匹配地区）
            instances = self.instance_manager.list_instances()
            active_instances = [inst for inst in instances if inst.is_active()]

            if not active_instances:
                return manager_pb2.LyricsReply(
                    header=manager_pb2.ReplyHeader(code=-1, msg="没有可用的实例")
                )

            # 优先寻找地区匹配的实例
            instance = None
            for inst in active_instances:
                if inst.region.lower() == data.region.lower():
                    instance = inst
                    break

            # 回退到首个可用实例
            if not instance:
                instance = active_instances[0]

            if not instance.proxy:
                return manager_pb2.LyricsReply(
                    header=manager_pb2.ReplyHeader(code=-1, msg="实例无效")
                )

            # 获取账号信息（dev_token 与 music_token）
            account_info = await instance.proxy.get_account_info()
            if not account_info:
                return manager_pb2.LyricsReply(
                    header=manager_pb2.ReplyHeader(code=-1, msg="无法获取账户信息")
                )

            dev_token = account_info.get("dev_token")
            music_token = account_info.get("music_token")

            if not dev_token or not music_token:
                return manager_pb2.LyricsReply(
                    header=manager_pb2.ReplyHeader(code=-1, msg=f"Token 缺失: dev_token={bool(dev_token)}, music_token={bool(music_token)}")
                )

            # 调用 Apple Music API 获取歌词
            url = f"https://amp-api.music.apple.com/v1/catalog/{instance.region}/songs/{data.adam_id}/syllable-lyrics"
            params = {
                "l[lyrics]": data.language,
                "extend": "ttmlLocalizations",
                "l[script]": "en-Latn"
            }
            headers = {
                "User-Agent": "Music/5.7 Android/10 model/Pixel6GR1YH build/1234 (dt:66)",
                "Authorization": f"Bearer {dev_token}",
                "media-user-token": music_token,
                "Origin": "https://music.apple.com"
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(url, params=params, headers=headers, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                    if resp.status != 200:
                        error_text = await resp.text()
                        return manager_pb2.LyricsReply(
                            header=manager_pb2.ReplyHeader(code=-1, msg=f"歌词 API 失败: HTTP {resp.status}")
                        )

                    resp_json = await resp.json()

                    # 检查 API 错误
                    if "errors" in resp_json:
                        error_msg = str(resp_json["errors"])
                        return manager_pb2.LyricsReply(
                            header=manager_pb2.ReplyHeader(code=-1, msg=f"API 错误: {error_msg}")
                        )

                    # 提取 TTML 歌词
                    if "data" in resp_json and len(resp_json["data"]) > 0:
                        ttml = resp_json["data"][0].get("attributes", {}).get("ttmlLocalizations")
                        if ttml:
                            return manager_pb2.LyricsReply(
                                header=manager_pb2.ReplyHeader(code=0, msg="SUCCESS"),
                                data=manager_pb2.LyricsDataResponse(
                                    adam_id=data.adam_id,
                                    lyrics=ttml
                                )
                            )

                    return manager_pb2.LyricsReply(
                        header=manager_pb2.ReplyHeader(code=-1, msg="未找到歌词数据")
                    )

        except Exception as e:
            logger.error(f"Lyrics error: {e}", exc_info=True)
            return manager_pb2.LyricsReply(
                header=manager_pb2.ReplyHeader(code=-1, msg=str(e))
            )

    # 其他方法（License、WebPlayback）后续按需实现
    # 暂时返回未实现

    async def License(self, request, context):
        """未实现。"""
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "License not implemented")

    async def WebPlayback(self, request, context):
        """未实现。"""
        context.abort(grpc.StatusCode.UNIMPLEMENTED, "WebPlayback not implemented")


class NativeWrapperManagerServer:
    """原生 wrapper 管理器 gRPC 服务器。"""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18923,
        proxy_config: Optional[WrapperProxyConfig] = None,
        enable_health_monitor: bool = True,
        health_check_interval: int = 30,
    ):
        """初始化 gRPC 服务器。"""
        self.host = host
        self.port = port

        # 初始化组件
        self.instance_manager = InstanceManager(proxy_config)
        self.dispatcher = DecryptDispatcher(self.instance_manager)
        self.login_handler = LoginHandler(self.instance_manager)

        # 健康监控
        self.health_monitor = HealthMonitor(
            instance_manager=self.instance_manager,
            check_interval=health_check_interval,
            failure_threshold=3,
            recovery_enabled=enable_health_monitor,
            max_recovery_attempts=5,
        ) if enable_health_monitor else None

        # 注册健康监控回调
        if self.health_monitor:
            self.health_monitor.set_recovery_start_callback(self._on_recovery_start)
            self.health_monitor.set_recovery_complete_callback(self._on_recovery_complete)

        # gRPC 服务
        self._server: Optional[grpc.aio.Server] = None
        self._servicer: Optional[NativeWrapperManagerServicer] = None

    async def start(self):
        """启动 gRPC 服务器。"""
        logger.info(f"Starting native wrapper-manager server on {self.host}:{self.port}")

        # 创建服务实现
        self._servicer = NativeWrapperManagerServicer(
            instance_manager=self.instance_manager,
            dispatcher=self.dispatcher,
            login_handler=self.login_handler
        )

        # 创建 gRPC 服务端
        self._server = grpc.aio.server()

        # 注册服务
        manager_pb2_grpc.add_WrapperManagerServiceServicer_to_server(
            self._servicer,
            self._server
        )

        # 绑定地址
        listen_addr = f"{self.host}:{self.port}"
        self._server.add_insecure_port(listen_addr)

        # 启动服务
        await self._server.start()

        # 标记就绪
        if self._servicer:
            self._servicer.set_ready(True)

        # 启动健康监控
        if self.health_monitor:
            await self.health_monitor.start()
            logger.info("Health monitor started")

        logger.info(f"Native wrapper-manager server started on {listen_addr}")

    async def stop(self):
        """停止 gRPC 服务器。"""
        logger.info("Stopping native wrapper-manager server...")

        # 停止健康监控
        if self.health_monitor:
            await self.health_monitor.stop()
            logger.info("Health monitor stopped")

        if self._server:
            await self._server.stop(grace=5.0)
            logger.info("gRPC server stopped")

        # 关闭全部实例
        await self.instance_manager.shutdown_all()

        logger.info("Native wrapper-manager server stopped")

    async def wait_for_termination(self):
        """等待服务终止。"""
        if self._server:
            await self._server.wait_for_termination()

    async def _on_recovery_start(self, action: RecoveryAction):
        """恢复开始回调。"""
        logger.info(
            f"Recovery started for instance {action.instance_id}: "
            f"action={action.action_type}, reason={action.reason}"
        )

    async def _on_recovery_complete(self, instance_id: str, success: bool, message: str):
        """恢复完成回调。"""
        if success:
            logger.info(f"Recovery completed for instance {instance_id}: {message}")
        else:
            logger.error(f"Recovery failed for instance {instance_id}: {message}")

    def get_health_metrics(self) -> dict:
        """获取所有实例健康指标。"""
        if not self.health_monitor:
            return {"error": "Health monitor not enabled"}

        return {
            "enabled": True,
            "check_interval": self.health_monitor.check_interval,
            "failure_threshold": self.health_monitor.failure_threshold,
            "instances": self.health_monitor.get_all_metrics(),
        }
