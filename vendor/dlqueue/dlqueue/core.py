"""
Core of the dlqueue library: a small, thread-safe, framework-agnostic
download queue built on top of yt-dlp.

Nothing here knows about Flask (or any web framework). You give it URLs,
it downloads them in the background (no watermark, via yt-dlp) and tracks
each one through: queued -> downloading -> success / failed.

Basic usage
-----------
    from dlqueue import DownloadQueue

    dq = DownloadQueue(download_dir="downloads")
    item = dq.add("https://www.tiktok.com/@user/video/123")
    ...
    for it in dq.list():
        print(it.platform, it.state.value, it.filename)
    dq.shutdown()
"""

from __future__ import annotations

import enum
import os
import queue as _queue
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import yt_dlp

# Default yt-dlp format: prefer a single progressive mp4 (no ffmpeg merge
# needed for the typical short-video case), else best available.
DEFAULT_FORMAT = "best[ext=mp4]/best"

# Domain suffix -> platform label. Subdomains (vm.tiktok.com, www., etc.)
# are matched automatically. Pass your own mapping to support more sites.
DEFAULT_PLATFORMS: Dict[str, str] = {
    "tiktok.com": "tiktok",
    "instagram.com": "instagram",
    "facebook.com": "facebook",
    "fb.watch": "facebook",
}


class UnsupportedURLError(ValueError):
    """Raised by DownloadQueue.add() when a URL is empty or not allowed."""


class ItemState(str, enum.Enum):
    QUEUED = "queued"
    DOWNLOADING = "downloading"
    SUCCESS = "success"
    FAILED = "failed"


@dataclass
class Item:
    """One download in the queue."""

    id: str
    url: str
    platform: str
    state: ItemState = ItemState.QUEUED
    filename: Optional[str] = None
    title: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)

    @property
    def downloadable(self) -> bool:
        return self.state == ItemState.SUCCESS and bool(self.filename)

    def to_dict(self) -> dict:
        """JSON-friendly view (state as a string, plus a downloadable flag)."""
        data = asdict(self)
        data["state"] = self.state.value
        data["downloadable"] = self.downloadable
        return data


