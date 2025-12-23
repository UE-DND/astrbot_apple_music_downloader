"""
日志抽象层。
"""

import logging
from abc import ABC, abstractmethod
from typing import Any


class LoggerInterface(ABC):
    """日志接口抽象类"""

    @abstractmethod
    def debug(self, msg: str, *args, **kwargs) -> None:
        """调试级别日志"""
        pass

    @abstractmethod
    def info(self, msg: str, *args, **kwargs) -> None:
        """信息级别日志"""
        pass

    @abstractmethod
    def warning(self, msg: str, *args, **kwargs) -> None:
        """警告级别日志"""
        pass

    @abstractmethod
    def error(self, msg: str, *args, **kwargs) -> None:
        """错误级别日志"""
        pass

    @abstractmethod
    def exception(self, msg: str, *args, **kwargs) -> None:
        """异常级别日志(包含堆栈追踪)"""
        pass


class PythonLogger(LoggerInterface):
    """基于 Python logging 的日志实现"""

    def __init__(self, name: str = "apple_music_downloader"):
        self._logger = logging.getLogger(name)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)


class AstrBotLoggerAdapter(LoggerInterface):
    """用于 AstrBot 的日志适配器。"""

    def __init__(self, astrbot_logger: Any):
        """初始化 AstrBot 日志适配器。"""
        self._logger = astrbot_logger

    def debug(self, msg: str, *args, **kwargs) -> None:
        self._logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self._logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self._logger.warning(msg, *args, **kwargs)

    def error(self, msg: str, *args, **kwargs) -> None:
        self._logger.error(msg, *args, **kwargs)

    def exception(self, msg: str, *args, **kwargs) -> None:
        self._logger.exception(msg, *args, **kwargs)


def get_logger(name: str = "apple_music_downloader") -> LoggerInterface:
    """获取 logger 实例（自动检测环境）。"""
    try:
        from astrbot.api import logger as astrbot_logger
        return AstrBotLoggerAdapter(astrbot_logger)
    except ImportError:
        # 独立运行模式，使用 Python 日志
        return PythonLogger(name)


# 全局 logger 实例(向后兼容)
logger = get_logger()
