"""
Apple Music Downloader gRPC 模块。
"""

from .manager import WrapperManager, WrapperManagerException

__all__ = [
    "WrapperManager",
    "WrapperManagerException",
]
