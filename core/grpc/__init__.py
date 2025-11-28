"""
gRPC Module for Apple Music Downloader
"""

from .manager import WrapperManager, WrapperManagerException

__all__ = [
    "WrapperManager",
    "WrapperManagerException",
]
