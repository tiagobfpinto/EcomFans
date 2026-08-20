"""
dlqueue - a small, reusable, framework-agnostic download queue.

Add TikTok / Instagram / Facebook (or any yt-dlp supported) URLs, and they are
downloaded in the background (no watermark) while you track each one
through: queued -> downloading -> success / failed.

See `dlqueue.core` for the implementation.
"""

from .core import (
    DEFAULT_FORMAT,
    DEFAULT_PLATFORMS,
    DownloadQueue,
    Item,
    ItemState,
    UnsupportedURLError,
    detect_platform,
    download,
)

__all__ = [
    "DownloadQueue",
    "Item",
    "ItemState",
    "UnsupportedURLError",
    "detect_platform",
    "download",
    "DEFAULT_FORMAT",
    "DEFAULT_PLATFORMS",
]

__version__ = "0.1.0"
