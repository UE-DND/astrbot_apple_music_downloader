"""
重试工具。
提供重试装饰器与异常处理工具。
"""

import asyncio
import functools
from typing import Optional, Callable, TypeVar, Any
from dataclasses import dataclass
from enum import Enum

from ..logger import LoggerInterface, get_logger
logger = get_logger()


class RetryStrategy(Enum):
    """重试策略类型。"""
    EXPONENTIAL = "exponential"  # 指数退避
    LINEAR = "linear"  # 线性退避
    FIXED = "fixed"  # 固定间隔


@dataclass
class RetryConfig:
    """重试配置。"""
    max_attempts: int = 3
    initial_delay: float = 1.0
    max_delay: float = 60.0
    multiplier: float = 2.0
    strategy: RetryStrategy = RetryStrategy.EXPONENTIAL
    retry_on_exceptions: tuple = (Exception,)
    retry_on_result: Optional[Callable[[Any], bool]] = None


T = TypeVar('T')


def retry_async(config: Optional[RetryConfig] = None):
    """异步函数重试装饰器。"""
    if config is None:
        config = RetryConfig()

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            last_exception = None
            delay = config.initial_delay

            for attempt in range(1, config.max_attempts + 1):
                try:
                    result = await func(*args, **kwargs)

                    # 判断是否需要重试
                    if config.retry_on_result and config.retry_on_result(result):
                        if attempt < config.max_attempts:
                            logger.warning(
                                f"{func.__name__} returned retryable result, "
                                f"attempt {attempt}/{config.max_attempts}, "
                                f"retrying in {delay}s..."
                            )
                            await asyncio.sleep(delay)
                            delay = _calculate_next_delay(delay, config)
                            continue

                    return result

                except config.retry_on_exceptions as e:
                    last_exception = e

                    if attempt < config.max_attempts:
                        logger.warning(
                            f"{func.__name__} failed: {e}, "
                            f"attempt {attempt}/{config.max_attempts}, "
                            f"retrying in {delay}s..."
                        )
                        await asyncio.sleep(delay)
                        delay = _calculate_next_delay(delay, config)
                    else:
                        logger.error(
                            f"{func.__name__} failed after {config.max_attempts} attempts: {e}"
                        )

            # 已耗尽重试次数
            if last_exception:
                raise last_exception

        return wrapper

    return decorator


def _calculate_next_delay(current_delay: float, config: RetryConfig) -> float:
    """根据策略计算下一次重试延迟。"""
    if config.strategy == RetryStrategy.EXPONENTIAL:
        next_delay = current_delay * config.multiplier
    elif config.strategy == RetryStrategy.LINEAR:
        next_delay = current_delay + config.initial_delay
    else:  # 固定间隔
        next_delay = config.initial_delay

    return min(next_delay, config.max_delay)


class CircuitBreaker:
    """断路器模式实现。"""

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        expected_exception: type = Exception
    ):
        """初始化断路器。"""
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception

        self._failure_count = 0
        self._last_failure_time: Optional[float] = None
        self._state = "closed"  # closed/open/half_open 状态

    def __call__(self, func: Callable[..., Any]) -> Callable[..., Any]:
        """为函数添加断路器保护。"""
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            if self._state == "open":
                if self._should_attempt_reset():
                    self._state = "half_open"
                    logger.info(f"Circuit breaker half-open for {func.__name__}")
                else:
                    raise Exception(f"Circuit breaker open for {func.__name__}")

            try:
                result = await func(*args, **kwargs)
                self._on_success()
                return result

            except self.expected_exception as e:
                self._on_failure()
                raise

        return wrapper

    def _should_attempt_reset(self) -> bool:
        """判断是否应尝试恢复。"""
        if self._last_failure_time is None:
            return True

        import time
        elapsed = time.time() - self._last_failure_time
        return elapsed >= self.recovery_timeout

    def _on_success(self):
        """处理成功调用。"""
        if self._state == "half_open":
            self._state = "closed"
            logger.info("Circuit breaker closed (recovered)")

        self._failure_count = 0

    def _on_failure(self):
        """处理失败调用。"""
        import time
        self._failure_count += 1
        self._last_failure_time = time.time()

        if self._failure_count >= self.failure_threshold:
            self._state = "open"
            logger.error(
                f"Circuit breaker opened "
                f"(failures: {self._failure_count}/{self.failure_threshold})"
            )

    def reset(self):
        """手动重置断路器。"""
        self._failure_count = 0
        self._last_failure_time = None
        self._state = "closed"
        logger.info("Circuit breaker manually reset")


class ErrorHandler:
    """集中式错误处理工具。"""

    @staticmethod
    async def handle_with_fallback(
        primary: Callable[..., Any],
        fallback: Callable[..., Any],
        *args,
        **kwargs
    ) -> tuple[bool, Any, Optional[str]]:
        """尝试主函数，失败后回退。"""
        try:
            result = await primary(*args, **kwargs)
            return True, result, None

        except Exception as e:
            logger.warning(f"Primary function failed: {e}, trying fallback...")

            try:
                result = await fallback(*args, **kwargs)
                return True, result, None

            except Exception as fallback_error:
                logger.error(f"Fallback also failed: {fallback_error}")
                return False, None, f"Primary: {e}, Fallback: {fallback_error}"

    @staticmethod
    async def handle_with_timeout(
        func: Callable[..., Any],
        timeout: float,
        *args,
        **kwargs
    ) -> tuple[bool, Any, Optional[str]]:
        """带超时执行函数。"""
        try:
            result = await asyncio.wait_for(
                func(*args, **kwargs),
                timeout=timeout
            )
            return True, result, None

        except asyncio.TimeoutError:
            error = f"Operation timed out after {timeout}s"
            logger.error(error)
            return False, None, error

        except Exception as e:
            error = f"Operation failed: {e}"
            logger.error(error)
            return False, None, error

    @staticmethod
    def safe_cast(value: Any, cast_type: type, default: Any = None) -> Any:
        """安全类型转换，失败回退默认值。"""
        try:
            return cast_type(value)
        except (ValueError, TypeError):
            return default

    @staticmethod
    def validate_required_fields(data: dict, required_fields: list) -> tuple[bool, Optional[str]]:
        """校验必填字段是否存在。"""
        missing = [field for field in required_fields if field not in data]

        if missing:
            return False, f"Missing required fields: {', '.join(missing)}"

        return True, None
