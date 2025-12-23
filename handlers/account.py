"""
账户管理命令处理器

支持 Apple Music 账户登录/登出，包括双因素身份验证 (2FA)。
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Optional, Dict

from astrbot.api.event import AstrMessageEvent, MessageChain
import astrbot.api.message_components as Comp
from astrbot.api import logger

if TYPE_CHECKING:
    from ..main import AppleMusicDownloader


class AccountHandler:
    """
    账户管理处理器

    提供 Apple Music 账户的登录、登出和状态查询功能。
    支持双因素身份验证 (2FA) 的交互式登录流程。
    """

    def __init__(self, plugin: "AppleMusicDownloader"):
        self._plugin = plugin
        # 存储等待 2FA 验证的会话 {user_id: {"username": ..., "password": ..., "event": ...}}
        self._pending_2fa: Dict[str, dict] = {}

    async def handle_login(self, event: AstrMessageEvent, username: str = "", password: str = ""):
        """
        处理登录命令

        用法:
          /am_login <用户名> <密码>  - 使用用户名密码登录
          /am_login                  - 交互式登录

        Args:
            event: 消息事件
            username: Apple ID 用户名
            password: Apple ID 密码
        """
        user_id = event.get_sender_id()

        if not self._plugin.wrapper_service or not self._plugin.wrapper_service.is_connected:
            yield event.plain_result("× 服务未连接，请先使用 /am_start 启动服务")
            return

        manager = self._plugin.wrapper_service.manager
        if not manager:
            yield event.plain_result("× 无法获取服务管理器")
            return

        if user_id in self._pending_2fa:
            if username and not password:
                code = username
                if code.isdigit() and len(code) == 6:
                    yield event.plain_result(f"... 正在验证 2FA 验证码: {code}")
                    async for result in self.handle_2fa_code(event, code):
                        yield result
                    return

        if not username:
            yield event.plain_result(
                "🔐 Apple Music 账户登录\n"
                "─" * 25 + "\n"
                "请输入您的 Apple ID 用户名和密码：\n"
                "/am_login <用户名> <密码>\n\n"
                "示例：/am_login example@apple.com mypassword\n\n"
                "⚠️ 注意：\n"
                "• 需要有效的 Apple Music 订阅\n"
                "• 可能需要进行双因素身份验证\n"
                "• 建议使用应用专用密码"
            )
            return

        if not password:
            yield event.plain_result("× 请提供密码：/am_login <用户名> <密码>")
            return

        yield event.plain_result(f"... 正在登录账户: {self._mask_email(username)}")

        try:
            async def on_2fa(uname: str, pwd: str) -> str:
                """2FA 验证码回调"""
                self._pending_2fa[user_id] = {
                    "username": uname,
                    "password": pwd,
                    "event": event,
                }

                logger.info(f"2FA required for user {user_id}, username: {self._mask_email(uname)}")

                wait_event = asyncio.Event()
                self._pending_2fa[user_id]["wait_event"] = wait_event
                self._pending_2fa[user_id]["code"] = None

                await self._send_2fa_prompt(event, uname)

                try:
                    await asyncio.wait_for(wait_event.wait(), timeout=300)
                    code = self._pending_2fa[user_id].get("code")
                    if code:
                        return code
                    raise Exception("未收到验证码")
                except asyncio.TimeoutError:
                    raise Exception("验证码输入超时")
                finally:
                    if user_id in self._pending_2fa:
                        del self._pending_2fa[user_id]

            await manager.login(username, password, on_2fa)

            yield event.plain_result(
                f"√ 登录成功！\n"
                f"账户: {self._mask_email(username)}\n\n"
                "现在可以使用 /am 命令下载音乐了"
            )

        except Exception as e:
            error_msg = str(e)
            if "already login" in error_msg.lower():
                yield event.plain_result(f"× 该账户已登录: {self._mask_email(username)}")
            elif "login failed" in error_msg.lower():
                yield event.plain_result(
                    f"× 登录失败: {self._mask_email(username)}\n"
                    "请检查用户名和密码是否正确\n\n"
                    "提示：如果启用了双因素认证，建议使用应用专用密码"
                )
            elif "no active subscription" in error_msg.lower():
                yield event.plain_result(
                    f"× 登录失败: 该账户没有有效的 Apple Music 订阅\n"
                    f"账户: {self._mask_email(username)}"
                )
            else:
                yield event.plain_result(f"× 登录失败: {error_msg}")

    async def _send_2fa_prompt(self, event: AstrMessageEvent, username: str):
        """发送 2FA 验证提示"""
        msg = (
            "🔐 需要双因素身份验证\n"
            "─" * 25 + "\n"
            f"账户: {self._mask_email(username)}\n\n"
            "请输入您收到的 6 位验证码：\n"
            "/am_2fa <验证码>\n\n"
            "示例：/am_2fa 123456\n\n"
            "⏰ 验证码 5 分钟内有效"
        )
        try:
            message_chain = MessageChain(chain=[Comp.Plain(msg)])
            await self._plugin.context.send_message(
                event.unified_msg_origin,
                message_chain
            )
        except Exception as e:
            logger.error(f"Failed to send 2FA prompt: {e}")

    async def handle_2fa_code(self, event: AstrMessageEvent, code: str = ""):
        """
        处理 2FA 验证码输入

        用法: /am_2fa <验证码>
        """
        user_id = event.get_sender_id()

        if not code:
            yield event.plain_result("× 请输入 6 位验证码：/am_2fa <验证码>")
            return

        if not code.isdigit() or len(code) != 6:
            yield event.plain_result("× 验证码格式错误，请输入 6 位数字")
            return

        if user_id not in self._pending_2fa:
            yield event.plain_result("× 没有待验证的登录会话\n请先使用 /am_login 开始登录")
            return

        session = self._pending_2fa[user_id]
        session["code"] = code

        wait_event = session.get("wait_event")
        if wait_event:
            wait_event.set()
            yield event.plain_result(f"... 正在验证: {code}")
        else:
            yield event.plain_result("× 验证会话已过期，请重新登录")

    async def handle_logout(self, event: AstrMessageEvent, username: str = ""):
        """
        处理登出命令

        用法: /am_logout <用户名>
        """
        if not self._plugin.wrapper_service or not self._plugin.wrapper_service.is_connected:
            yield event.plain_result("× 服务未连接")
            return

        manager = self._plugin.wrapper_service.manager
        if not manager:
            yield event.plain_result("× 无法获取服务管理器")
            return

        if not username:
            yield event.plain_result(
                "请指定要登出的账户：\n"
                "/am_logout <用户名>\n\n"
                "使用 /am_accounts 查看已登录的账户"
            )
            return

        yield event.plain_result(f"... 正在登出账户: {self._mask_email(username)}")

        try:
            await manager.logout(username)
            yield event.plain_result(f"√ 已登出账户: {self._mask_email(username)}")
        except Exception as e:
            error_msg = str(e)
            if "no such account" in error_msg.lower():
                yield event.plain_result(f"× 账户未登录: {self._mask_email(username)}")
            else:
                yield event.plain_result(f"× 登出失败: {error_msg}")

    async def handle_accounts(self, event: AstrMessageEvent):
        """
        查看已登录的账户

        用法: /am_accounts
        """
        if not self._plugin.wrapper_service:
            yield event.plain_result("× 服务未初始化")
            return

        status = await self._plugin.wrapper_service.get_status()

        lines = [
            "🔐 Apple Music 账户状态",
            "─" * 25,
            "",
            f"服务状态: {'√ 已连接' if status.connected else '× 未连接'}",
            f"服务就绪: {'√ 是' if status.ready else '× 否'}",
            f"已登录账户数: {status.client_count}",
        ]

        if status.regions:
            lines.append(f"可用地区: {', '.join(status.regions)}")

        if not status.ready and status.client_count == 0:
            lines.extend([
                "",
                "⚠️ 尚未登录任何账户",
                "使用 /am_login 登录 Apple Music 账户",
            ])

        yield event.plain_result("\n".join(lines))

    def _mask_email(self, email: str) -> str:
        """隐藏邮箱中间部分"""
        if "@" not in email:
            if len(email) <= 4:
                return email
            return email[:2] + "***" + email[-2:]

        local, domain = email.split("@", 1)
        if len(local) <= 2:
            masked_local = local[0] + "*"
        else:
            masked_local = local[0] + "*" * (len(local) - 2) + local[-1]

        return f"{masked_local}@{domain}"
