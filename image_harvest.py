"""
Media harvesting helpers for the competitors "page media extractor" feature.

Pure, network-free parsing lives here (``extract_media_candidates`` /
``upgrade_url``) alongside an SSRF-guarded fetch/stream used by the download
proxy. Keeping these in a dedicated module keeps ``competitors.py`` focused and
makes the extraction/upgrade logic straightforward to unit-test.
"""
from __future__ import annotations

import ipaddress
import re
import socket
from urllib.parse import (
    parse_qsl,
    unquote,
    urlencode,
    urljoin,
    urlparse,
    urlunparse,
)

from bs4 import BeautifulSoup

# ── Limits ────────────────────────────────────────────────────────────────────
MAX_HTML_BYTES = 6 * 1024 * 1024        # cap on pasted source size
MAX_IMAGES = 200                        # cap on returned candidates (per kind)
MAX_IMAGE_BYTES = 25 * 1024 * 1024      # cap per proxied/zipped image
MAX_VIDEO_BYTES = 128 * 1024 * 1024     # cap per proxied video
MAX_ZIP_IMAGES = 80                     # cap for "download all"
FETCH_TIMEOUT = 15                      # seconds per media request
MAX_REDIRECTS = 4
MIN_DATA_URI_BYTES = 512                # skip tiny inline placeholders

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# Query params that shrink/re-encode an image — dropped when the URL is unsigned
# so we fall back to the original master asset.
_SIZE_QUERY_PARAMS = {
    "width", "height", "w", "h", "maxwidth", "maxheight", "max-w", "max-h",
    "size", "resize", "fit", "crop", "dpr", "imwidth", "imageheight",
    "imagewidth", "sw", "sh", "wid", "hei", "q", "quality",
}
# If any param name looks like a signature we leave the query untouched, because
# stripping it would break the signed URL.
_SIGNATURE_HINTS = ("sig", "signature", "token", "hash", "expires", "policy", "_a")

_CSS_URL_RE = re.compile(r"url\(\s*(['\"]?)([^'\")]+)\1\s*\)", re.IGNORECASE)
_VIDEO_EXT_RE = re.compile(r"\.(mp4|webm|ogv|ogg|mov|m4v)(?:$|[?#])", re.IGNORECASE)
# Streaming manifests can't be grabbed as a single downloadable file.
_VIDEO_MANIFEST_RE = re.compile(r"\.(m3u8|mpd)(?:$|[?#])", re.IGNORECASE)


class ImageFetchError(Exception):
    """Raised when a remote media file cannot be safely fetched."""


# ── srcset parsing ─────────────────────────────────────────────────────────────


def parse_srcset(value: str) -> list[tuple[str, float]]:
    """Return ``[(url, weight), ...]`` for a srcset/imagesrcset value."""
    candidates: list[tuple[str, float]] = []
    for part in (value or "").split(","):
        part = part.strip()
        if not part:
            continue
        bits = part.split()
        url = bits[0].strip()
        if not url:
            continue
        weight = 0.0
        if len(bits) > 1:
            match = re.match(r"([\d.]+)([wx])", bits[1])
            if match:
                num = float(match.group(1))
                # Density descriptors ("2x") are scaled so they still outrank
                # smaller ones; width and density never mix in one srcset.
                weight = num if match.group(2) == "w" else num * 1000
        candidates.append((url, weight))
    return candidates


def best_from_srcset(value: str) -> str | None:
    """Pick the highest-resolution URL from a srcset value."""
    candidates = parse_srcset(value)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[1])[0]


# ── URL normalisation / quality upgrade ────────────────────────────────────────


