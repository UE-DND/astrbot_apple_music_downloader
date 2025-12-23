"""
gRPC Wrapper 管理器客户端。
适配 AstrBot 插件。
"""

import asyncio
import json
import logging
from typing import Awaitable, Callable, Optional

import grpc
from async_lru import alru_cache
from grpc import ssl_channel_credentials
from grpc.aio import insecure_channel, Channel, secure_channel
from grpc.experimental import ChannelOptions
from tenacity import (
    retry_if_exception_type,
    retry,
    wait_random_exponential,
    stop_after_attempt,
    retry_if_not_exception_message,
    before_sleep_log,
)

from .manager_pb2 import (
    StatusReply,
    StatusData,
    LoginRequest,
    LoginData,
    LoginReply,
    LogoutRequest,
    LogoutData,
    LogoutReply,
    DecryptRequest,
    DecryptData,
    DecryptReply,
    M3U8Request,
    M3U8DataRequest,
    M3U8Reply,
    LyricsRequest,
    LyricsDataRequest,
    LyricsReply,
    LicenseRequest,
    LicenseDataRequest,
    LicenseReply,
    WebPlaybackRequest,
    WebPlaybackDataRequest,
    WebPlaybackReply,
)
from .manager_pb2_grpc import WrapperManagerServiceStub, google_dot_protobuf_dot_empty__pb2


# 模块日志
logger = logging.getLogger(__name__)


class WrapperManagerException(Exception):
    """操作异常（WrapperManager）。"""
    def __init__(self, msg: str):
        self.msg = msg
        super().__init__(msg)