def detect_platform(url: str, platforms: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Return the platform label for the URL, or None if not supported."""
    platforms = platforms if platforms is not None else DEFAULT_PLATFORMS
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return None
    if not host:
        return None
    for suffix, name in platforms.items():
        if host == suffix or host.endswith("." + suffix):
            return name
    return None


def download(
    url: str,
    dest_dir: str,
    *,
    out_id: str,
    ydl_format: str = DEFAULT_FORMAT,
    extra_opts: Optional[dict] = None,
) -> Tuple[str, str]:
    """
    Download the URL into dest_dir as <out_id>.<ext> using yt-dlp.

    Returns (filename, title). Raises whatever yt-dlp raises on failure.
    Stateless and queue-independent, so it is usable on its own.
    """
    os.makedirs(dest_dir, exist_ok=True)
    opts = {
        "outtmpl": os.path.join(dest_dir, f"{out_id}.%(ext)s"),
        "format": ydl_format,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
    }
    if extra_opts:
        opts.update(extra_opts)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filename = ydl.prepare_filename(info)
    title = (info or {}).get("title") or os.path.basename(filename)
    return filename, title


class DownloadQueue:
    """
    A background download queue.

    Parameters
    ----------
    download_dir : where files are written (created if missing).
    workers      : number of concurrent download threads (default 1 = serial).
    ydl_format   : yt-dlp format string.
    ydl_opts     : extra yt-dlp options merged in (can override defaults,
                   e.g. {"cookiefile": "cookies.txt"} for private Instagram).
    platforms    : domain-suffix -> label map (defaults to TikTok+Instagram+Facebook).
    on_change    : optional callback(item) invoked on every state change
                   (useful for websockets, notifications, persistence...).
    delete_file_on_remove : delete the downloaded file when remove() is called.
    start        : start worker threads immediately (default True).
    """

    def __init__(
        self,
        download_dir: str = "downloads",
        *,
        workers: int = 1,
        ydl_format: str = DEFAULT_FORMAT,
        ydl_opts: Optional[dict] = None,
        platforms: Optional[Dict[str, str]] = None,
        on_change: Optional[Callable[[Item], None]] = None,
        delete_file_on_remove: bool = True,
        start: bool = True,
    ) -> None:
        self.download_dir = os.path.abspath(download_dir)
        os.makedirs(self.download_dir, exist_ok=True)
        self.ydl_format = ydl_format
        self.ydl_opts = ydl_opts
        self.platforms = platforms if platforms is not None else DEFAULT_PLATFORMS
        self.on_change = on_change
        self.delete_file_on_remove = delete_file_on_remove

        self._items: Dict[str, Item] = {}
        self._lock = threading.Lock()
        self._q: "_queue.Queue[Optional[str]]" = _queue.Queue()
        self._threads: List[threading.Thread] = []
        self._stop = threading.Event()

        if start:
            self.start(workers)

    def start(self, workers: int = 1) -> None:
        """Start worker threads (no-op if already started)."""
        if self._threads:
            return
        self._stop.clear()
        for i in range(max(1, workers)):
            thread = threading.Thread(
                target=self._worker,
                name=f"dlqueue-worker-{i}",
                daemon=True,
            )
            thread.start()
            self._threads.append(thread)

    def add(self, url: str) -> Item:
        """Validate, enqueue and return a new Item. Raises UnsupportedURLError."""
        url = (url or "").strip()
        if not url:
            raise UnsupportedURLError("URL is empty.")
        platform = detect_platform(url, self.platforms)
        if platform is None:
            raise UnsupportedURLError(f"Unsupported URL: {url}")

        item = Item(id=uuid.uuid4().hex, url=url, platform=platform)
        with self._lock:
            self._items[item.id] = item
        self._emit(item)
        self._q.put(item.id)
        return item

    def get(self, item_id: str) -> Optional[Item]:
        with self._lock:
            return self._items.get(item_id)

    def list(self) -> List[Item]:
        """Return all items, newest first."""
        with self._lock:
            items = list(self._items.values())
        items.sort(key=lambda item: item.created_at, reverse=True)
        return items

    def remove(self, item_id: str) -> bool:
        """Drop an item and optionally delete its file. Return whether it existed."""
        with self._lock:
            item = self._items.pop(item_id, None)
        if (
            item is not None
            and self.delete_file_on_remove
            and item.filename
            and os.path.exists(item.filename)
        ):
            try:
                os.remove(item.filename)
            except OSError:
                pass
        return item is not None

    def retry(self, item_id: str) -> Optional[Item]:
        """Re-queue the URL of an existing item as a fresh download."""
        item = self.get(item_id)
        if item is None:
            return None
        return self.add(item.url)

    def shutdown(self, wait: bool = False, timeout: float = 5.0) -> None:
        """Signal workers to stop. With wait=True, join them."""
        self._stop.set()
        for _ in self._threads:
            self._q.put(None)
        if wait:
            for thread in self._threads:
                thread.join(timeout=timeout)
        self._threads.clear()

    def _emit(self, item: Item) -> None:
        if self.on_change is not None:
            try:
                self.on_change(item)
            except Exception:
                pass

    def _update(self, item_id: str, **changes) -> Optional[Item]:
        with self._lock:
            item = self._items.get(item_id)
            if item is None:
                return None
            for key, value in changes.items():
                setattr(item, key, value)
        self._emit(item)
        return item

    def _worker(self) -> None:
        while not self._stop.is_set():
            item_id = self._q.get()
            try:
                if item_id is None:
                    return
                self._process(item_id)
            finally:
                self._q.task_done()

    def _process(self, item_id: str) -> None:
        item = self._update(item_id, state=ItemState.DOWNLOADING)
        if item is None:
            return
        try:
            filename, title = download(
                item.url,
                self.download_dir,
                out_id=item.id,
                ydl_format=self.ydl_format,
                extra_opts=self.ydl_opts,
            )
            self._update(
                item_id,
                state=ItemState.SUCCESS,
                filename=filename,
                title=title,
            )
        except Exception as exc:
            message = str(exc).strip()
            last_line = message.splitlines()[-1] if message else "Download failed"
            self._update(item_id, state=ItemState.FAILED, error=last_line)