def _resolve(
    raw: str, base_url: str | None, data_prefixes: tuple[str, ...] = ("data:image/",)
) -> str | None:
    raw = (raw or "").strip()
    if not raw or raw.startswith(("javascript:", "about:", "#", "blob:")):
        return None
    if raw.startswith("data:"):
        if not raw.startswith(data_prefixes):
            return None
        if len(raw) < MIN_DATA_URI_BYTES:
            return None  # tiny inline lazy-load placeholder, not a real asset
        return raw
    if raw.startswith("//"):
        raw = "https:" + raw
    if base_url:
        raw = urljoin(base_url, raw)
    parsed = urlparse(raw)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return raw


def upgrade_url(url: str) -> str:
    """Best-effort rewrite of *url* to the highest-quality variant."""
    if url.startswith("data:"):
        return url
    try:
        parsed = urlparse(url)
    except ValueError:
        return url
    if parsed.scheme not in ("http", "https"):
        return url

    host = parsed.netloc.lower()
    path = parsed.path
    query_pairs = parse_qsl(parsed.query, keep_blank_values=True)

    is_shopify = (
        "cdn.shopify.com" in host or "/cdn/shop/" in path or "/s/files/" in path
    )
    if is_shopify:
        # Drop dimension suffixes ("_1024x1024", "_1024x", "_x512", "@2x") so we
        # request the original uploaded master, which always exists on Shopify.
        path = re.sub(r"_(\d+x\d+|\d+x|x\d+)(@\d+x)?(\.\w+)$", r"\3", path)
        path = re.sub(
            r"_(pico|icon|thumb|small|compact|medium|large|grande|master|original)"
            r"(\.\w+)$",
            r"\2",
            path,
        )

    has_signature = any(
        any(hint in key.lower() for hint in _SIGNATURE_HINTS)
        for key, _ in query_pairs
    )
    if not has_signature:
        query_pairs = [
            (key, value)
            for key, value in query_pairs
            if key.lower() not in _SIZE_QUERY_PARAMS
        ]

    return urlunparse(parsed._replace(path=path, query=urlencode(query_pairs)))


def _urls_from_css(css: str) -> list[str]:
    return [match.group(2).strip() for match in _CSS_URL_RE.finditer(css or "")]


def _filename_for(url: str, default_ext: str = ".jpg") -> str:
    fallback = "video" if default_ext == ".mp4" else "image"
    if url.startswith("data:"):
        match = re.match(r"data:(image|video)/([\w.+-]+)", url)
        main = match.group(1) if match else "image"
        ext = (match.group(2) if match else "png").split("+")[0]
        return f"{'video' if main == 'video' else 'image'}.{ext}"
    path = urlparse(url).path
    name = unquote(path.rsplit("/", 1)[-1]) or fallback
    name = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or fallback
    if "." not in name:
        name += default_ext
    return name[:120]


# ── Extraction ─────────────────────────────────────────────────────────────────


def _detect_base(soup: BeautifulSoup, base_url: str | None) -> str | None:
    if base_url:
        return base_url
    base_tag = soup.find("base", href=True)
    if base_tag and base_tag["href"].strip():
        return base_tag["href"].strip()
    return None