class WrapperManager:
    """用于 Wrapper Manager 的 gRPC 客户端。"""

    _channel: Channel
    _stub: WrapperManagerServiceStub
    _decrypt_queue: asyncio.Queue
    _login_lock: asyncio.Lock
    _background_tasks: set
    _initialized: bool

    def __init__(self):
        self._login_lock = asyncio.Lock()
        self._decrypt_queue = asyncio.Queue()
        self._background_tasks = set()
        self._initialized = False

    async def init(self, url: str, secure: bool = False) -> "WrapperManager":
        """初始化 gRPC 连接。"""
        service_config_json = json.dumps(
            {
                "methodConfig": [
                    {
                        "name": [{}],
                        "retryPolicy": {
                            "maxAttempts": 5,
                            "initialBackoff": "0.1s",
                            "maxBackoff": "1s",
                            "backoffMultiplier": 2,
                            "retryableStatusCodes": ["UNAVAILABLE", "INTERNAL"],
                        },
                    }
                ]
            }
        )
        options = (
            (ChannelOptions.SingleThreadedUnaryStream, 1),
            ("grpc.service_config", service_config_json),
        )

        if secure:
            self._channel = secure_channel(url, credentials=ssl_channel_credentials(), options=options)
        else:
            self._channel = insecure_channel(url, options=options)

        self._stub = WrapperManagerServiceStub(self._channel)
        self._initialized = True
        return self

    async def close(self):
        """关闭 gRPC 通道并取消后台任务。"""
        for task in self._background_tasks:
            task.cancel()
        self._background_tasks.clear()

        if self._initialized and hasattr(self, '_channel'):
            await self._channel.close()
            self._initialized = False

    def _safely_create_task(self, coro):
        """创建任务并纳入清理跟踪。"""
        loop = asyncio.get_event_loop()
        task = loop.create_task(coro)
        self._background_tasks.add(task)

        def done_callback(t):
            self._background_tasks.discard(t)
            if t.exception():
                try:
                    raise t.exception()
                except Exception as e:
                    logger.exception(f"Background task error: {e}")

        task.add_done_callback(done_callback)

    @alru_cache
    async def status(self) -> StatusData:
        """获取 wrapper 服务状态。"""
        resp: StatusReply = await self._stub.Status(google_dot_protobuf_dot_empty__pb2.Empty())
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data

    async def login(
        self,
        username: str,
        password: str,
        on_2fa: Callable[[str, str], Awaitable[str]]
    ):
        """登录 Apple Music 账户。"""
        await self._login_lock.acquire()

        login_queue: asyncio.Queue = asyncio.Queue()

        async def request_stream():
            while True:
                item = await login_queue.get()
                if item is None:
                    break
                yield item

        stream = self._stub.Login(request_stream())

        await login_queue.put(
            LoginRequest(data=LoginData(username=username, password=password))
        )

        try:
            async for reply in stream:
                reply: LoginReply
                match reply.header.code:
                    case -1:
                        await login_queue.put(None)
                        raise WrapperManagerException(reply.header.msg)
                    case 0:
                        await login_queue.put(None)
                        return
                    case 2:
                        two_step_code = await on_2fa(username, password)
                        await login_queue.put(
                            LoginRequest(
                                data=LoginData(
                                    username=username,
                                    password=password,
                                    two_step_code=two_step_code,
                                )
                            )
                        )
        finally:
            self._login_lock.release()

    async def decrypt(self, adam_id: str, key: str, sample: bytes, sample_index: int):
        """将样本加入解密队列。"""
        await self._decrypt_queue.put(
            DecryptRequest(
                data=DecryptData(
                    adam_id=adam_id,
                    key=key,
                    sample_index=sample_index,
                    sample=sample,
                )
            )
        )

    async def _decrypt_request_generator(self):
        """从队列生成解密请求。"""
        while True:
            yield await self._decrypt_queue.get()

    async def decrypt_init(
        self,
        on_success: Callable[[str, str, bytes, int], Awaitable[None]],
        on_failure: Callable[[str, str, bytes, int], Awaitable[None]],
    ):
        """初始化解密流。"""
        async def handle_stream():
            try:
                stream = self._stub.Decrypt(self._decrypt_request_generator())
                self._safely_create_task(self._decrypt_keepalive())

                async for reply in stream:
                    reply: DecryptReply
                    if reply.data.adam_id == "KEEPALIVE":
                        continue
                    match reply.header.code:
                        case -1:
                            self._safely_create_task(
                                on_failure(
                                    reply.data.adam_id,
                                    reply.data.key,
                                    reply.data.sample,
                                    reply.data.sample_index,
                                )
                            )
                        case 0:
                            self._safely_create_task(
                                on_success(
                                    reply.data.adam_id,
                                    reply.data.key,
                                    reply.data.sample,
                                    reply.data.sample_index,
                                )
                            )
            except grpc.aio.AioRpcError as e:
                logger.error(f"[Decrypt] Stream error: {e}")
            except Exception as e:
                logger.error(f"[Decrypt] Unexpected error: {e}", exc_info=True)

        self._safely_create_task(handle_stream())
        logger.info("[Decrypt] Stream initialized")

    async def _decrypt_keepalive(self):
        """定时发送保活消息。"""
        while True:
            await self._decrypt_queue.put(
                DecryptRequest(data=DecryptData(adam_id="KEEPALIVE"))
            )
            await asyncio.sleep(15)

    @retry(
        retry=(
            retry_if_exception_type(WrapperManagerException)
            & retry_if_not_exception_message("no available instance")
        ),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def m3u8(self, adam_id: str) -> str:
        """获取歌曲 M3U8 内容。"""
        resp: M3U8Reply = await self._stub.M3U8(
            M3U8Request(data=M3U8DataRequest(adam_id=adam_id))
        )
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data.m3u8

    @retry(
        retry=(
            retry_if_exception_type(WrapperManagerException)
            & retry_if_not_exception_message("no such account")
        ),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def logout(self, username: str):
        """登出 Apple Music 账户。"""
        resp: LogoutReply = await self._stub.Logout(
            LogoutRequest(data=LogoutData(username=username))
        )
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)

    @retry(
        retry=(
            retry_if_exception_type(WrapperManagerException)
            & retry_if_not_exception_message("no available instance")
        ),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def lyrics(self, adam_id: str, language: str, region: str) -> str:
        """获取歌曲歌词。"""
        resp: LyricsReply = await self._stub.Lyrics(
            LyricsRequest(
                data=LyricsDataRequest(adam_id=adam_id, language=language, region=region)
            )
        )
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data.lyrics

    @retry(
        retry=(
            retry_if_exception_type(WrapperManagerException)
            & retry_if_not_exception_message("no available instance")
        ),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def web_playback(self, adam_id: str) -> str:
        """获取 WebPlayback M3U8（AAC-Legacy）。"""
        resp: WebPlaybackReply = await self._stub.WebPlayback(
            WebPlaybackRequest(data=WebPlaybackDataRequest(adam_id=adam_id))
        )
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data.m3u8

    @retry(
        retry=(
            retry_if_exception_type(WrapperManagerException)
            & retry_if_not_exception_message("no available instance")
        ),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def license(self, adam_id: str, challenge: str, kid: str) -> str:
        """获取 Widevine License。"""
        resp: LicenseReply = await self._stub.License(
            LicenseRequest(
                data=LicenseDataRequest(adam_id=adam_id, challenge=challenge, uri=kid)
            )
        )
        if resp.header.code != 0:
            raise WrapperManagerException(resp.header.msg)
        return resp.data.license
