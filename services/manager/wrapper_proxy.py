"""
Wrapper 代理。
将解密请求转发到 Docker wrapper 容器。
"""

import asyncio
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple
import socket
import struct
import aiohttp

from ..logger import LoggerInterface, get_logger
logger = get_logger()
from .retry_utils import retry_async, RetryConfig, RetryStrategy, ErrorHandler


@dataclass
class WrapperProxyConfig:
    """用于 Wrapper 的代理配置。"""
    host: str = "127.0.0.1"
    decrypt_port: int = 10020
    m3u8_port: int = 20020
    account_port: int = 30020
    timeout: int = 30


class WrapperProxy:
    """用于 Docker wrapper 的容器代理。"""

    def __init__(
        self,
        instance_id: str,
        username: str,
        region: str,
        config: WrapperProxyConfig,
    ):
        """初始化 wrapper 代理。"""
        self.instance_id = instance_id
        self.username = username
        self.region = region
        self.config = config

        # 构造基础地址
        self.account_url = f"http://{config.host}:{config.account_port}"

        # HTTP 客户端（仅账号服务使用）
        self._session: Optional[aiohttp.ClientSession] = None

        # 状态跟踪
        self._last_adam_id: str = ""
        self._active: bool = False

    async def start(self):
        """启动代理并创建 HTTP 会话。"""
        if self._session is None:
            timeout = aiohttp.ClientTimeout(total=self.config.timeout)
            self._session = aiohttp.ClientSession(timeout=timeout)
            self._active = True
            logger.debug(f"Wrapper proxy started: {self.instance_id}")

    async def stop(self):
        """停止代理并关闭 HTTP 会话。"""
        if self._session:
            await self._session.close()
            self._session = None
        self._active = False
        logger.debug(f"Wrapper proxy stopped: {self.instance_id}")

    @property
    def is_active(self) -> bool:
        """检查代理是否可用。"""
        return self._active and self._session is not None

    def get_last_adam_id(self) -> str:
        """获取该实例最近处理的 adam_id。"""
        return self._last_adam_id

    def set_last_adam_id(self, adam_id: str):
        """设置该实例最近处理的 adam_id。"""
        self._last_adam_id = adam_id

    async def decrypt(
        self,
        adam_id: str,
        key: str,
        sample: bytes,
        sample_index: int
    ) -> Tuple[bool, bytes, Optional[str]]:
        """通过原始 socket 解密单个样本。"""
        if not self.is_active:
            return False, b"", "Proxy not active"

        try:
            # 连接解密端口
            reader, writer = await asyncio.open_connection(
                self.config.host,
                self.config.decrypt_port
            )

            logger.info(f"[Decrypt] Connecting to {self.config.host}:{self.config.decrypt_port} with adam_id={adam_id}, sample_size={len(sample)}")

            # 发送上下文信息
            # 格式：[adamSize(1 byte)][adam][uri_size(1 byte)][uri]
            adam_id_bytes = adam_id.encode('utf-8')
            key_bytes = key.encode('utf-8')

            # 发送 adam_id 长度（1 byte uint8）
            writer.write(bytes([len(adam_id_bytes)]))
            # 发送 adam_id
            writer.write(adam_id_bytes)
            # 发送 uri/key 长度（1 byte uint8，非 4 byte）
            writer.write(bytes([len(key_bytes)]))
            # 发送 uri/key
            writer.write(key_bytes)
            await writer.drain()

            # 发送样本进行解密
            # 发送样本长度（4 bytes uint32 小端）
            writer.write(struct.pack('<I', len(sample)))
            # 发送样本数据
            writer.write(sample)
            await writer.drain()

            # 读取解密数据（长度与样本一致）
            decrypted_data = await asyncio.wait_for(
                reader.readexactly(len(sample)),
                timeout=30.0
            )

            logger.debug(f"[Decrypt] Success: decrypted {len(decrypted_data)} bytes")
            return True, decrypted_data, None

        except asyncio.TimeoutError:
            logger.error(f"[Decrypt] Timeout for {adam_id}")
            return False, b"", "Socket timeout"
        except ConnectionRefusedError:
            logger.error(f"[Decrypt] Connection refused to {self.config.host}:{self.config.decrypt_port}")
            return False, b"", "Connection refused"
        except Exception as e:
            logger.error(f"[Decrypt] Exception: {e}")
            return False, b"", str(e)
        finally:
            # 关闭连接
            if 'writer' in locals():
                writer.close()
                await writer.wait_closed()

    async def decrypt_all(
        self,
        adam_id: str,
        key: str,
        samples: list,
        progress_callback=None
    ) -> Tuple[bool, list, Optional[str]]:
        """使用单连接批量解密样本（快速模式）。"""
        if not self.is_active:
            return False, [], "Proxy not active"

        total_samples = len(samples)
        # 按输入顺序返回解密结果（非 sample_index 顺序）
        # 调用方负责映射回原位置
        decrypted_samples = []

        try:
            # 连接解密端口（全量样本共用一次连接）
            reader, writer = await asyncio.open_connection(
                self.config.host,
                self.config.decrypt_port
            )

            logger.info(f"[Decrypt] Connected to {self.config.host}:{self.config.decrypt_port} for {total_samples} samples")

            # 发送上下文信息（仅一次）
            adam_id_bytes = adam_id.encode('utf-8')
            key_bytes = key.encode('utf-8')

            # 发送 adam_id 长度（1 byte uint8）
            writer.write(bytes([len(adam_id_bytes)]))
            # 发送 adam_id
            writer.write(adam_id_bytes)
            # 发送 uri/key 长度（1 byte uint8）
            writer.write(bytes([len(key_bytes)]))
            # 发送 uri/key
            writer.write(key_bytes)
            await writer.drain()

            # 在同一连接上循环发送样本
            for i, (sample_data, sample_index) in enumerate(samples):
                # 发送样本长度（4 bytes uint32 小端）
                writer.write(struct.pack('<I', len(sample_data)))
                # 发送样本数据
                writer.write(sample_data)
                await writer.drain()

                # 读取解密数据（长度与样本一致）
                decrypted_data = await asyncio.wait_for(
                    reader.readexactly(len(sample_data)),
                    timeout=30.0
                )

                # 按输入顺序追加，调用方自行映射原位置
                decrypted_samples.append(decrypted_data)

                # 进度回调
                if progress_callback and (i + 1) % 100 == 0:
                    progress_callback(i + 1, total_samples)

            logger.info(f"[Decrypt] All {total_samples} samples decrypted successfully")
            return True, decrypted_samples, None

        except asyncio.TimeoutError:
            logger.error(f"[Decrypt] Timeout for {adam_id}")
            return False, [], "Socket timeout"
        except asyncio.IncompleteReadError as e:
            logger.error(f"[Decrypt] Incomplete read: {e}")
            return False, [], f"Incomplete read: {e}"
        except ConnectionRefusedError:
            logger.error(f"[Decrypt] Connection refused to {self.config.host}:{self.config.decrypt_port}")
            return False, [], "Connection refused"
        except Exception as e:
            logger.error(f"[Decrypt] Exception: {e}")
            return False, [], str(e)
        finally:
            # 关闭连接
            if 'writer' in locals():
                writer.close()
                await writer.wait_closed()

    @retry_async(RetryConfig(
        max_attempts=3,
        initial_delay=0.5,
        strategy=RetryStrategy.EXPONENTIAL,
        retry_on_exceptions=(ConnectionError, socket.timeout)
    ))
    async def get_m3u8(self, adam_id: str) -> Tuple[bool, str, Optional[str]]:
        """通过原始 socket 获取 M3U8 链接。"""
        if not self.is_active:
            return False, "", "Proxy not active"

        # 创建 socket 连接
        try:
            # 连接 m3u8 端口
            reader, writer = await asyncio.open_connection(
                self.config.host,
                self.config.m3u8_port
            )

            logger.debug(f"[M3U8] Connecting to {self.config.host}:{self.config.m3u8_port} for adam_id={adam_id}")

            # 按 C 实现发送 adam_id：[adamSize][adamId]
            adam_id_bytes = adam_id.encode('utf-8')

            # 先发送长度，再发送 adam_id
            writer.write(bytes([len(adam_id_bytes)]))  # uint8
            writer.write(adam_id_bytes)
            await writer.drain()

            # 读取响应：以换行结尾的纯文本 URL
            # C 实现：成功返回 m3u8_url + "\n"，失败仅返回 "\n"
            response = await asyncio.wait_for(reader.readline(), timeout=30.0)
            m3u8_url = response.decode('utf-8', errors='ignore').strip()

            if m3u8_url and m3u8_url.startswith('http'):
                logger.debug(f"[M3U8] Success: got URL length {len(m3u8_url)}")
                return True, m3u8_url, None
            else:
                logger.error(f"[M3U8] Failed: empty or invalid URL response")
                return False, "", "Failed to get M3U8 URL"

        except asyncio.TimeoutError:
            logger.error(f"[M3U8] Timeout for {adam_id}")
            return False, "", "Socket timeout"
        except ConnectionRefusedError:
            logger.error(f"[M3U8] Connection refused to {self.config.host}:{self.config.m3u8_port}")
            return False, "", "Connection refused"
        except Exception as e:
            logger.error(f"[M3U8] Exception: {e}")
            return False, "", str(e)
        finally:
            # 关闭连接
            if 'writer' in locals():
                writer.close()
                await writer.wait_closed()

    async def get_lyrics(
        self,
        adam_id: str,
        storefront: str,
        language: str
    ) -> Tuple[bool, dict, Optional[str]]:
        """获取歌曲歌词。"""
        if not self.is_active:
            return False, {}, "Proxy not active"

        try:
            payload = {
                "adam_id": adam_id,
                "storefront": storefront,
                "language": language,
            }

            async with self._session.post(
                f"{self.account_url}/lyrics",
                json=payload
            ) as response:
                if response.status != 200:
                    error_text = await response.text()
                    return False, {}, f"HTTP {response.status}: {error_text}"

                result = await response.json()

                if result.get("success"):
                    lyrics_data = result.get("lyrics", {})
                    return True, lyrics_data, None
                else:
                    error = result.get("error", "Unknown error")
                    return False, {}, error

        except Exception as e:
            logger.error(f"Get lyrics exception: {e}")
            return False, {}, str(e)

    async def get_account_info(self) -> Optional[dict]:
        """获取 wrapper 账户信息。"""
        if not self.is_active:
            return None

        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json'
            }
            async with self._session.get(
                f"{self.account_url}/account",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10)
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return data
                else:
                    logger.error(f"Account info request failed: {response.status}")
                    return None
        except Exception as e:
            logger.error(f"Failed to get account info for {self.instance_id}: {e}")
            return None

    async def health_check(self) -> bool:
        """检查 wrapper 容器健康状态。"""
        if not self.is_active:
            return False

        try:
            # 使用账号接口做健康检查
            async with self._session.get(
                f"{self.account_url}/account",
                timeout=aiohttp.ClientTimeout(total=5)
            ) as response:
                return response.status == 200
        except Exception:
            return False


def create_instance_id(username: str) -> str:
    """基于用户名生成确定性实例 ID。"""
    namespace = uuid.UUID("77777777-7777-7777-7777-777777777777")
    return str(uuid.uuid5(namespace, username))