def _extract_images(soup: BeautifulSoup, base_url: str | None) -> list[dict]:
    found: list[str] = []
    seen_found: set[str] = set()

    def add(raw: str | None) -> None:
        resolved = _resolve(raw or "", base_url, ("data:image/",))
        if not resolved or resolved in seen_found:
            return
        seen_found.add(resolved)
        found.append(resolved)

    # <img>, <source>, <input type=image> — plain + common lazy-load attributes.
    for node in soup.find_all(["img", "source", "input"]):
        # Skip <source> that explicitly declares a non-image (e.g. video) type.
        stype = (node.get("type") or "").lower()
        if node.name == "source" and stype and not stype.startswith("image/"):
            continue
        for attr in (
            "src", "data-src", "data-lazy-src", "data-original",
            "data-image", "data-fallback-src", "data-hi-res-src", "data-large",
        ):
            if node.get(attr):
                add(node.get(attr))
        for attr in ("srcset", "data-srcset", "data-lazy-srcset", "imagesrcset"):
            if node.get(attr):
                add(best_from_srcset(node.get(attr)))

    # <link rel="preload" as="image"> and rel="image_src"
    for link in soup.find_all("link", href=True):
        rel = " ".join(link.get("rel") or []).lower()
        if "preload" in rel and link.get("as") == "image":
            add(link.get("href"))
            if link.get("imagesrcset"):
                add(best_from_srcset(link.get("imagesrcset")))
        elif "image_src" in rel:
            add(link.get("href"))

    # Social preview images.
    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in (
            "og:image", "og:image:secure_url", "og:image:url",
            "twitter:image", "twitter:image:src",
        ):
            add(meta.get("content"))

    # Inline background-image on style attributes and in <style> blocks.
    for el in soup.find_all(style=True):
        for url in _urls_from_css(el.get("style")):
            add(url)
    for style in soup.find_all("style"):
        if style.string:
            for url in _urls_from_css(style.string):
                add(url)

    # Common data-* background hooks used by lazy-load libraries.
    for attr in ("data-bg", "data-background", "data-background-image", "data-bgset"):
        for el in soup.find_all(attrs={attr: True}):
            value = el.get(attr)
            if attr == "data-bgset":
                add(best_from_srcset(value))
            else:
                add(value)

    results: list[dict] = []
    seen_best: set[str] = set()
    for src in found:
        best = upgrade_url(src)
        if best in seen_best:
            continue
        seen_best.add(best)
        results.append(
            {
                "url": best,
                "source": src if src != best else None,
                "filename": _filename_for(best, ".jpg"),
                "is_data": best.startswith("data:"),
                "type": "image",
                "poster": None,
            }
        )
        if len(results) >= MAX_IMAGES:
            break
    return results


def _extract_videos(soup: BeautifulSoup, base_url: str | None) -> list[dict]:
    found: list[tuple[str, str | None]] = []
    seen_found: set[str] = set()

    def add(raw: str | None, poster: str | None = None) -> None:
        resolved = _resolve(raw or "", base_url, ("data:video/",))
        if not resolved or resolved in seen_found:
            return
        if _VIDEO_MANIFEST_RE.search(resolved):
            return  # HLS/DASH manifests aren't single downloadable files
        seen_found.add(resolved)
        found.append((resolved, poster))

    for video in soup.find_all("video"):
        raw_poster = video.get("poster")
        poster = (
            _resolve(raw_poster, base_url, ("data:image/",)) if raw_poster else None
        )
        for attr in ("src", "data-src", "data-video-src", "data-lazy-src"):
            if video.get(attr):
                add(video.get(attr), poster)
        for source in video.find_all("source"):
            for attr in ("src", "data-src"):
                if source.get(attr):
                    add(source.get(attr), poster)

    # Standalone <source type="video/*"> (outside a parsed <video>).
    for source in soup.find_all("source"):
        stype = (source.get("type") or "").lower()
        if stype.startswith("video/"):
            for attr in ("src", "data-src"):
                if source.get(attr):
                    add(source.get(attr))

    # Social / player meta.
    for meta in soup.find_all("meta"):
        key = (meta.get("property") or meta.get("name") or "").lower()
        if key in (
            "og:video", "og:video:url", "og:video:secure_url",
            "twitter:player:stream",
        ):
            add(meta.get("content"))

    # Direct links to video files.
    for anchor in soup.find_all("a", href=True):
        if _VIDEO_EXT_RE.search(anchor["href"]):
            add(anchor["href"])

    results: list[dict] = []
    for url, poster in found[:MAX_IMAGES]:
        results.append(
            {
                "url": url,          # videos are left as-found (avoid breaking CDNs)
                "source": None,
                "filename": _filename_for(url, ".mp4"),
                "is_data": url.startswith("data:"),
                "type": "video",
                "poster": poster,
            }
        )
    return results


def extract_image_candidates(html: str, base_url: str | None = None) -> list[dict]:
    """Parse *html* and return de-duplicated image candidates."""
    soup = BeautifulSoup(html or "", "html.parser")
    return _extract_images(soup, _detect_base(soup, base_url))


