"""
登录处理器。
支持 2FA 的登录会话管理。
"""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Dict
import uuid

from ..logger import LoggerInterface, get_logger
logger = get_logger()

from .instance_manager import InstanceManager


class LoginState(Enum):
    """登录状态。"""
    PENDING_PASSWORD = "pending_password"
    PENDING_2FA = "pending_2fa"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class LoginSession:
    """登录会话数据。"""
    session_id: str
    username: str
    password: str
    state: LoginState = LoginState.PENDING_PASSWORD
    created_at: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None
    two_factor_code: Optional[str] = None


class LoginHandler:
    """账户登录流程处理器。"""

    def __init__(self, instance_manager: InstanceManager):
        """初始化登录处理器。"""
        self.instance_manager = instance_manager
        self._sessions: Dict[str, LoginSession] = {}
        self._username_to_session: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def start_login(
        self,
        username: str,
        password: str
    ) -> tuple[bool, str, Optional[str]]:
        """启动登录流程。"""
        async with self._lock:
            # 检查是否已登录
            existing_instance = self.instance_manager.get_instance_by_username(username)
            if existing_instance:
                return False, f"账户 {username} 已登录", None

            # 检查是否已有登录会话
            if username in self._username_to_session:
                session_id = self._username_to_session[username]
                session = self._sessions.get(session_id)
                if session and session.state == LoginState.PENDING_2FA:
                    return False, "等待输入双因素验证码", session_id

            # 创建新的登录会话
            session_id = str(uuid.uuid4())
            session = LoginSession(
                session_id=session_id,
                username=username,
                password=password,
                state=LoginState.PENDING_PASSWORD
            )

            self._sessions[session_id] = session
            self._username_to_session[username] = session_id

            logger.info(f"Started login session for {username}")

            # 发起登录（异步）
            asyncio.create_task(self._perform_login(session_id))

            return True, "登录请求已提交", session_id

    async def provide_2fa_code(
        self,
        username: str,
        code: str
    ) -> tuple[bool, str]:
        """提交 2FA 验证码。"""
        async with self._lock:
            # 查找会话
            session_id = self._username_to_session.get(username)
            if not session_id:
                return False, "未找到登录会话"

            session = self._sessions.get(session_id)
            if not session:
                return False, "登录会话无效"

            if session.state != LoginState.PENDING_2FA:
                return False, f"登录状态错误: {session.state.value}"

            # 写入 2FA 验证码
            session.two_factor_code = code
            logger.info(f"Received 2FA code for {username}")

            # 继续登录流程
            asyncio.create_task(self._continue_login_with_2fa(session_id))

            return True, "双因素验证码已提交"

    async def get_session_status(
        self,
        session_id: str
    ) -> Optional[LoginSession]:
        """获取登录会话状态。"""
        return self._sessions.get(session_id)

    async def _perform_login(self, session_id: str):
        """执行实际登录流程。"""
        session = self._sessions.get(session_id)
        if not session:
            return

        try:
            # TODO：实现真实的 wrapper 登录流程
            # 暂时使用模拟流程

            # 步骤 1：尝试添加实例（触发 wrapper 登录）
            success, msg, instance = await self.instance_manager.add_instance(
                username=session.username,
                password=session.password,
                region="us"  # 默认地区
            )

            if success:
                # 登录成功
                session.state = LoginState.COMPLETED
                logger.info(f"Login completed for {session.username}")

                # 延迟清理会话
                await asyncio.sleep(60)
                async with self._lock:
                    if session_id in self._sessions:
                        del self._sessions[session_id]
                    if session.username in self._username_to_session:
                        del self._username_to_session[session.username]

            else:
                # 检查是否需要 2FA
                if "2FA" in msg or "双因素" in msg or "验证码" in msg:
                    session.state = LoginState.PENDING_2FA
                    logger.info(f"2FA required for {session.username}")
                else:
                    # 登录失败
                    session.state = LoginState.FAILED
                    session.error = msg
                    logger.error(f"Login failed for {session.username}: {msg}")

        except Exception as e:
            logger.error(f"Login exception for {session.username}: {e}")
            session.state = LoginState.FAILED
            session.error = str(e)

    async def _continue_login_with_2fa(self, session_id: str):
        """使用 2FA 继续登录。"""
        session = self._sessions.get(session_id)
        if not session or not session.two_factor_code:
            return

        try:
            # TODO：实现真实的 2FA 验证
            # 暂时模拟成功

            # 使用 2FA 验证码添加实例
            success, msg, instance = await self.instance_manager.add_instance(
                username=session.username,
                password=session.password,
                region="us"
            )

            if success:
                session.state = LoginState.COMPLETED
                logger.info(f"Login with 2FA completed for {session.username}")

                # 清理会话
                await asyncio.sleep(60)
                async with self._lock:
                    if session_id in self._sessions:
                        del self._sessions[session_id]
                    if session.username in self._username_to_session:
                        del self._username_to_session[session.username]

            else:
                session.state = LoginState.FAILED
                session.error = msg
                logger.error(f"2FA verification failed for {session.username}: {msg}")

        except Exception as e:
            logger.error(f"2FA verification exception: {e}")
            session.state = LoginState.FAILED
            session.error = str(e)

    async def cleanup_expired_sessions(self, max_age_seconds: int = 600):
        """清理过期登录会话。"""
        now = datetime.now()
        to_remove = []

        async with self._lock:
            for session_id, session in self._sessions.items():
                age = (now - session.created_at).total_seconds()
                if age > max_age_seconds:
                    to_remove.append((session_id, session.username))

            for session_id, username in to_remove:
                logger.info(f"Cleaning up expired session: {username}")
                del self._sessions[session_id]
                if username in self._username_to_session:
                    del self._username_to_session[username]