def extract_video_candidates(html: str, base_url: str | None = None) -> list[dict]:
    """Parse *html* and return de-duplicated video candidates."""
    soup = BeautifulSoup(html or "", "html.parser")
    return _extract_videos(soup, _detect_base(soup, base_url))


def extract_media_candidates(html: str, base_url: str | None = None) -> dict:
    """
    Parse *html* once and return ``{"images": [...], "videos": [...]}``.

    Each item is::

        {"url": <best-quality url>, "source": <fallback url or None>,
         "filename": <str>, "is_data": <bool>, "type": "image"|"video",
         "poster": <video poster url or None>}
    """
    soup = BeautifulSoup(html or "", "html.parser")
    base = _detect_base(soup, base_url)
    return {"images": _extract_images(soup, base), "videos": _extract_videos(soup, base)}


# ── SSRF-guarded fetch / stream ─────────────────────────────────────────────────


def is_public_url(url: str) -> bool:
    """True only when *url* is http(s) and resolves entirely to public IPs."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    host = parsed.hostname
    if not host:
        return False
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror:
        return False
    if not infos:
        return False
    for info in infos:
        try:
            addr = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False
        if (
            addr.is_private
            or addr.is_loopback
            or addr.is_link_local
            or addr.is_reserved
            or addr.is_multicast
            or addr.is_unspecified
        ):
            return False
    return True


def _content_type_ok(content_type: str) -> bool:
    if not content_type:
        return True
    return (
        content_type.startswith("image/")
        or content_type.startswith("video/")
        or content_type == "application/octet-stream"
    )


def _open_validated(session, url: str):
    """Open *url*, following redirects manually and re-validating every hop
    against the SSRF guard. Returns a live streamed ``requests`` response at the
    final ``200`` hop (caller must close it)."""
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        if not is_public_url(current):
            raise ImageFetchError("This URL is not allowed.")
        response = session.get(
            current,
            stream=True,
            timeout=FETCH_TIMEOUT,
            allow_redirects=False,
            headers={"User-Agent": _USER_AGENT, "Accept": "image/*,video/*,*/*;q=0.8"},
        )
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise ImageFetchError("Broken redirect.")
            current = urljoin(current, location)
            continue
        if response.status_code != 200:
            status = response.status_code
            response.close()
            raise ImageFetchError(f"Remote server returned {status}.")
        return response
    raise ImageFetchError("Too many redirects.")


def fetch_image(url: str, session=None, max_bytes: int = MAX_IMAGE_BYTES) -> tuple[bytes, str]:
    """Fetch a remote media file fully into memory. Returns ``(data, content_type)``."""
    import requests

    sess = session or requests.Session()
    response = _open_validated(sess, url)
    content_type = (
        (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    )
    try:
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(64 * 1024):
            if not chunk:
                continue
            total += len(chunk)
            if total > max_bytes:
                raise ImageFetchError("File is too large to download.")
            chunks.append(chunk)
    finally:
        response.close()

    if not _content_type_ok(content_type):
        raise ImageFetchError("The URL did not return a media file.")
    return b"".join(chunks), content_type or "application/octet-stream"


def stream_media(url: str, session=None, max_bytes: int = MAX_VIDEO_BYTES):
    """
    Open a remote media file for streaming. Returns ``(chunk_iterator, content_type)``
    so large videos are proxied without buffering the whole file in memory.
    """
    import requests

    sess = session or requests.Session()
    response = _open_validated(sess, url)
    content_type = (
        (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
    )
    if not _content_type_ok(content_type):
        response.close()
        raise ImageFetchError("The URL did not return a media file.")

    def generator():
        total = 0
        try:
            for chunk in response.iter_content(256 * 1024):
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    break
                yield chunk
        finally:
            response.close()

    return generator(), content_type or "application/octet-stream"
